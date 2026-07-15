import math
from typing import List, Optional, Tuple

import pygame

from .audio_player import MidiAudioPlayer
from .tab_converter import STANDARD_TUNING, TabEvent, build_guitar_tab, tab_statistics
from .midi_mixer import build_mix
from .midi_parser import MidiParser
from .models import NoteEvent, PracticeOptions
from .ui import FontBook, Theme, draw_card, draw_text
from .utils import clamp, format_time


class PracticeView:
    """Interactive practice screen shared by the drum, tab and piano modes."""

    DRUM_ROWS = [
        ("Kick", {35, 36}),
        ("Snare", {38, 40}),
        ("Clap", {39}),
        ("Hi-hat", {42, 44, 46}),
        ("Toms", {41, 43, 45, 47, 48, 50}),
        ("Crash", {49, 57}),
        ("Ride", {51, 59}),
        ("Other", set(range(0, 128))),
    ]

    def __init__(
        self,
        screen: pygame.Surface,
        fonts: FontBook,
        theme: Theme,
        parser: MidiParser,
        title: str,
        options: PracticeOptions,
        volume: int = 100,
    ):
        self.screen = screen
        self.fonts = fonts
        self.theme = theme
        self.parser = parser
        self.title = title
        self.options = options
        self.part = parser.parts_summary[options.target_part]
        self.notes = list(parser.parts_notes[options.target_part])
        self.song_length = parser.song_length_seconds
        self.time_signature = parser.time_signatures[0] if parser.time_signatures else "4/4"
        self.key_signature = parser.key_signatures[0] if parser.key_signatures else "Unknown"
        self.bpm = parser.initial_bpm
        self.speed = options.speed
        self.mode = options.mode
        self.time = 0.0
        self.previous_time = 0.0
        self.playing = False
        self.counting_in = False
        self.count_in_remaining = 0.0
        self.count_in_total = 0.0
        self.count_in_last_beat = -1
        self.metronome = options.metronome
        self.loop_enabled = False
        self.loop_a = 0.0
        self.loop_b = self.song_length
        self.elapsed_practice = 0.0
        self.actions: List[Tuple[pygame.Rect, str]] = []
        self.timeline_rect: Optional[pygame.Rect] = None
        self.tab_events: List[TabEvent] = build_guitar_tab(
            [note for note in self.notes if note.channel != 9]
        )

        excluded = set() if options.include_target else {options.target_part}
        audio_notes, programs = build_mix(parser, excluded_parts=excluded)
        self.audio = MidiAudioPlayer(programs, volume=volume)
        self.audio.load_notes(audio_notes)

        try:
            numerator, denominator = self.time_signature.split("/")
            self.beats_per_bar = max(1, int(numerator))
            self.beat_unit = max(1, int(denominator))
        except (ValueError, TypeError):
            self.beats_per_bar = 4
            self.beat_unit = 4

    def set_screen(self, screen: pygame.Surface) -> None:
        self.screen = screen

    def beat_seconds(self) -> float:
        quarter_note = 60.0 / max(1.0, self.bpm)
        return quarter_note * (4.0 / self.beat_unit)

    def bar_seconds(self) -> float:
        return self.beats_per_bar * self.beat_seconds()

    def close(self) -> None:
        self.audio.close()

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return "back"
            if event.key == pygame.K_SPACE:
                self.toggle_playback()
            elif event.key == pygame.K_LEFT:
                self.seek(self.time - 2.0)
            elif event.key == pygame.K_RIGHT:
                self.seek(self.time + 2.0)
            elif event.key == pygame.K_r:
                self.restart()
            elif event.key == pygame.K_l:
                self.loop_enabled = not self.loop_enabled
            elif event.key == pygame.K_1:
                self.mode = "piano"
            elif event.key == pygame.K_2:
                self.mode = "drums"
            elif event.key == pygame.K_3:
                self.mode = "guitar"

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for rect, action in reversed(self.actions):
                if rect.collidepoint(event.pos):
                    return self._perform_action(action, event.pos)
        return None

    def _perform_action(self, action: str, mouse_position) -> Optional[str]:
        if action == "back":
            return "back"
        if action == "play":
            self.toggle_playback()
        elif action == "restart":
            self.restart()
        elif action == "timeline" and self.timeline_rect:
            fraction = (mouse_position[0] - self.timeline_rect.left) / self.timeline_rect.width
            self.seek(fraction * self.song_length)
        elif action.startswith("mode:"):
            self.mode = action.split(":", 1)[1]
        elif action.startswith("speed:"):
            self.speed = float(action.split(":", 1)[1])
        elif action == "metronome":
            self.metronome = not self.metronome
        elif action == "set_a":
            self.loop_a = min(self.time, self.loop_b - 0.2)
        elif action == "set_b":
            self.loop_b = max(self.time, self.loop_a + 0.2)
        elif action == "loop":
            self.loop_enabled = not self.loop_enabled
        return None

    def toggle_playback(self) -> None:
        if self.playing or self.counting_in:
            self.playing = False
            self.counting_in = False
            self.audio.stop_all_notes()
            return

        if self.options.count_in_bars > 0:
            self._start_count_in()
        else:
            self.playing = True
            self.audio.start(self.time)

    def _start_count_in(self) -> None:
        beat_length = self.beat_seconds() / self.speed
        beat_count = self.options.count_in_bars * self.beats_per_bar
        self.count_in_total = beat_count * beat_length
        self.count_in_remaining = self.count_in_total
        self.count_in_last_beat = -1
        self.counting_in = True

    def restart(self) -> None:
        self.playing = False
        self.counting_in = False
        self.time = 0.0
        self.previous_time = 0.0
        self.audio.seek(0.0)

    def seek(self, new_time: float) -> None:
        was_playing = self.playing
        self.time = float(clamp(new_time, 0.0, self.song_length))
        self.previous_time = self.time
        self.counting_in = False
        self.audio.seek(self.time)
        if was_playing:
            self.audio.start(self.time)

    def update(self, dt: float) -> None:
        if self.counting_in:
            self.count_in_remaining -= dt
            beat_length = self.beat_seconds() / self.speed
            elapsed = self.count_in_total - max(0.0, self.count_in_remaining)
            beat_index = int(elapsed / max(0.01, beat_length))
            if beat_index != self.count_in_last_beat:
                self.audio.click(beat_index % self.beats_per_bar == 0)
                self.count_in_last_beat = beat_index
            if self.count_in_remaining <= 0:
                self.counting_in = False
                self.playing = True
                self.audio.start(self.time)
            return

        if not self.playing:
            return

        self.elapsed_practice += dt
        self.previous_time = self.time
        self.time += dt * self.speed

        if self.loop_enabled and self.time >= self.loop_b:
            self.time = self.loop_a
            self.previous_time = self.time
            self.audio.start(self.time)
        elif self.time >= self.song_length:
            self.time = self.song_length
            self.playing = False
            self.audio.stop_all_notes()
        else:
            self.audio.update(self.time)

        if self.metronome:
            old_beat = int(self.previous_time / self.beat_seconds())
            new_beat = int(self.time / self.beat_seconds())
            if new_beat != old_beat:
                self.audio.click(new_beat % self.beats_per_bar == 0)

    def draw(self) -> None:
        self.actions = []
        width, height = self.screen.get_size()
        self.screen.fill(self.theme.background)

        header = pygame.Rect(18, 16, width - 36, 92)
        controls = pygame.Rect(18, height - 142, width - 36, 124)
        content = pygame.Rect(18, 122, width - 36, height - 282)
        self._draw_header(header)
        self._draw_content(content)
        self._draw_controls(controls)

        if self.counting_in:
            self._draw_count_in_overlay()

    def _register_button(
        self, rect: pygame.Rect, text: str, action: str, selected: bool = False, danger: bool = False
    ) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        if danger:
            colour = self.theme.red
        elif selected:
            colour = self.theme.accent
        elif hovered:
            colour = self.theme.panel_hover
        else:
            colour = self.theme.panel_light
        pygame.draw.rect(self.screen, colour, rect, border_radius=10)
        draw_text(
            self.screen,
            text,
            self.fonts.small_bold,
            self.theme.text,
            rect.center,
            "center",
        )
        self.actions.append((rect, action))

    def _chip(self, x: int, y: int, text: str) -> int:
        rendered = self.fonts.small.render(text, True, self.theme.text_dim)
        rect = pygame.Rect(x, y, rendered.get_width() + 20, 28)
        pygame.draw.rect(self.screen, self.theme.panel_light, rect, border_radius=14)
        self.screen.blit(rendered, (rect.left + 10, rect.centery - rendered.get_height() // 2))
        return rect.right + 8

    def _draw_header(self, rect: pygame.Rect) -> None:
        draw_card(self.screen, rect, self.theme.panel)
        self._register_button(pygame.Rect(rect.left + 14, rect.top + 14, 70, 34), "BACK", "back")
        draw_text(self.screen, self.title, self.fonts.heading, self.theme.text, (rect.left + 100, rect.top + 14))
        draw_text(
            self.screen,
            self.part["name"],
            self.fonts.small,
            self.theme.text_dim,
            (rect.left + 102, rect.top + 51),
        )

        mode_x = rect.right - 322
        for label, mode in (("PIANO", "piano"), ("DRUMS", "drums"), ("TAB", "guitar")):
            button_rect = pygame.Rect(mode_x, rect.top + 14, 94, 34)
            self._register_button(button_rect, label, f"mode:{mode}", self.mode == mode)
            mode_x += 102

        chip_x = rect.right - 322
        chip_y = rect.top + 55
        chip_x = self._chip(chip_x, chip_y, f"{self.bpm:.0f} BPM")
        chip_x = self._chip(chip_x, chip_y, self.time_signature)
        self._chip(chip_x, chip_y, f"KEY {self.key_signature}")

    def _draw_content(self, rect: pygame.Rect) -> None:
        draw_card(self.screen, rect, self.theme.panel)
        inner = rect.inflate(-24, -24)
        if self.mode == "drums":
            self._draw_drums(inner)
        elif self.mode == "guitar":
            self._draw_guitar(inner)
        else:
            self._draw_piano(inner)

    def _time_geometry(self, rect: pygame.Rect):
        playline = rect.left + int(rect.width * 0.28)
        seconds_right = max(self.bar_seconds() * 2.5, 2.0)
        pixels_per_second = (rect.right - playline - 18) / seconds_right
        visible_start = self.time - (playline - rect.left) / pixels_per_second
        visible_end = self.time + seconds_right
        return playline, pixels_per_second, visible_start, visible_end

    def _draw_time_grid(self, rect: pygame.Rect, playline: int, pixels_per_second: float) -> None:
        beat = self.beat_seconds()
        bar = self.bar_seconds()
        visible_start = self.time - (playline - rect.left) / pixels_per_second
        visible_end = self.time + (rect.right - playline) / pixels_per_second

        beat_number = math.floor(visible_start / beat)
        while beat_number * beat <= visible_end + beat:
            beat_time = beat_number * beat
            x = playline + int((beat_time - self.time) * pixels_per_second)
            if rect.left <= x <= rect.right:
                colour = self.theme.border
                line_width = 2 if beat_number % self.beats_per_bar == 0 else 1
                pygame.draw.line(self.screen, colour, (x, rect.top), (x, rect.bottom), line_width)
            beat_number += 1
        pygame.draw.line(
            self.screen, self.theme.accent_light, (playline, rect.top), (playline, rect.bottom), 3
        )

    def _draw_piano(self, rect: pygame.Rect) -> None:
        pitched = [note for note in self.notes if note.channel != 9]
        if not pitched:
            self._empty_state(rect, "This selected part has no pitched notes.", "Try the DRUMS view.")
            return

        label_width = 80
        roll = pygame.Rect(rect.left + label_width, rect.top, rect.width - label_width, rect.height)
        pygame.draw.rect(self.screen, (12, 15, 22), roll, border_radius=12)
        playline, pps, start, end = self._time_geometry(roll)
        self._draw_time_grid(roll, playline, pps)

        low = min(note.pitch for note in pitched) - 2
        high = max(note.pitch for note in pitched) + 2
        pitch_range = max(1, high - low)
        draw_text(self.screen, f"HIGH {high}", self.fonts.small, self.theme.text_dim, (rect.left, rect.top + 4))
        draw_text(
            self.screen,
            f"LOW {low}",
            self.fonts.small,
            self.theme.text_dim,
            (rect.left, rect.bottom - 20),
        )

        self.screen.set_clip(roll)
        for note in pitched:
            if note.start_sec > end:
                break
            if note.start_sec + note.duration_sec < start:
                continue
            fraction = (note.pitch - low) / pitch_range
            y = roll.bottom - 24 - int(fraction * (roll.height - 48))
            x = playline + int((note.start_sec - self.time) * pps)
            note_width = max(10, int(note.duration_sec * pps))
            note_rect = pygame.Rect(x, y - 11, note_width, 23)
            pygame.draw.rect(self.screen, self.theme.accent, note_rect, border_radius=7)
            if note_rect.width > 42:
                draw_text(
                    self.screen,
                    note.label,
                    self.fonts.small_bold,
                    self.theme.text,
                    (note_rect.left + 7, note_rect.top + 4),
                )
        self.screen.set_clip(None)

    def _drum_row(self, pitch: int) -> int:
        for index, (_, pitches) in enumerate(self.DRUM_ROWS[:-1]):
            if pitch in pitches:
                return index
        return len(self.DRUM_ROWS) - 1

    def _draw_drums(self, rect: pygame.Rect) -> None:
        drum_notes = [note for note in self.notes if note.channel == 9]
        if not drum_notes:
            self._empty_state(rect, "This selected part is not a drum track.", "Choose a drum part or use PIANO/TAB.")
            return

        label_width = 105
        roll = pygame.Rect(rect.left + label_width, rect.top, rect.width - label_width, rect.height)
        pygame.draw.rect(self.screen, (12, 15, 22), roll, border_radius=12)
        row_height = roll.height / len(self.DRUM_ROWS)
        playline, pps, start, end = self._time_geometry(roll)

        for index, (name, _) in enumerate(self.DRUM_ROWS):
            y = roll.top + int(index * row_height)
            lane = pygame.Rect(roll.left, y, roll.width, math.ceil(row_height))
            if index % 2 == 0:
                pygame.draw.rect(self.screen, (16, 19, 27), lane)
            pygame.draw.line(self.screen, self.theme.border, (roll.left, lane.bottom), (roll.right, lane.bottom))
            draw_text(
                self.screen,
                name.upper(),
                self.fonts.small_bold,
                self.theme.text_dim,
                (rect.left + 4, y + row_height / 2),
                "midleft",
            )
        self._draw_time_grid(roll, playline, pps)

        self.screen.set_clip(roll)
        for note in drum_notes:
            if note.start_sec > end:
                break
            if note.start_sec + note.duration_sec < start:
                continue
            row = self._drum_row(note.pitch)
            y = roll.top + int((row + 0.5) * row_height)
            x = playline + int((note.start_sec - self.time) * pps)
            radius = 6 + int(note.velocity / 127 * 6)
            pygame.draw.circle(self.screen, self.theme.orange, (x, y), radius)
            pygame.draw.circle(self.screen, self.theme.text, (x, y), radius, 1)
        self.screen.set_clip(None)

    def _draw_guitar(self, rect: pygame.Rect) -> None:
        if not self.tab_events:
            self._empty_state(rect, "No guitar tablature can be made from this part.", "Choose a pitched instrument part.")
            return

        label_width = 52
        roll = pygame.Rect(rect.left + label_width, rect.top + 35, rect.width - label_width, rect.height - 55)
        pygame.draw.rect(self.screen, (12, 15, 22), roll, border_radius=12)
        playline, pps, start, end = self._time_geometry(roll)
        string_gap = roll.height / 6

        stats = tab_statistics(self.tab_events)
        draw_text(
            self.screen,
            f"STANDARD TUNING  |  {stats['mapped']} notes mapped  |  highest fret {stats['highest_fret']}",
            self.fonts.small,
            self.theme.text_dim,
            (rect.left, rect.top + 2),
        )

        for index, (name, _) in enumerate(STANDARD_TUNING):
            y = roll.top + int((index + 0.5) * string_gap)
            draw_text(
                self.screen,
                name,
                self.fonts.body_bold,
                self.theme.text,
                (rect.left + 17, y),
                "center",
            )
            line_colour = (126, 130, 142) if index < 3 else (170, 173, 181)
            pygame.draw.line(self.screen, line_colour, (roll.left, y), (roll.right, y), 1 + index // 3)
        self._draw_time_grid(roll, playline, pps)

        self.screen.set_clip(roll)
        for event in self.tab_events:
            if event.start_sec > end:
                break
            if event.start_sec + event.duration_sec < start or event.string_index < 0:
                continue
            x = playline + int((event.start_sec - self.time) * pps)
            y = roll.top + int((event.string_index + 0.5) * string_gap)
            radius = 15
            colour = self.theme.green if event.start_sec <= self.time < event.start_sec + event.duration_sec else self.theme.accent
            pygame.draw.circle(self.screen, colour, (x, y), radius)
            draw_text(
                self.screen,
                str(event.fret),
                self.fonts.small_bold,
                self.theme.text,
                (x, y),
                "center",
            )
        self.screen.set_clip(None)

    def _empty_state(self, rect: pygame.Rect, title: str, subtitle: str) -> None:
        draw_text(self.screen, title, self.fonts.subheading, self.theme.text, rect.center, "center")
        draw_text(
            self.screen,
            subtitle,
            self.fonts.body,
            self.theme.text_dim,
            (rect.centerx, rect.centery + 34),
            "center",
        )

    def _draw_controls(self, rect: pygame.Rect) -> None:
        draw_card(self.screen, rect, self.theme.panel)
        play_text = "PAUSE" if self.playing or self.counting_in else "PLAY"
        self._register_button(pygame.Rect(rect.left + 14, rect.top + 14, 84, 38), play_text, "play", True)
        self._register_button(pygame.Rect(rect.left + 106, rect.top + 14, 78, 38), "RESTART", "restart")

        speed_x = rect.left + 202
        for speed in (0.5, 0.75, 1.0, 1.25):
            button = pygame.Rect(speed_x, rect.top + 14, 58, 38)
            self._register_button(button, f"{speed:g}x", f"speed:{speed}", self.speed == speed)
            speed_x += 64

        self._register_button(
            pygame.Rect(speed_x + 10, rect.top + 14, 102, 38),
            "METRONOME",
            "metronome",
            self.metronome,
        )
        self._register_button(pygame.Rect(speed_x + 120, rect.top + 14, 62, 38), "SET A", "set_a")
        self._register_button(pygame.Rect(speed_x + 188, rect.top + 14, 62, 38), "SET B", "set_b")
        self._register_button(
            pygame.Rect(speed_x + 256, rect.top + 14, 68, 38), "LOOP", "loop", self.loop_enabled
        )

        status = "PLAYING" if self.playing else "COUNT-IN" if self.counting_in else "PAUSED"
        draw_text(self.screen, status, self.fonts.small_bold, self.theme.text, (rect.right - 106, rect.top + 26), "midleft")

        timeline = pygame.Rect(rect.left + 16, rect.bottom - 42, rect.width - 32, 15)
        self.timeline_rect = timeline
        pygame.draw.rect(self.screen, self.theme.panel_light, timeline, border_radius=8)
        progress = int(timeline.width * self.time / max(0.1, self.song_length))
        pygame.draw.rect(
            self.screen,
            self.theme.accent,
            pygame.Rect(timeline.left, timeline.top, progress, timeline.height),
            border_radius=8,
        )
        thumb_x = timeline.left + progress
        pygame.draw.circle(self.screen, self.theme.text, (thumb_x, timeline.centery), 8)
        self.actions.append((timeline.inflate(0, 16), "timeline"))

        time_text = f"{format_time(self.time)} / {format_time(self.song_length)}"
        draw_text(
            self.screen,
            time_text,
            self.fonts.small,
            self.theme.text_dim,
            (timeline.right, timeline.top - 8),
            "bottomright",
        )
        loop_text = f"LOOP {format_time(self.loop_a)} - {format_time(self.loop_b)}"
        draw_text(
            self.screen,
            loop_text,
            self.fonts.small,
            self.theme.text_dim,
            (timeline.left, timeline.top - 8),
            "bottomleft",
        )

    def _draw_count_in_overlay(self) -> None:
        width, height = self.screen.get_size()
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((5, 6, 10, 185))
        self.screen.blit(overlay, (0, 0))
        beat_length = self.beat_seconds() / self.speed
        beats_left = max(1, math.ceil(self.count_in_remaining / beat_length))
        number = ((beats_left - 1) % self.beats_per_bar) + 1
        draw_text(
            self.screen,
            str(number),
            pygame.font.SysFont("arial", 112, bold=True),
            self.theme.text,
            (width // 2, height // 2 - 18),
            "center",
        )
        draw_text(
            self.screen,
            "GET READY",
            self.fonts.subheading,
            self.theme.accent_light,
            (width // 2, height // 2 + 72),
            "center",
        )


class Visualiser:
    """Compatibility wrapper for older code that launched the visualiser directly."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("Launch JamSpot through main.py to use the complete multi-page app.")