from pathlib import Path
from typing import List, Optional, Tuple

import pygame

from .midi_parser import MidiParser
from .models import PracticeOptions, SongRecord
from .storage import SettingsStore, SongLibrary
from .ui import (
    BackgroundParticles,
    FontBook,
    Theme,
    draw_card,
    draw_glow_rect,
    draw_text,
    draw_wrapped_text,
    mix_colour,
)
from .utils import clamp, format_time
from .visualiser import PracticeView


class JamSpotApp:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("JamSpot")

        self.screen = pygame.display.set_mode((1280, 800), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()

        self.project_root = Path(__file__).resolve().parent.parent

        self.theme = Theme()
        self.fonts = FontBook(self.project_root)

        self.library = SongLibrary(self.project_root / "data")
        self.settings = SettingsStore(self.project_root / "data")

        self.running = True
        self.page = "home"

        self.actions: List[Tuple[pygame.Rect, str]] = []

        self.current_song: Optional[SongRecord] = None
        self.current_parser: Optional[MidiParser] = None
        self.practice: Optional[PracticeView] = None

        self.options = PracticeOptions(
            speed=float(self.settings.get("default_speed", 1.0)),
            count_in_bars=int(self.settings.get("count_in_bars", 1)),
            metronome=bool(self.settings.get("metronome", False)),
        )

        self.search_text = ""
        self.search_active = False

        self.library_scroll = 0
        self.part_scroll = 0

        self.pending_delete: Optional[str] = None

        self.toast_text = ""
        self.toast_time = 0.0

        self.particles = BackgroundParticles(22)

        pygame.key.set_repeat(350, 40)

    def run(self) -> None:
        try:
            while self.running:
                dt = self.clock.tick(60) / 1000.0

                self.handle_events()
                self.update(dt)
                self.draw()

                pygame.display.flip()

        finally:
            if self.practice:
                self.practice.close()

            pygame.quit()

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                continue

            if event.type == pygame.VIDEORESIZE:
                width = max(980, event.w)
                height = max(680, event.h)

                self.screen = pygame.display.set_mode(
                    (width, height),
                    pygame.RESIZABLE,
                )

                if self.practice:
                    self.practice.set_screen(self.screen)

                continue

            if self.practice:
                result = self.practice.handle_event(event)

                if result == "back":
                    self._finish_practice()

                continue

            if event.type == pygame.KEYDOWN:
                if self.pending_delete and event.key == pygame.K_ESCAPE:
                    self.pending_delete = None
                    continue

                if self.page == "library" and self.search_active:
                    self._handle_search_key(event)
                    continue

                if event.key == pygame.K_ESCAPE:
                    self.page = "home"

            if event.type == pygame.MOUSEWHEEL:
                if self.page == "library":
                    self.library_scroll = max(
                        0,
                        self.library_scroll - event.y * 44,
                    )

                elif self.page == "setup":
                    self.part_scroll = max(
                        0,
                        self.part_scroll - event.y * 42,
                    )

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for rect, action in reversed(self.actions):
                    if rect.collidepoint(event.pos):
                        self._perform_action(action)
                        break

    def _handle_search_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self.search_active = False

        elif event.key == pygame.K_RETURN:
            self.search_active = False

        elif event.key == pygame.K_BACKSPACE:
            self.search_text = self.search_text[:-1]
            self.library_scroll = 0

        elif (
            event.unicode
            and event.unicode.isprintable()
            and len(self.search_text) < 50
        ):
            self.search_text += event.unicode
            self.library_scroll = 0

    def _perform_action(self, action: str) -> None:
        if self.pending_delete and not action.startswith("delete_"):
            return

        if action.startswith("nav:"):
            destination = action.split(":", 1)[1]

            if destination != "setup" or self.current_parser:
                self.page = destination
                self.search_active = False

        elif action == "import":
            self._import_song_dialog()

        elif action == "search":
            self.search_active = True

        elif action.startswith("open:"):
            self._open_song(action.split(":", 1)[1])

        elif action.startswith("favourite:"):
            self.library.toggle_favourite(action.split(":", 1)[1])

        elif action.startswith("ask_delete:"):
            self.pending_delete = action.split(":", 1)[1]

        elif action == "delete_cancel":
            self.pending_delete = None

        elif action == "delete_confirm" and self.pending_delete:
            song_id = self.pending_delete

            self.library.delete(song_id)

            if self.current_song and self.current_song.song_id == song_id:
                self.current_song = None
                self.current_parser = None
                self.page = "library"

            self.pending_delete = None
            self._toast("Song removed from your library")

        elif action.startswith("part:"):
            self._select_part(int(action.split(":", 1)[1]))

        elif action.startswith("mode:"):
            self.options.mode = action.split(":", 1)[1]

        elif action.startswith("speed:"):
            self.options.speed = float(action.split(":", 1)[1])

        elif action.startswith("count:"):
            self.options.count_in_bars = int(action.split(":", 1)[1])

        elif action == "target_sound":
            self.options.include_target = not self.options.include_target

        elif action == "metronome":
            self.options.metronome = not self.options.metronome

        elif action == "start_practice":
            self._start_practice()

        elif action.startswith("setting_speed:"):
            value = float(action.split(":", 1)[1])

            self.settings.set("default_speed", value)
            self.options.speed = value

        elif action.startswith("setting_count:"):
            value = int(action.split(":", 1)[1])

            self.settings.set("count_in_bars", value)
            self.options.count_in_bars = value

        elif action == "setting_metronome":
            value = not bool(self.settings.get("metronome", False))

            self.settings.set("metronome", value)
            self.options.metronome = value

        elif action.startswith("volume:"):
            value = int(action.split(":", 1)[1])
            self.settings.set("master_volume", value)

    def _import_song_dialog(self) -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            path = filedialog.askopenfilename(
                title="Import a MIDI song",
                filetypes=[
                    ("MIDI files", "*.mid *.midi"),
                    ("All files", "*.*"),
                ],
            )

            root.destroy()

        except Exception as error:
            self._toast(
                f"The file picker could not open: {error}"
            )
            return

        if not path:
            return

        try:
            record = self.library.import_song(path)

            self._open_song(record.song_id)
            self._toast("Song imported successfully")

        except Exception as error:
            self._toast(
                f"Could not import MIDI: {error}",
                5.0,
            )

    def _open_song(self, song_id: str) -> None:
        record = self.library.get(song_id)

        if not record:
            self._toast("That song could not be found")
            return

        try:
            parser = MidiParser(
                str(self.library.path_for(record))
            ).parse()

        except Exception as error:
            self._toast(
                f"Could not open song: {error}",
                5.0,
            )
            return

        self.current_song = record
        self.current_parser = parser

        self.options.target_part = parser.auto_pick_part()

        self.options.speed = float(
            self.settings.get("default_speed", 1.0)
        )

        self.options.count_in_bars = int(
            self.settings.get("count_in_bars", 1)
        )

        self.options.metronome = bool(
            self.settings.get("metronome", False)
        )

        self.options.include_target = True

        self._select_part(self.options.target_part)

        self.part_scroll = 0
        self.page = "setup"

    def _select_part(self, part_index: int) -> None:
        if (
            not self.current_parser
            or not 0 <= part_index < len(self.current_parser.parts_summary)
        ):
            return

        self.options.target_part = part_index

        part = self.current_parser.parts_summary[part_index]

        if part["is_drums"]:
            self.options.mode = "drums"

        elif part["instrument"] in {"Guitar", "Bass"}:
            self.options.mode = "guitar"

        else:
            self.options.mode = "piano"

    def _start_practice(self) -> None:
        if not self.current_parser or not self.current_song:
            return

        self.library.mark_played(self.current_song.song_id)

        self.practice = PracticeView(
            screen=self.screen,
            fonts=self.fonts,
            theme=self.theme,
            parser=self.current_parser,
            title=self.current_song.title,
            options=PracticeOptions(**self.options.__dict__),
            volume=int(self.settings.get("master_volume", 100)),
        )

    def _finish_practice(self) -> None:
        if not self.practice:
            return

        seconds = self.practice.elapsed_practice

        self.practice.close()
        self.practice = None

        self.settings.record_session(seconds)

        self.page = "setup"

        if seconds >= 1:
            self._toast(
                f"Practice session saved: {format_time(seconds)}"
            )

    def update(self, dt: float) -> None:
        self.particles.update(dt)

        if self.practice:
            self.practice.update(dt)

        if self.toast_time > 0:
            self.toast_time -= dt

    def draw(self) -> None:
        if self.practice:
            self.practice.draw()

            if not self.practice.audio.available:
                self._draw_audio_warning()

            return

        self.actions = []

        self.screen.fill(self.theme.background)
        self.particles.draw(self.screen)

        content = self._draw_sidebar()

        if self.page == "library":
            self._draw_library(content)

        elif self.page == "setup":
            self._draw_setup(content)

        elif self.page == "settings":
            self._draw_settings(content)

        else:
            self._draw_home(content)

        if self.pending_delete:
            self._draw_delete_modal()

        if self.toast_time > 0:
            self._draw_toast()

    def _draw_sidebar(self) -> pygame.Rect:
        width, height = self.screen.get_size()

        sidebar = pygame.Rect(
            0,
            0,
            218,
            height,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.sidebar,
            sidebar,
        )

        pygame.draw.line(
            self.screen,
            self.theme.border,
            (sidebar.right, 0),
            (sidebar.right, height),
            1,
        )

        logo = pygame.Rect(
            25,
            25,
            39,
            39,
        )

        draw_glow_rect(
            self.screen,
            logo,
            self.theme.accent,
            10,
            1,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent_soft,
            logo,
            border_radius=10,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent,
            logo,
            width=1,
            border_radius=10,
        )

        draw_text(
            self.screen,
            "J",
            self.fonts.heading,
            self.theme.accent_light,
            logo.center,
            "center",
        )

        draw_text(
            self.screen,
            "JAMSPOT",
            self.fonts.subheading,
            self.theme.text,
            (76, 29),
        )

        draw_text(
            self.screen,
            "PRACTICE STUDIO",
            self.fonts.tiny,
            self.theme.text_dim,
            (77, 53),
        )

        nav_items = [
            ("HOME", "home"),
            ("SONG LIBRARY", "library"),
            ("PRACTICE SETUP", "setup"),
            ("SETTINGS", "settings"),
        ]

        y = 120

        for label, destination in nav_items:
            disabled = (
                destination == "setup"
                and not self.current_parser
            )

            rect = pygame.Rect(
                17,
                y,
                184,
                46,
            )

            selected = self.page == destination

            hovered = (
                rect.collidepoint(
                    pygame.mouse.get_pos()
                )
                and not disabled
            )

            if selected:
                colour = self.theme.panel_selected
            elif hovered:
                colour = self.theme.panel_hover
            else:
                colour = self.theme.sidebar

            pygame.draw.rect(
                self.screen,
                colour,
                rect,
                border_radius=10,
            )

            if selected:
                pygame.draw.rect(
                    self.screen,
                    self.theme.accent,
                    pygame.Rect(
                        rect.left,
                        rect.top + 9,
                        3,
                        rect.height - 18,
                    ),
                    border_radius=2,
                )

            if disabled:
                text_colour = self.theme.text_dark
            elif selected or hovered:
                text_colour = self.theme.text
            else:
                text_colour = self.theme.text_dim

            draw_text(
                self.screen,
                label,
                self.fonts.small_bold,
                text_colour,
                (rect.left + 15, rect.centery),
                "midleft",
            )

            if not disabled:
                self.actions.append(
                    (
                        rect,
                        f"nav:{destination}",
                    )
                )

            y += 55

        pygame.draw.line(
            self.screen,
            self.theme.border,
            (20, height - 105),
            (sidebar.right - 20, height - 105),
            1,
        )

        self._button(
            pygame.Rect(
                18,
                height - 78,
                182,
                48,
            ),
            "IMPORT MIDI",
            "import",
            selected=True,
        )

        return pygame.Rect(
            218,
            0,
            width - 218,
            height,
        )

    def _page_heading(
        self,
        content: pygame.Rect,
        title: str,
        subtitle: str,
    ) -> int:
        draw_text(
            self.screen,
            title,
            self.fonts.title,
            self.theme.text,
            (content.left + 30, 27),
        )

        draw_text(
            self.screen,
            subtitle,
            self.fonts.body,
            self.theme.text_dim,
            (content.left + 32, 69),
        )

        pygame.draw.line(
            self.screen,
            self.theme.border,
            (content.left + 30, 99),
            (content.right - 30, 99),
            1,
        )

        pygame.draw.line(
            self.screen,
            self.theme.accent,
            (content.left + 30, 99),
            (content.left + 86, 99),
            2,
        )

        return 122

    def _draw_home(self, content: pygame.Rect) -> None:
        top = self._page_heading(
            content,
            "Welcome to JamSpot",
            "Choose a song, isolate your part and practise with the band.",
        )

        margin = 30
        available = content.width - margin * 2

        card_gap = 14
        card_width = (
            available - card_gap * 2
        ) // 3

        stats = [
            (
                str(
                    len(
                        self.library.records
                    )
                ),
                "SONGS",
            ),
            (
                str(
                    int(
                        self.settings.get(
                            "sessions_completed",
                            0,
                        )
                    )
                ),
                "SESSIONS",
            ),
            (
                format_time(
                    float(
                        self.settings.get(
                            "practice_seconds",
                            0,
                        )
                    )
                ),
                "PRACTICE TIME",
            ),
        ]

        for index, (value, label) in enumerate(stats):
            rect = pygame.Rect(
                content.left
                + margin
                + index * (
                    card_width
                    + card_gap
                ),
                top,
                card_width,
                112,
            )

            hovered = rect.collidepoint(
                pygame.mouse.get_pos()
            )

            colour = (
                self.theme.panel_hover
                if hovered
                else self.theme.panel
            )

            draw_card(
                self.screen,
                rect,
                colour,
                border_colour=self.theme.border,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.accent,
                pygame.Rect(
                    rect.left + 18,
                    rect.top + 16,
                    27,
                    3,
                ),
                border_radius=2,
            )

            draw_text(
                self.screen,
                value,
                self.fonts.large_number,
                self.theme.text,
                (rect.left + 18, rect.top + 28),
            )

            draw_text(
                self.screen,
                label,
                self.fonts.small_bold,
                self.theme.accent_light,
                (rect.left + 20, rect.bottom - 27),
            )

        section_y = top + 144

        draw_text(
            self.screen,
            "RECENT SONGS",
            self.fonts.subheading,
            self.theme.text,
            (content.left + margin, section_y),
        )

        draw_text(
            self.screen,
            "Your most recently opened practice files",
            self.fonts.small,
            self.theme.text_dim,
            (
                content.left + margin,
                section_y + 27,
            ),
        )

        self._button(
            pygame.Rect(
                content.right
                - margin
                - 108,
                section_y - 3,
                108,
                34,
            ),
            "VIEW ALL",
            "nav:library",
        )

        records = (
            self.library
            .sorted_records()[:4]
        )

        if not records:
            empty = pygame.Rect(
                content.left + margin,
                section_y + 55,
                available,
                min(
                    220,
                    content.bottom
                    - section_y
                    - 78,
                ),
            )

            draw_card(
                self.screen,
                empty,
                self.theme.panel,
                border_colour=self.theme.border,
            )

            pygame.draw.circle(
                self.screen,
                self.theme.panel_light,
                (empty.centerx, empty.top + 53),
                24,
            )

            pygame.draw.circle(
                self.screen,
                self.theme.accent,
                (empty.centerx, empty.top + 53),
                24,
                1,
            )

            draw_text(
                self.screen,
                "M",
                self.fonts.subheading,
                self.theme.accent_light,
                (empty.centerx, empty.top + 53),
                "center",
            )

            draw_text(
                self.screen,
                "Your library is empty",
                self.fonts.heading,
                self.theme.text,
                (empty.centerx, empty.top + 94),
                "center",
            )

            draw_text(
                self.screen,
                "Import a MIDI file to separate its instruments and begin practising.",
                self.fonts.body,
                self.theme.text_dim,
                (empty.centerx, empty.top + 128),
                "center",
            )

            self._button(
                pygame.Rect(
                    empty.centerx - 73,
                    empty.bottom - 55,
                    146,
                    39,
                ),
                "IMPORT MIDI",
                "import",
                True,
            )

            return

        y = section_y + 55

        for record in records:
            if y + 68 > content.bottom - 14:
                break

            self._song_row(
                pygame.Rect(
                    content.left + margin,
                    y,
                    available,
                    66,
                ),
                record,
                compact=True,
            )

            y += 76

    def _draw_library(self, content: pygame.Rect) -> None:
        top = self._page_heading(
            content,
            "Song Library",
            "Search, favourite and open the MIDI songs you have imported.",
        )

        margin = 30

        search_rect = pygame.Rect(
            content.left + margin,
            top,
            min(
                470,
                content.width - 245,
            ),
            45,
        )

        hovered = search_rect.collidepoint(
            pygame.mouse.get_pos()
        )

        if self.search_active:
            colour = self.theme.panel_selected
        elif hovered:
            colour = self.theme.panel_hover
        else:
            colour = self.theme.panel

        pygame.draw.rect(
            self.screen,
            colour,
            search_rect,
            border_radius=11,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent
            if self.search_active
            else self.theme.border,
            search_rect,
            width=1,
            border_radius=11,
        )

        search_display = (
            self.search_text
            or "Search songs..."
        )

        search_colour = (
            self.theme.text
            if self.search_text
            else self.theme.text_dim
        )

        draw_text(
            self.screen,
            search_display,
            self.fonts.body,
            search_colour,
            (
                search_rect.left + 15,
                search_rect.centery,
            ),
            "midleft",
        )

        if (
            self.search_active
            and (
                pygame.time.get_ticks()
                // 500
            )
            % 2
            == 0
        ):
            cursor_x = (
                search_rect.left
                + 15
                + self.fonts.body.size(
                    self.search_text
                )[0]
            )

            pygame.draw.line(
                self.screen,
                self.theme.text,
                (cursor_x, search_rect.top + 11),
                (cursor_x, search_rect.bottom - 11),
                2,
            )

        self.actions.append(
            (
                search_rect,
                "search",
            )
        )

        self._button(
            pygame.Rect(
                content.right
                - margin
                - 138,
                top,
                138,
                45,
            ),
            "IMPORT MIDI",
            "import",
            True,
        )

        records = self.library.sorted_records(
            self.search_text
        )

        list_top = top + 66
        list_bottom = content.bottom - 24

        row_height = 78
        visible_height = list_bottom - list_top

        max_scroll = max(
            0,
            len(records)
            * row_height
            - visible_height,
        )

        self.library_scroll = int(
            clamp(
                self.library_scroll,
                0,
                max_scroll,
            )
        )

        self.screen.set_clip(
            pygame.Rect(
                content.left,
                list_top,
                content.width,
                visible_height,
            )
        )

        y = list_top - self.library_scroll

        for record in records:
            row = pygame.Rect(
                content.left + margin,
                y,
                content.width
                - margin * 2
                - (
                    7
                    if max_scroll > 0
                    else 0
                ),
                68,
            )

            if (
                row.bottom >= list_top
                and row.top <= list_bottom
            ):
                self._song_row(
                    row,
                    record,
                )

            y += row_height

        self.screen.set_clip(None)

        if max_scroll > 0:
            scrollbar = pygame.Rect(
                content.right - 19,
                list_top,
                4,
                visible_height,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.panel_light,
                scrollbar,
                border_radius=2,
            )

            thumb_height = max(
                38,
                int(
                    visible_height
                    * (
                        visible_height
                        / (
                            len(records)
                            * row_height
                        )
                    )
                ),
            )

            travel = visible_height - thumb_height

            thumb_y = (
                list_top
                + int(
                    travel
                    * self.library_scroll
                    / max(
                        1,
                        max_scroll,
                    )
                )
            )

            pygame.draw.rect(
                self.screen,
                self.theme.border_light,
                pygame.Rect(
                    scrollbar.left,
                    thumb_y,
                    scrollbar.width,
                    thumb_height,
                ),
                border_radius=2,
            )

        if not records:
            draw_text(
                self.screen,
                (
                    "No songs match your search."
                    if self.search_text
                    else "No songs imported yet."
                ),
                self.fonts.subheading,
                self.theme.text_dim,
                (content.centerx, list_top + 90),
                "center",
            )

    def _song_row(
        self,
        rect: pygame.Rect,
        record: SongRecord,
        compact: bool = False,
    ) -> None:
        hovered = rect.collidepoint(
            pygame.mouse.get_pos()
        )

        draw_card(
            self.screen,
            rect,
            self.theme.panel_hover
            if hovered
            else self.theme.panel,
            border_colour=(
                self.theme.border_light
                if hovered
                else self.theme.border
            ),
        )

        marker = pygame.Rect(
            rect.left + 13,
            rect.top + 13,
            42,
            42,
        )

        marker_colour = (
            mix_colour(
                self.theme.panel_light,
                self.theme.accent,
                0.45,
            )
            if record.favourite
            else self.theme.panel_light
        )

        pygame.draw.rect(
            self.screen,
            marker_colour,
            marker,
            border_radius=10,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent_soft,
            marker,
            width=1,
            border_radius=10,
        )

        draw_text(
            self.screen,
            "M",
            self.fonts.body_bold,
            self.theme.accent_light,
            marker.center,
            "center",
        )

        reserved = 180 if compact else 230

        title = self._fit_text(
            record.title,
            self.fonts.body_bold,
            max(
                150,
                rect.width
                - reserved
                - 70,
            ),
        )

        draw_text(
            self.screen,
            title,
            self.fonts.body_bold,
            self.theme.text,
            (rect.left + 67, rect.top + 13),
        )

        details = (
            f"{record.part_count} parts   "
            f"{record.bpm:.0f} BPM   "
            f"{record.time_signature}   "
            f"{format_time(record.duration)}"
        )

        draw_text(
            self.screen,
            details,
            self.fonts.small,
            self.theme.text_dim,
            (rect.left + 67, rect.top + 39),
        )

        self._button(
            pygame.Rect(
                rect.right - 91,
                rect.top + 15,
                76,
                36,
            ),
            "OPEN",
            f"open:{record.song_id}",
            selected=True,
        )

        self._button(
            pygame.Rect(
                rect.right - 137,
                rect.top + 15,
                38,
                36,
            ),
            "*" if record.favourite else "+",
            f"favourite:{record.song_id}",
            selected=record.favourite,
        )

        if not compact:
            self._button(
                pygame.Rect(
                    rect.right - 183,
                    rect.top + 15,
                    38,
                    36,
                ),
                "X",
                f"ask_delete:{record.song_id}",
                danger=True,
            )

    def _draw_setup(self, content: pygame.Rect) -> None:
        if not self.current_parser or not self.current_song:
            self.page = "library"
            return

        top = self._page_heading(
            content,
            "Practice Setup",
            self.current_song.title,
        )

        margin = 30
        gap = 16

        available = (
            content.width
            - margin * 2
            - gap
        )

        left_width = int(
            available * 0.55
        )

        left = pygame.Rect(
            content.left + margin,
            top,
            left_width,
            content.height - top - 26,
        )

        right = pygame.Rect(
            left.right + gap,
            top,
            content.right
            - margin
            - left.right
            - gap,
            left.height,
        )

        draw_card(
            self.screen,
            left,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        draw_card(
            self.screen,
            right,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        draw_text(
            self.screen,
            "1. CHOOSE YOUR PART",
            self.fonts.subheading,
            self.theme.text,
            (left.left + 18, left.top + 18),
        )

        draw_text(
            self.screen,
            f"{len(self.current_parser.parts_summary)} instrument parts detected",
            self.fonts.small,
            self.theme.text_dim,
            (left.left + 20, left.top + 49),
        )

        list_rect = pygame.Rect(
            left.left + 14,
            left.top + 76,
            left.width - 33,
            left.height - 90,
        )

        row_height = 62

        max_scroll = max(
            0,
            len(
                self.current_parser
                .parts_summary
            )
            * row_height
            - list_rect.height,
        )

        self.part_scroll = int(
            clamp(
                self.part_scroll,
                0,
                max_scroll,
            )
        )

        self.screen.set_clip(
            list_rect
        )

        y = list_rect.top - self.part_scroll

        for part in self.current_parser.parts_summary:
            selected = (
                part["part_index"]
                == self.options.target_part
            )

            row = pygame.Rect(
                list_rect.left,
                y,
                list_rect.width - 7,
                54,
            )

            if (
                row.bottom >= list_rect.top
                and row.top <= list_rect.bottom
            ):
                hovered = row.collidepoint(
                    pygame.mouse.get_pos()
                )

                if selected:
                    colour = self.theme.panel_selected
                elif hovered:
                    colour = self.theme.panel_hover
                else:
                    colour = self.theme.panel_light

                pygame.draw.rect(
                    self.screen,
                    colour,
                    row,
                    border_radius=10,
                )

                pygame.draw.rect(
                    self.screen,
                    self.theme.accent
                    if selected
                    else self.theme.border,
                    row,
                    width=1,
                    border_radius=10,
                )

                if selected:
                    pygame.draw.rect(
                        self.screen,
                        self.theme.accent,
                        pygame.Rect(
                            row.left,
                            row.top + 8,
                            4,
                            row.height - 16,
                        ),
                        border_radius=2,
                    )

                name = self._fit_text(
                    part["name"],
                    self.fonts.body_bold,
                    row.width - 135,
                )

                draw_text(
                    self.screen,
                    name,
                    self.fonts.body_bold,
                    self.theme.text,
                    (row.left + 15, row.top + 8),
                )

                draw_text(
                    self.screen,
                    f'{part["note_count"]} notes   {part["instrument"]}',
                    self.fonts.small,
                    self.theme.text_dim,
                    (row.left + 15, row.top + 31),
                )

                draw_text(
                    self.screen,
                    "SELECTED"
                    if selected
                    else "SELECT",
                    self.fonts.tiny_bold,
                    self.theme.accent_light
                    if selected
                    else self.theme.text_dim,
                    (row.right - 13, row.centery),
                    "midright",
                )

                self.actions.append(
                    (
                        row,
                        f'part:{part["part_index"]}',
                    )
                )

            y += row_height

        self.screen.set_clip(None)

        if max_scroll > 0:
            scrollbar = pygame.Rect(
                list_rect.right + 5,
                list_rect.top,
                3,
                list_rect.height,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.panel_light,
                scrollbar,
                border_radius=2,
            )

            thumb_height = max(
                35,
                int(
                    list_rect.height
                    * (
                        list_rect.height
                        / (
                            len(
                                self.current_parser
                                .parts_summary
                            )
                            * row_height
                        )
                    )
                ),
            )

            thumb_y = (
                scrollbar.top
                + int(
                    (
                        scrollbar.height
                        - thumb_height
                    )
                    * self.part_scroll
                    / max(
                        1,
                        max_scroll,
                    )
                )
            )

            pygame.draw.rect(
                self.screen,
                self.theme.border_light,
                pygame.Rect(
                    scrollbar.left,
                    thumb_y,
                    scrollbar.width,
                    thumb_height,
                ),
                border_radius=2,
            )

        x = right.left + 18
        usable = right.width - 36

        draw_text(
            self.screen,
            "2. PRACTICE OPTIONS",
            self.fonts.subheading,
            self.theme.text,
            (x, right.top + 18),
        )

        selected_part = self.current_parser.parts_summary[
            self.options.target_part
        ]

        part_name = self._fit_text(
            selected_part["name"],
            self.fonts.small,
            usable,
        )

        draw_text(
            self.screen,
            part_name,
            self.fonts.small,
            self.theme.text_dim,
            (x, right.top + 47),
        )

        y = right.top + 76

        draw_text(
            self.screen,
            "DISPLAY",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (x, y),
        )

        y += 18

        button_width = (
            usable - 12
        ) // 3

        for index, (label, mode) in enumerate(
            (
                ("PIANO", "piano"),
                ("DRUMS", "drums"),
                ("TAB", "guitar"),
            )
        ):
            rect = pygame.Rect(
                x
                + index
                * (
                    button_width
                    + 6
                ),
                y,
                button_width,
                34,
            )

            self._button(
                rect,
                label,
                f"mode:{mode}",
                self.options.mode == mode,
            )

        y += 50

        draw_text(
            self.screen,
            "SPEED",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (x, y),
        )

        y += 18

        speed_width = (
            usable - 18
        ) // 4

        for index, speed in enumerate(
            (
                0.5,
                0.75,
                1.0,
                1.25,
            )
        ):
            rect = pygame.Rect(
                x
                + index
                * (
                    speed_width
                    + 6
                ),
                y,
                speed_width,
                34,
            )

            self._button(
                rect,
                f"{speed:g}x",
                f"speed:{speed}",
                self.options.speed
                == speed,
            )

        y += 50

        draw_text(
            self.screen,
            "COUNT-IN",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (x, y),
        )

        y += 18

        count_width = (
            usable - 12
        ) // 3

        for index, bars in enumerate(
            (
                0,
                1,
                2,
            )
        ):
            label = (
                "OFF"
                if bars == 0
                else (
                    "1 BAR"
                    if bars == 1
                    else "2 BARS"
                )
            )

            rect = pygame.Rect(
                x
                + index
                * (
                    count_width
                    + 6
                ),
                y,
                count_width,
                34,
            )

            self._button(
                rect,
                label,
                f"count:{bars}",
                self.options.count_in_bars
                == bars,
            )

        y += 50

        self._toggle_row(
            pygame.Rect(
                x,
                y,
                usable,
                40,
            ),
            "HEAR TARGET PART",
            self.options.include_target,
            "target_sound",
        )

        y += 46

        self._toggle_row(
            pygame.Rect(
                x,
                y,
                usable,
                40,
            ),
            "METRONOME",
            self.options.metronome,
            "metronome",
        )

        self._button(
            pygame.Rect(
                x,
                right.bottom - 55,
                usable,
                41,
            ),
            "START PRACTICE",
            "start_practice",
            selected=True,
        )

    def _draw_settings(self, content: pygame.Rect) -> None:
        top = self._page_heading(
            content,
            "Settings",
            "Choose the defaults used whenever you open a practice session.",
        )

        panel = pygame.Rect(
            content.left + 30,
            top,
            content.width - 60,
            content.height - top - 28,
        )

        draw_card(
            self.screen,
            panel,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        inside = panel.inflate(-42, -38)

        gap = 40

        left_width = int(
            (
                inside.width
                - gap
            )
            * 0.48
        )

        left = pygame.Rect(
            inside.left,
            inside.top,
            left_width,
            inside.height,
        )

        right = pygame.Rect(
            left.right + gap,
            inside.top,
            inside.right
            - left.right
            - gap,
            inside.height,
        )

        draw_text(
            self.screen,
            "PRACTICE DEFAULTS",
            self.fonts.subheading,
            self.theme.text,
            (left.left, left.top),
        )

        y = left.top + 47

        draw_text(
            self.screen,
            "DEFAULT SPEED",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (left.left, y),
        )

        y += 22

        speed_width = (
            left.width
            - 18
        ) // 4

        for index, speed in enumerate(
            (
                0.5,
                0.75,
                1.0,
                1.25,
            )
        ):
            rect = pygame.Rect(
                left.left
                + index
                * (
                    speed_width
                    + 6
                ),
                y,
                speed_width,
                38,
            )

            self._button(
                rect,
                f"{speed:g}x",
                f"setting_speed:{speed}",
                float(
                    self.settings.get(
                        "default_speed",
                        1.0,
                    )
                )
                == speed,
            )

        y += 66

        draw_text(
            self.screen,
            "DEFAULT COUNT-IN",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (left.left, y),
        )

        y += 22

        count_width = (
            left.width
            - 12
        ) // 3

        for index, bars in enumerate(
            (
                0,
                1,
                2,
            )
        ):
            label = (
                "OFF"
                if bars == 0
                else (
                    "1 BAR"
                    if bars == 1
                    else "2 BARS"
                )
            )

            rect = pygame.Rect(
                left.left
                + index
                * (
                    count_width
                    + 6
                ),
                y,
                count_width,
                38,
            )

            self._button(
                rect,
                label,
                f"setting_count:{bars}",
                int(
                    self.settings.get(
                        "count_in_bars",
                        1,
                    )
                )
                == bars,
            )

        y += 68

        self._toggle_row(
            pygame.Rect(
                left.left,
                y,
                left.width,
                42,
            ),
            "METRONOME BY DEFAULT",
            bool(
                self.settings.get(
                    "metronome",
                    False,
                )
            ),
            "setting_metronome",
        )

        y += 68

        draw_text(
            self.screen,
            "MASTER MIDI VOLUME",
            self.fonts.tiny_bold,
            self.theme.text_dim,
            (left.left, y),
        )

        y += 22

        volume_width = (
            left.width
            - 18
        ) // 4

        for index, volume in enumerate(
            (
                40,
                70,
                100,
                127,
            )
        ):
            rect = pygame.Rect(
                left.left
                + index
                * (
                    volume_width
                    + 6
                ),
                y,
                volume_width,
                38,
            )

            self._button(
                rect,
                str(volume),
                f"volume:{volume}",
                int(
                    self.settings.get(
                        "master_volume",
                        100,
                    )
                )
                == volume,
            )

        pygame.draw.line(
            self.screen,
            self.theme.border,
            (right.left - 20, inside.top),
            (right.left - 20, inside.bottom),
            1,
        )

        draw_text(
            self.screen,
            "KEYBOARD CONTROLS",
            self.fonts.subheading,
            self.theme.text,
            (right.left, right.top),
        )

        controls = [
            ("SPACE", "Play or pause"),
            ("LEFT / RIGHT", "Seek two seconds"),
            ("R", "Restart the song"),
            ("L", "Toggle the loop"),
            ("1 / 2 / 3", "Piano, drums or tab view"),
            ("ESC", "Return to setup"),
        ]

        help_y = right.top + 48

        for key, meaning in controls:
            key_width = min(
                118,
                max(
                    66,
                    self.fonts.mono.size(key)[0]
                    + 25,
                ),
            )

            key_rect = pygame.Rect(
                right.left,
                help_y,
                key_width,
                31,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.panel_light,
                key_rect,
                border_radius=7,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.border,
                key_rect,
                width=1,
                border_radius=7,
            )

            draw_text(
                self.screen,
                key,
                self.fonts.mono,
                self.theme.accent_light,
                key_rect.center,
                "center",
            )

            draw_text(
                self.screen,
                meaning,
                self.fonts.body,
                self.theme.text_dim,
                (
                    key_rect.right + 15,
                    key_rect.centery,
                ),
                "midleft",
            )

            help_y += 43

    def _draw_audio_warning(self) -> None:
        width, _ = self.screen.get_size()

        rect = pygame.Rect(
            width // 2 - 224,
            116,
            448,
            36,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.orange,
            rect,
            border_radius=10,
        )

        draw_text(
            self.screen,
            "No MIDI audio output found - visuals are still available",
            self.fonts.small_bold,
            (20, 21, 24),
            rect.center,
            "center",
        )

    def _draw_delete_modal(self) -> None:
        width, height = self.screen.get_size()

        overlay = pygame.Surface(
            (width, height),
            pygame.SRCALPHA,
        )

        overlay.fill((3, 4, 7, 205))
        self.screen.blit(overlay, (0, 0))

        panel = pygame.Rect(
            width // 2 - 230,
            height // 2 - 105,
            460,
            210,
        )

        draw_card(
            self.screen,
            panel,
            self.theme.panel,
            border_colour=self.theme.border_light,
        )

        draw_text(
            self.screen,
            "Remove this song?",
            self.fonts.heading,
            self.theme.text,
            (panel.centerx, panel.top + 42),
            "center",
        )

        draw_wrapped_text(
            self.screen,
            "The copied MIDI file and its JamSpot library entry will be deleted.",
            self.fonts.body,
            self.theme.text_dim,
            panel.left + 42,
            panel.top + 84,
            panel.width - 84,
        )

        self._button(
            pygame.Rect(
                panel.left + 56,
                panel.bottom - 58,
                150,
                40,
            ),
            "CANCEL",
            "delete_cancel",
        )

        self._button(
            pygame.Rect(
                panel.right - 206,
                panel.bottom - 58,
                150,
                40,
            ),
            "REMOVE",
            "delete_confirm",
            danger=True,
        )

    def _draw_toast(self) -> None:
        width, height = self.screen.get_size()

        text_width = min(
            640,
            self.fonts.body.size(
                self.toast_text
            )[0]
            + 42,
        )

        rect = pygame.Rect(
            width // 2
            - text_width // 2,
            height - 70,
            text_width,
            42,
        )

        draw_glow_rect(
            self.screen,
            rect,
            self.theme.accent,
            11,
            1,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.panel_selected,
            rect,
            border_radius=11,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent,
            rect,
            width=1,
            border_radius=11,
        )

        fitted = self._fit_text(
            self.toast_text,
            self.fonts.body,
            rect.width - 30,
        )

        draw_text(
            self.screen,
            fitted,
            self.fonts.body,
            self.theme.text,
            rect.center,
            "center",
        )

    def _button(
        self,
        rect: pygame.Rect,
        label: str,
        action: str,
        selected: bool = False,
        danger: bool = False,
    ) -> None:
        hovered = rect.collidepoint(
            pygame.mouse.get_pos()
        )

        if danger:
            colour = (
                self.theme.red
                if hovered
                else mix_colour(
                    self.theme.panel_light,
                    self.theme.red,
                    0.55,
                )
            )

            border = self.theme.red

        elif selected:
            colour = self.theme.accent_soft
            border = self.theme.accent

        elif hovered:
            colour = self.theme.panel_hover
            border = self.theme.border_light

        else:
            colour = self.theme.panel_light
            border = self.theme.border

        pygame.draw.rect(
            self.screen,
            colour,
            rect,
            border_radius=9,
        )

        pygame.draw.rect(
            self.screen,
            border,
            rect,
            width=1,
            border_radius=9,
        )

        text_colour = (
            self.theme.accent_light
            if selected
            else self.theme.text
        )

        draw_text(
            self.screen,
            label,
            self.fonts.small_bold,
            text_colour,
            rect.center,
            "center",
        )

        self.actions.append(
            (
                rect,
                action,
            )
        )

    def _toggle_row(
        self,
        rect: pygame.Rect,
        label: str,
        enabled: bool,
        action: str,
    ) -> None:
        hovered = rect.collidepoint(
            pygame.mouse.get_pos()
        )

        colour = (
            self.theme.panel_hover
            if hovered
            else self.theme.panel_light
        )

        pygame.draw.rect(
            self.screen,
            colour,
            rect,
            border_radius=10,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent
            if enabled
            else self.theme.border,
            rect,
            width=1,
            border_radius=10,
        )

        draw_text(
            self.screen,
            label,
            self.fonts.small_bold,
            self.theme.text
            if enabled
            else self.theme.text_dim,
            (rect.left + 13, rect.centery),
            "midleft",
        )

        switch = pygame.Rect(
            rect.right - 53,
            rect.centery - 12,
            40,
            24,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.accent
            if enabled
            else self.theme.border,
            switch,
            border_radius=12,
        )

        knob_x = (
            switch.right - 12
            if enabled
            else switch.left + 12
        )

        pygame.draw.circle(
            self.screen,
            self.theme.text,
            (
                knob_x,
                switch.centery,
            ),
            8,
        )

        self.actions.append(
            (
                rect,
                action,
            )
        )

    def _fit_text(self, text: str, font, width: int) -> str:
        if font.size(text)[0] <= width:
            return text

        shortened = text

        while (
            shortened
            and font.size(
                shortened + "..."
            )[0]
            > width
        ):
            shortened = shortened[:-1]

        return shortened + "..."

    def _toast(
        self,
        message: str,
        duration: float = 3.0,
    ) -> None:
        self.toast_text = message
        self.toast_time = duration