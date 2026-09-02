import math
from typing import List, Optional, Tuple

import pygame

from .audio_player import MidiAudioPlayer
from .midi_mixer import build_mix
from .midi_parser import MidiParser
from .models import PracticeOptions
from .tab_converter import STANDARD_TUNING, TabEvent, build_guitar_tab, tab_statistics
from .ui import (
    BackgroundParticles,
    FontBook,
    Theme,
    draw_card,
    draw_glow_rect,
    draw_text,
    mix_colour,
)
from .utils import clamp, format_time, midi_note_to_name


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

        self.dragging_timeline = False
        self.resume_after_scrub = False

        self.animation_time = 0.0
        self.particles = BackgroundParticles(18)

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
            if self.timeline_rect and self.timeline_rect.inflate(0, 24).collidepoint(event.pos):
                self._start_scrub()
                self._scrub_to(event.pos[0])
                return None

            for rect, action in reversed(self.actions):
                if rect.collidepoint(event.pos):
                    return self._perform_action(action, event.pos)

        if event.type == pygame.MOUSEMOTION and self.dragging_timeline:
            self._scrub_to(event.pos[0])

        if (
            event.type == pygame.MOUSEBUTTONUP
            and event.button == 1
            and self.dragging_timeline
        ):
            self._finish_scrub()

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

        if was_playing:
            self.audio.start(self.time)
        else:
            self.audio.seek(self.time)

    def _start_scrub(self) -> None:
        self.resume_after_scrub = self.playing

        self.playing = False
        self.counting_in = False
        self.dragging_timeline = True

        self.audio.stop_all_notes()

    def _scrub_to(self, mouse_x: int) -> None:
        if not self.timeline_rect:
            return

        fraction = (mouse_x - self.timeline_rect.left) / self.timeline_rect.width
        fraction = clamp(fraction, 0.0, 1.0)

        self.time = fraction * self.song_length
        self.previous_time = self.time

    def _finish_scrub(self) -> None:
        self.dragging_timeline = False

        if self.resume_after_scrub:
            self.playing = True
            self.audio.start(self.time)
        else:
            self.audio.seek(self.time)

        self.resume_after_scrub = False

    def update(self, dt: float) -> None:
        self.animation_time += dt
        self.particles.update(dt)

        if self.dragging_timeline:
            return

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
        self.particles.draw(self.screen)

        header = pygame.Rect(18, 16, width - 36, 94)
        controls = pygame.Rect(18, height - 150, width - 36, 132)
        content = pygame.Rect(18, 124, width - 36, height - 292)

        self._draw_header(header)
        self._draw_content(content)
        self._draw_controls(controls)

        if self.counting_in:
            self._draw_count_in_overlay()

    def _register_button(
        self,
        rect: pygame.Rect,
        text: str,
        action: str,
        selected: bool = False,
        danger: bool = False,
    ) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())

        if danger:
            colour = self.theme.red
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

        if selected:
            draw_glow_rect(self.screen, rect, self.theme.accent, 10, 1)

        pygame.draw.rect(self.screen, colour, rect, border_radius=10)
        pygame.draw.rect(self.screen, border, rect, width=1, border_radius=10)

        text_colour = self.theme.accent_light if selected else self.theme.text

        draw_text(
            self.screen,
            text,
            self.fonts.small_bold,
            text_colour,
            rect.center,
            "center",
        )

        self.actions.append((rect, action))

    def _chip(self, x: int, y: int, text: str) -> int:
        rendered = self.fonts.small.render(text, True, self.theme.text_dim)

        rect = pygame.Rect(x, y, rendered.get_width() + 20, 27)

        pygame.draw.rect(self.screen, self.theme.panel_light, rect, border_radius=13)
        pygame.draw.rect(self.screen, self.theme.border, rect, width=1, border_radius=13)

        self.screen.blit(
            rendered,
            (rect.left + 10, rect.centery - rendered.get_height() // 2),
        )

        return rect.right + 8

    def _draw_header(self, rect: pygame.Rect) -> None:
        draw_card(
            self.screen,
            rect,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        self._register_button(
            pygame.Rect(rect.left + 14, rect.top + 15, 68, 34),
            "BACK",
            "back",
        )

        draw_text(
            self.screen,
            self.title,
            self.fonts.heading,
            self.theme.text,
            (rect.left + 100, rect.top + 13),
        )

        draw_text(
            self.screen,
            self.part["name"],
            self.fonts.small,
            self.theme.text_dim,
            (rect.left + 102, rect.top + 50),
        )

        mode_x = rect.right - 322

        for label, mode in (
            ("PIANO", "piano"),
            ("DRUMS", "drums"),
            ("TAB", "guitar"),
        ):
            button_rect = pygame.Rect(mode_x, rect.top + 14, 94, 34)
            self._register_button(button_rect, label, f"mode:{mode}", self.mode == mode)
            mode_x += 102

        chip_x = rect.right - 322
        chip_y = rect.top + 56

        chip_x = self._chip(chip_x, chip_y, f"{self.bpm:.0f} BPM")
        chip_x = self._chip(chip_x, chip_y, self.time_signature)
        self._chip(chip_x, chip_y, f"KEY {self.key_signature}")

    def _draw_content(self, rect: pygame.Rect) -> None:
        draw_card(
            self.screen,
            rect,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        inner = rect.inflate(-24, -24)

        if self.mode == "drums":
            self._draw_drums(inner)
        elif self.mode == "guitar":
            self._draw_guitar(inner)
        else:
            self._draw_piano(inner)

    def _time_geometry(self, rect: pygame.Rect):
        playline = rect.left + int(rect.width * 0.29)

        seconds_right = clamp(
            self.bar_seconds() * 1.35,
            3.0,
            5.5,
        )

        pixels_per_second = (rect.right - playline - 18) / seconds_right

        visible_start = self.time - (playline - rect.left) / pixels_per_second
        visible_end = self.time + seconds_right

        return playline, pixels_per_second, visible_start, visible_end

    def _draw_time_grid(
        self,
        rect: pygame.Rect,
        playline: int,
        pixels_per_second: float,
    ) -> None:
        beat = self.beat_seconds()
        bar = self.bar_seconds()

        visible_start = self.time - (playline - rect.left) / pixels_per_second
        visible_end = self.time + (rect.right - playline) / pixels_per_second

        beat_number = math.floor(visible_start / beat)

        while beat_number * beat <= visible_end + beat:
            beat_time = beat_number * beat
            x = playline + int((beat_time - self.time) * pixels_per_second)

            if rect.left <= x <= rect.right:
                is_bar = beat_number % self.beats_per_bar == 0

                colour = self.theme.grid_strong if is_bar else self.theme.grid

                pygame.draw.line(
                    self.screen,
                    colour,
                    (x, rect.top),
                    (x, rect.bottom),
                    2 if is_bar else 1,
                )

                if is_bar:
                    bar_number = int(beat_time / max(0.01, bar)) + 1

                    draw_text(
                        self.screen,
                        str(max(1, bar_number)),
                        self.fonts.tiny,
                        self.theme.text_dark,
                        (x + 5, rect.top + 5),
                    )

            beat_number += 1

        pygame.draw.line(
            self.screen,
            self.theme.accent_light,
            (playline, rect.top),
            (playline, rect.bottom),
            3,
        )

    def _active_pitches(self):
        active = set()

        for note in self.notes:
            if note.channel == 9:
                continue

            if note.start_sec > self.time:
                break

            if note.start_sec <= self.time < note.start_sec + note.duration_sec:
                active.add(note.pitch)

        return active

    def _draw_piano(self, rect: pygame.Rect) -> None:
        pitched = [note for note in self.notes if note.channel != 9]

        if not pitched:
            self._empty_state(
                rect,
                "This selected part has no pitched notes.",
                "Try the DRUMS view.",
            )
            return

        keyboard_width = 104

        keyboard = pygame.Rect(
            rect.left,
            rect.top,
            keyboard_width,
            rect.height,
        )

        roll = pygame.Rect(
            keyboard.right + 8,
            rect.top,
            rect.width - keyboard_width - 8,
            rect.height,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.roll_background,
            roll,
            border_radius=12,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.border,
            roll,
            width=1,
            border_radius=12,
        )

        playline, pps, start, end = self._time_geometry(roll)

        low = max(0, min(note.pitch for note in pitched) - 1)
        high = min(127, max(note.pitch for note in pitched) + 1)

        pitch_count = high - low + 1
        row_height = roll.height / max(1, pitch_count)

        active_pitches = self._active_pitches()
        black_notes = {1, 3, 6, 8, 10}

        for pitch in range(low, high + 1):
            distance = pitch - low

            centre_y = roll.bottom - (distance + 0.5) * row_height
            top_y = int(centre_y - row_height / 2)

            key_height = max(2, int(math.ceil(row_height)))

            is_black = pitch % 12 in black_notes
            active = pitch in active_pitches

            if active:
                key_colour = self.theme.accent_light
            elif is_black:
                key_colour = self.theme.piano_black
            else:
                key_colour = self.theme.piano_white

            key_width = int(keyboard.width * 0.62) if is_black else keyboard.width

            key_rect = pygame.Rect(
                keyboard.right - key_width,
                top_y,
                key_width,
                key_height,
            )

            if active:
                key_rect.x += 4
                key_rect.width = max(2, key_rect.width - 4)
                draw_glow_rect(self.screen, key_rect, self.theme.accent_light, 3, 1)

            pygame.draw.rect(
                self.screen,
                key_colour,
                key_rect,
                border_radius=2,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.accent if active else (8, 10, 15),
                key_rect,
                width=1,
                border_radius=2,
            )

            if row_height >= 13 and (pitch % 12 == 0 or active):
                label_colour = (
                    (10, 12, 18)
                    if not is_black or active
                    else self.theme.text
                )

                draw_text(
                    self.screen,
                    midi_note_to_name(pitch),
                    self.fonts.tiny_bold,
                    label_colour,
                    (key_rect.left + 7, key_rect.centery),
                    "midleft",
                )

            lane_y = int(centre_y + row_height / 2)

            pygame.draw.line(
                self.screen,
                self.theme.grid,
                (roll.left, lane_y),
                (roll.right, lane_y),
                1,
            )

        self._draw_time_grid(roll, playline, pps)

        self.screen.set_clip(roll)

        for note in pitched:
            if note.start_sec > end:
                break

            if note.start_sec + note.duration_sec < start:
                continue

            distance = note.pitch - low
            y = roll.bottom - (distance + 0.5) * row_height

            x = playline + int((note.start_sec - self.time) * pps)

            note_width = max(56, int(note.duration_sec * pps))

            note_height = max(
                16,
                min(28, int(row_height * 0.82)),
            )

            note_rect = pygame.Rect(
                x,
                int(y - note_height / 2),
                note_width,
                note_height,
            )

            active = (
                note.start_sec
                <= self.time
                < note.start_sec + note.duration_sec
            )

            if active:
                colour = self.theme.accent_light

                draw_glow_rect(
                    self.screen,
                    note_rect,
                    self.theme.accent_light,
                    7,
                    1,
                )

            elif note.start_sec < self.time:
                colour = mix_colour(
                    self.theme.panel_light,
                    self.theme.accent,
                    0.45,
                )

            else:
                colour = self.theme.accent

            pygame.draw.rect(
                self.screen,
                colour,
                note_rect,
                border_radius=7,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.accent_light if active else self.theme.accent_soft,
                note_rect,
                width=1,
                border_radius=7,
            )

            if note_rect.width >= 46:
                font = (
                    self.fonts.note
                    if note_rect.width >= 68 and note_rect.height >= 20
                    else self.fonts.small_bold
                )

                label = note.label

                if font.size(label)[0] > note_rect.width - 10 and len(label) > 1:
                    label = label[:-1]

                draw_text(
                    self.screen,
                    label,
                    font,
                    self.theme.text,
                    note_rect.center,
                    "center",
                )

        self.screen.set_clip(None)

    def _drum_row(self, pitch: int) -> int:
        for index, (_, pitches) in enumerate(self.DRUM_ROWS[:-1]):
            if pitch in pitches:
                return index

        return len(self.DRUM_ROWS) - 1

    def _active_drum_rows(self):
        active = set()

        for note in self.notes:
            if note.channel != 9:
                continue

            if note.start_sec > self.time:
                break

            hit_length = max(0.11, note.duration_sec)

            if note.start_sec <= self.time < note.start_sec + hit_length:
                active.add(self._drum_row(note.pitch))

        return active

    def _draw_drums(self, rect: pygame.Rect) -> None:
        drum_notes = [note for note in self.notes if note.channel == 9]

        if not drum_notes:
            self._empty_state(
                rect,
                "This selected part is not a drum track.",
                "Choose a drum part or use PIANO/TAB.",
            )
            return

        label_width = 126

        roll = pygame.Rect(
            rect.left + label_width,
            rect.top,
            rect.width - label_width,
            rect.height,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.roll_background,
            roll,
            border_radius=12,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.border,
            roll,
            width=1,
            border_radius=12,
        )

        row_height = roll.height / len(self.DRUM_ROWS)

        playline, pps, start, end = self._time_geometry(roll)
        active_rows = self._active_drum_rows()

        for index, (name, _) in enumerate(self.DRUM_ROWS):
            y = roll.top + int(index * row_height)

            lane = pygame.Rect(
                roll.left,
                y,
                roll.width,
                math.ceil(row_height),
            )

            if index % 2 == 0:
                pygame.draw.rect(
                    self.screen,
                    (13, 17, 25),
                    lane,
                )

            pygame.draw.line(
                self.screen,
                self.theme.grid,
                (roll.left, lane.bottom),
                (roll.right, lane.bottom),
                1,
            )

            centre_y = int(y + row_height / 2)

            pad_rect = pygame.Rect(
                rect.left + 5,
                centre_y - 18,
                label_width - 15,
                36,
            )

            active = index in active_rows

            pad_colour = (
                self.theme.panel_selected
                if active
                else self.theme.panel_light
            )

            if active:
                draw_glow_rect(
                    self.screen,
                    pad_rect,
                    self.theme.orange,
                    9,
                    1,
                )

            pygame.draw.rect(
                self.screen,
                pad_colour,
                pad_rect,
                border_radius=9,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.orange if active else self.theme.border,
                pad_rect,
                width=1,
                border_radius=9,
            )

            dot_radius = 6 if active else 4

            if active:
                pulse = int((math.sin(self.animation_time * 11) + 1) * 1.2)
                dot_radius += pulse

            pygame.draw.circle(
                self.screen,
                self.theme.orange if active else self.theme.text_dark,
                (pad_rect.left + 17, pad_rect.centery),
                dot_radius,
            )

            draw_text(
                self.screen,
                name.upper(),
                self.fonts.small_bold,
                self.theme.text if active else self.theme.text_dim,
                (pad_rect.left + 33, pad_rect.centery),
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

            active = (
                note.start_sec
                <= self.time
                < note.start_sec + max(0.11, note.duration_sec)
            )

            if active:
                pygame.draw.circle(
                    self.screen,
                    mix_colour(self.theme.orange, self.theme.text, 0.25),
                    (x, y),
                    radius + 5,
                )

            colour = (
                self.theme.orange
                if active
                else mix_colour(self.theme.accent, self.theme.orange, 0.55)
            )

            pygame.draw.circle(
                self.screen,
                colour,
                (x, y),
                radius,
            )

            pygame.draw.circle(
                self.screen,
                self.theme.text,
                (x, y),
                radius,
                1,
            )

        self.screen.set_clip(None)

    def _draw_guitar(self, rect: pygame.Rect) -> None:
        if not self.tab_events:
            self._empty_state(
                rect,
                "No guitar tablature can be made from this part.",
                "Choose a pitched instrument part.",
            )
            return

        label_width = 52

        roll = pygame.Rect(
            rect.left + label_width,
            rect.top + 35,
            rect.width - label_width,
            rect.height - 55,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.roll_background,
            roll,
            border_radius=12,
        )

        pygame.draw.rect(
            self.screen,
            self.theme.border,
            roll,
            width=1,
            border_radius=12,
        )

        playline, pps, start, end = self._time_geometry(roll)

        string_gap = roll.height / 6
        stats = tab_statistics(self.tab_events)

        draw_text(
            self.screen,
            (
                f"STANDARD TUNING   "
                f"{stats['mapped']} NOTES MAPPED   "
                f"HIGHEST FRET {stats['highest_fret']}"
            ),
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

            line_colour = (
                (106, 113, 129)
                if index < 3
                else (154, 158, 168)
            )

            pygame.draw.line(
                self.screen,
                line_colour,
                (roll.left, y),
                (roll.right, y),
                1 + index // 3,
            )

        self._draw_time_grid(roll, playline, pps)

        self.screen.set_clip(roll)

        for event in self.tab_events:
            if event.start_sec > end:
                break

            if (
                event.start_sec + event.duration_sec < start
                or event.string_index < 0
            ):
                continue

            x = playline + int((event.start_sec - self.time) * pps)
            y = roll.top + int((event.string_index + 0.5) * string_gap)

            active = (
                event.start_sec
                <= self.time
                < event.start_sec + event.duration_sec
            )

            radius = 17 if active else 15
            colour = self.theme.green if active else self.theme.accent

            if active:
                pygame.draw.circle(
                    self.screen,
                    mix_colour(
                        self.theme.roll_background,
                        self.theme.green,
                        0.4,
                    ),
                    (x, y),
                    radius + 6,
                )

            pygame.draw.circle(
                self.screen,
                colour,
                (x, y),
                radius,
            )

            draw_text(
                self.screen,
                str(event.fret),
                self.fonts.small_bold,
                self.theme.text,
                (x, y),
                "center",
            )

        self.screen.set_clip(None)

    def _empty_state(
        self,
        rect: pygame.Rect,
        title: str,
        subtitle: str,
    ) -> None:
        draw_text(
            self.screen,
            title,
            self.fonts.subheading,
            self.theme.text,
            (rect.centerx, rect.centery - 12),
            "center",
        )

        draw_text(
            self.screen,
            subtitle,
            self.fonts.body,
            self.theme.text_dim,
            (rect.centerx, rect.centery + 25),
            "center",
        )

    def _draw_controls(self, rect: pygame.Rect) -> None:
        draw_card(
            self.screen,
            rect,
            self.theme.panel,
            border_colour=self.theme.border,
        )

        play_text = "PAUSE" if self.playing or self.counting_in else "PLAY"

        self._register_button(
            pygame.Rect(rect.left + 14, rect.top + 14, 82, 38),
            play_text,
            "play",
            True,
        )

        self._register_button(
            pygame.Rect(rect.left + 103, rect.top + 14, 80, 38),
            "RESTART",
            "restart",
        )

        speed_x = rect.left + 198

        for speed in (0.5, 0.75, 1.0, 1.25):
            button = pygame.Rect(speed_x, rect.top + 14, 56, 38)

            self._register_button(
                button,
                f"{speed:g}x",
                f"speed:{speed}",
                self.speed == speed,
            )

            speed_x += 62

        self._register_button(
            pygame.Rect(speed_x + 5, rect.top + 14, 100, 38),
            "METRONOME",
            "metronome",
            self.metronome,
        )

        self._register_button(
            pygame.Rect(speed_x + 112, rect.top + 14, 56, 38),
            "SET A",
            "set_a",
        )

        self._register_button(
            pygame.Rect(speed_x + 174, rect.top + 14, 56, 38),
            "SET B",
            "set_b",
        )

        self._register_button(
            pygame.Rect(speed_x + 236, rect.top + 14, 64, 38),
            "LOOP",
            "loop",
            self.loop_enabled,
        )

        if self.dragging_timeline:
            status = "SEEKING"
        elif self.counting_in:
            status = "COUNT-IN"
        elif self.playing:
            status = "PLAYING"
        else:
            status = "PAUSED"

        if self.counting_in:
            status_colour = self.theme.orange
        elif self.playing:
            status_colour = self.theme.green
        else:
            status_colour = self.theme.text_dim

        status_x = rect.right - 105

        pygame.draw.circle(
            self.screen,
            status_colour,
            (status_x, rect.top + 33),
            4,
        )

        draw_text(
            self.screen,
            status,
            self.fonts.small_bold,
            status_colour,
            (status_x + 10, rect.top + 33),
            "midleft",
        )

        timeline = pygame.Rect(
            rect.left + 16,
            rect.bottom - 43,
            rect.width - 32,
            13,
        )

        self.timeline_rect = timeline

        pygame.draw.rect(
            self.screen,
            self.theme.panel_light,
            timeline,
            border_radius=7,
        )

        loop_start = int(
            timeline.width
            * self.loop_a
            / max(0.1, self.song_length)
        )

        loop_end = int(
            timeline.width
            * self.loop_b
            / max(0.1, self.song_length)
        )

        if loop_end > loop_start:
            loop_rect = pygame.Rect(
                timeline.left + loop_start,
                timeline.top,
                loop_end - loop_start,
                timeline.height,
            )

            loop_colour = mix_colour(
                self.theme.panel_light,
                self.theme.green,
                0.48 if self.loop_enabled else 0.22,
            )

            pygame.draw.rect(
                self.screen,
                loop_colour,
                loop_rect,
                border_radius=7,
            )

        progress = int(
            timeline.width
            * self.time
            / max(0.1, self.song_length)
        )

        if progress > 0:
            pygame.draw.rect(
                self.screen,
                self.theme.accent,
                pygame.Rect(
                    timeline.left,
                    timeline.top,
                    progress,
                    timeline.height,
                ),
                border_radius=7,
            )

        thumb_x = timeline.left + progress

        if self.dragging_timeline:
            pygame.draw.circle(
                self.screen,
                mix_colour(
                    self.theme.panel,
                    self.theme.accent_light,
                    0.45,
                ),
                (thumb_x, timeline.centery),
                13,
            )

        pygame.draw.circle(
            self.screen,
            self.theme.text,
            (thumb_x, timeline.centery),
            7,
        )

        if self.loop_a > 0 or self.loop_enabled:
            a_x = timeline.left + loop_start

            pygame.draw.line(
                self.screen,
                self.theme.green,
                (a_x, timeline.top - 4),
                (a_x, timeline.bottom + 4),
                2,
            )

        if self.loop_b < self.song_length or self.loop_enabled:
            b_x = timeline.left + loop_end

            pygame.draw.line(
                self.screen,
                self.theme.green,
                (b_x, timeline.top - 4),
                (b_x, timeline.bottom + 4),
                2,
            )

        self.actions.append(
            (
                timeline.inflate(0, 18),
                "timeline",
            )
        )

        draw_text(
            self.screen,
            f"{format_time(self.time)} / {format_time(self.song_length)}",
            self.fonts.small,
            self.theme.text_dim,
            (timeline.right, timeline.top - 8),
            "bottomright",
        )

        draw_text(
            self.screen,
            f"LOOP {format_time(self.loop_a)} - {format_time(self.loop_b)}",
            self.fonts.small,
            self.theme.green if self.loop_enabled else self.theme.text_dim,
            (timeline.left, timeline.top - 8),
            "bottomleft",
        )

        if self.dragging_timeline:
            time_box = pygame.Rect(
                thumb_x - 31,
                timeline.top - 36,
                62,
                25,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.panel_selected,
                time_box,
                border_radius=7,
            )

            pygame.draw.rect(
                self.screen,
                self.theme.accent,
                time_box,
                width=1,
                border_radius=7,
            )

            draw_text(
                self.screen,
                format_time(self.time),
                self.fonts.small_bold,
                self.theme.text,
                time_box.center,
                "center",
            )

    def _draw_count_in_overlay(self) -> None:
        width, height = self.screen.get_size()

        overlay = pygame.Surface(
            (width, height),
            pygame.SRCALPHA,
        )

        overlay.fill((4, 6, 10, 205))
        self.screen.blit(overlay, (0, 0))

        beat_length = self.beat_seconds() / self.speed

        beats_left = max(
            1,
            math.ceil(
                self.count_in_remaining
                / beat_length
            ),
        )

        number = ((beats_left - 1) % self.beats_per_bar) + 1

        centre = (
            width // 2,
            height // 2 - 20,
        )

        pulse = (math.sin(self.animation_time * 8) + 1) / 2
        radius = int(73 + pulse * 7)

        pygame.draw.circle(
            self.screen,
            mix_colour(
                self.theme.background,
                self.theme.accent,
                0.32,
            ),
            centre,
            radius,
            2,
        )

        pygame.draw.circle(
            self.screen,
            self.theme.accent,
            centre,
            radius - 10,
            1,
        )

        count_font = pygame.font.SysFont(
            "bahnschrift",
            105,
            bold=True,
        )

        draw_text(
            self.screen,
            str(number),
            count_font,
            self.theme.text,
            centre,
            "center",
        )

        draw_text(
            self.screen,
            "GET READY",
            self.fonts.subheading,
            self.theme.accent_light,
            (width // 2, height // 2 + 78),
            "center",
        )


class Visualiser:
    """Compatibility wrapper for older code that launched the visualiser directly."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "Launch JamSpot through main.py to use the complete multi-page app."
        )