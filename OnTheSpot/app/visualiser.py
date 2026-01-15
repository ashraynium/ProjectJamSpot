import os
import pygame
import math
from typing import Dict, List, Tuple, Optional

from .models import NoteEvent
from .utils import clamp
from .audio_player import MidiAudioPlayer


# ============================================================
# Pygame visualiser (modern layout + scrolling notes)
# Hybrid: window-clipping + playline "hit" effects (Synthesia-ish)
# ============================================================

class Visualiser:
    def __init__(self, title: str, part_name: str, bpm: float, time_sig: str, key_sig: str,
                 notes: List[NoteEvent], song_length: float, programs_by_channel: Dict[int, int]):

        # Step 1: Init pygame
        pygame.init()
        pygame.display.set_caption("JamSpot")
        self.screen = pygame.display.set_mode((1280, 760), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()

        # Step 2: Store display data
        self.title = title
        self.part_name = part_name
        self.bpm = bpm
        self.time_sig = time_sig
        self.key_sig = key_sig
        self.notes = notes
        self.song_length = song_length

        # Step 3: Parse time signature (beat alignment)
        self.ts_num = 4
        self.ts_den = 4
        try:
            a, b = self.time_sig.split("/")
            self.ts_num = int(a)
            self.ts_den = int(b)
            if self.ts_den <= 0:
                self.ts_den = 4
            if self.ts_num <= 0:
                self.ts_num = 4
        except:
            self.ts_num, self.ts_den = 4, 4

        # Step 4: Playback state
        self.time = 0.0
        self.playing = False
        self.prev_time = 0.0  # used for hit FX triggering

        # Step 5: Layout constants
        self.top_h = 86
        self.bottom_h = 96
        self.playline_ratio = 0.30  # slightly left = more "play space"
        self.lookahead_bars = 2

        # Step 6: Theme
        self.COL_BG = (9, 11, 16)
        self.COL_PANEL = (18, 22, 32)
        self.COL_PANEL_2 = (24, 30, 42)

        self.COL_TEXT = (235, 236, 240)
        self.COL_TEXT_DIM = (165, 170, 182)

        self.COL_BEAT = (40, 48, 62)
        self.COL_BAR = (70, 85, 110)
        self.COL_PLAYLINE = (245, 245, 245)

        # Step 7: Fonts (tries assets/fonts; falls back)
        self.font_ui = self._load_font("assets/fonts/ZalandoSansExpanded-Bold.ttf", 22, fallback=("arial", 22, True))
        self.font_ui_small = self._load_font("assets/fonts/ZalandoSansExpanded-Bold.ttf", 16, fallback=("arial", 16, True))
        self.font_mono = self._load_font("assets/fonts/RobotoMono-Regular.ttf", 16, fallback=("consolas", 16, False))
        self.font_mono_small = self._load_font("assets/fonts/RobotoMono-Regular.ttf", 14, fallback=("consolas", 14, False))

        # Step 8: Keep ALL notes for audio (drums can be tiny)
        self.audio_notes = list(self.notes)
        self.audio_notes.sort(key=lambda n: n.start_sec)

        # Step 9: Filter tiny notes for drawing only (audio keeps everything)
        self.min_note_seconds = 0.05
        self.draw_notes = [n for n in self.notes if n.duration_sec >= self.min_note_seconds]
        self.draw_notes.sort(key=lambda n: n.start_sec)

        # Step 10: Detect drums + pitched
        self.has_drums = any(n.channel == 9 for n in self.audio_notes)
        self.has_pitched = any(n.channel != 9 for n in self.audio_notes)

        # Step 11: Channel colors (multi-track)
        self.channel_colors = self._build_channel_color_map(self.audio_notes)

        # Step 12: Drum rows (kit lanes)
        self.drum_rows = self._drum_rows()
        self.drum_pitch_to_row = self._build_drum_pitch_map(self.drum_rows)

        # Step 13: Shared label width (keeps drum/piano grids aligned)
        self.roll_label_w = 150

        # Step 14: Hit effects (Synthesia-ish, subtle)
        # Each FX: {"t0": float, "kind": "piano"/"drum", "pitch": int, "channel": int, "color": (r,g,b)}
        self.hit_fx: List[dict] = []

        # Step 15: Note-on scan for FX (efficient triggering)
        self.fx_scan_index = 0
        self._resync_fx_index(self.time)

        # Step 16: Precompute a stable piano pitch range (so FX Y mapping stays consistent)
        self.piano_lo, self.piano_hi = self._compute_piano_pitch_range()

        # Step 17: Audio
        self.audio = MidiAudioPlayer(programs_by_channel)
        self.audio.load_notes(self.audio_notes)

    # --------------------------
    # Project root + fonts
    # --------------------------
    def _project_root(self) -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.abspath(os.path.join(here, ".."))

    def _load_font(self, rel_path: str, size: int, fallback=("arial", 16, False)):
        root = self._project_root()
        path = os.path.join(root, rel_path.replace("/", os.sep))
        if os.path.exists(path):
            try:
                return pygame.font.Font(path, size)
            except:
                pass
        name, _, bold = fallback
        return pygame.font.SysFont(name, size, bold=bold)

    # --------------------------
    # Small UI helpers
    # --------------------------
    def _card(self, rect: pygame.Rect, color, radius=16):
        shadow = rect.move(0, 5)
        pygame.draw.rect(self.screen, (0, 0, 0), shadow, border_radius=radius)
        pygame.draw.rect(self.screen, color, rect, border_radius=radius)

    def _chip(self, x: int, y: int, text: str, col_bg=None):
        col_bg = col_bg if col_bg else self.COL_PANEL_2
        pad_x, pad_y = 12, 7
        surf = self.font_mono_small.render(text, True, self.COL_TEXT_DIM)
        w, h = surf.get_size()
        r = pygame.Rect(x, y, w + pad_x * 2, h + pad_y * 2)
        pygame.draw.rect(self.screen, col_bg, r, border_radius=999)
        self.screen.blit(surf, (x + pad_x, y + pad_y))
        return r.right + 10

    def _fmt_time(self, t: float) -> str:
        m = int(t // 60)
        s = int(t % 60)
        return f"{m:02d}:{s:02d}"

    # --------------------------
    # Timing helpers
    # --------------------------
    def beat_seconds(self) -> float:
        quarter = 60.0 / max(1.0, self.bpm)
        return quarter * (4.0 / float(self.ts_den))

    def bar_seconds(self) -> float:
        return float(self.ts_num) * self.beat_seconds()

    def lookahead_seconds(self) -> float:
        return self.lookahead_bars * self.bar_seconds()

    def pixels_per_second(self, roll_width: int) -> float:
        # roll_width is the actual piano/drum roll width (excluding label strip)
        playline_local = int(roll_width * self.playline_ratio)
        visible_right = max(260, roll_width - playline_local - 24)
        return visible_right / max(0.1, self.lookahead_seconds())

    # --------------------------
    # Color + mapping
    # --------------------------
    def _build_channel_color_map(self, notes: List[NoteEvent]) -> Dict[int, Tuple[int, int, int]]:
        palette = [
            (125, 95, 210),
            (90, 180, 160),
            (220, 140, 90),
            (90, 140, 220),
            (220, 90, 140),
            (160, 200, 90),
            (200, 120, 220),
            (120, 200, 240),
            (240, 200, 120),
            (160, 140, 220),
            (120, 220, 170),
            (220, 160, 120),
        ]

        used = sorted(set(n.channel for n in notes if n.channel != 9))
        cmap: Dict[int, Tuple[int, int, int]] = {}
        for i, ch in enumerate(used):
            cmap[ch] = palette[i % len(palette)]

        # Drums get a neutral base
        cmap[9] = (210, 210, 215)
        return cmap

    def _drum_rows(self) -> List[Tuple[str, List[int]]]:
        # Rows are "music inspired" but physically interpreted on the left
        return [
            ("Kick",  [35, 36]),
            ("Snare", [38, 40]),
            ("Clap",  [39]),
            ("HiHat", [42, 44, 46]),
            ("Tom",   [41, 43, 45, 47, 48, 50]),
            ("Crash", [49, 57]),
            ("Ride",  [51, 59]),
            ("Perc",  [52, 53, 54, 55, 56, 58, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81]),
        ]

    def _build_drum_pitch_map(self, rows: List[Tuple[str, List[int]]]) -> Dict[int, int]:
        pitch_to_row: Dict[int, int] = {}
        for idx, (_, pitches) in enumerate(rows):
            for p in pitches:
                pitch_to_row[p] = idx
        return pitch_to_row

    def _compute_piano_pitch_range(self) -> Tuple[int, int]:
        pitched = [n for n in self.audio_notes if n.channel != 9]
        if not pitched:
            return 40, 80
        lo = min(n.pitch for n in pitched)
        hi = max(n.pitch for n in pitched)
        if hi == lo:
            hi += 1
        return lo, hi

    # --------------------------
    # Window-clipping draw helpers (no fade)
    # --------------------------
    def _blit_clipped(self, surf: pygame.Surface, dest_rect: pygame.Rect, clip_rect: pygame.Rect):
        # Step 1: Work out visible portion
        vis = dest_rect.clip(clip_rect)
        if vis.width <= 0 or vis.height <= 0:
            return

        # Step 2: Source slice for that visible portion
        src_x = vis.left - dest_rect.left
        src_y = vis.top - dest_rect.top
        src_area = pygame.Rect(src_x, src_y, vis.width, vis.height)

        # Step 3: Blit only the visible slice (smooth "walk off")
        self.screen.blit(surf, vis.topleft, area=src_area)

    def _make_note_surface(self, w: int, h: int, fill_rgb: Tuple[int, int, int], edge_rgb: Tuple[int, int, int], label_text: str):
        # Step 1: Surface
        s = pygame.Surface((w, h), pygame.SRCALPHA)

        # Step 2: Body + border
        pygame.draw.rect(s, fill_rgb, s.get_rect(), border_radius=10)
        pygame.draw.rect(s, edge_rgb, s.get_rect(), width=2, border_radius=10)

        # Step 3: Highlight strip
        hi = pygame.Rect(2, 2, max(0, w - 4), 6)
        pygame.draw.rect(s, (255, 255, 255, 110), hi, border_radius=6)

        # Step 4: Label
        txt = self.font_mono_small.render(label_text, True, self.COL_TEXT)
        s.blit(txt, (8, 6))

        return s

    # --------------------------
    # Soft overlap helper (less harsh on the eye)
    # --------------------------
    def _draw_soft_overlap(self, inter: pygame.Rect, col_a: Tuple[int, int, int], col_b: Tuple[int, int, int]):
        # Step 1: Soft blended overlay
        blend = ((col_a[0] + col_b[0]) // 2, (col_a[1] + col_b[1]) // 2, (col_a[2] + col_b[2]) // 2)

        surf = pygame.Surface((inter.width, inter.height), pygame.SRCALPHA)
        surf.fill((blend[0], blend[1], blend[2], 95))

        # Step 2: Subtle diagonals (very light)
        step = 12
        for i in range(-inter.height, inter.width, step):
            pygame.draw.line(surf, (255, 255, 255, 28), (i, 0), (i + inter.height, inter.height), 2)

        self.screen.blit(surf, inter.topleft)

    # --------------------------
    # Hit FX (Synthesia-ish, subtle)
    # --------------------------
    def _resync_fx_index(self, t: float):
        # Step 1: Advance scan index to first note that hasn't started yet
        self.fx_scan_index = 0
        while self.fx_scan_index < len(self.audio_notes) and self.audio_notes[self.fx_scan_index].start_sec < t:
            self.fx_scan_index += 1

    def _spawn_hit_fx_for_note(self, n: NoteEvent):
        # Step 1: Choose kind + color
        kind = "drum" if n.channel == 9 else "piano"
        base = self.channel_colors.get(n.channel, (125, 95, 210))

        # Step 2: Slight boost for the flash
        col = (min(255, base[0] + 35), min(255, base[1] + 35), min(255, base[2] + 35))

        self.hit_fx.append({
            "t0": self.time,
            "kind": kind,
            "pitch": n.pitch,
            "channel": n.channel,
            "color": col
        })

    def _update_hit_fx(self, dt: float):
        # Step 1: Remove old FX (short lifespan)
        life = 0.18
        now = self.time
        self.hit_fx = [fx for fx in self.hit_fx if (now - fx["t0"]) <= life]

    def _draw_hit_fx(self, fx: dict, playline_x: int, y: int):
        # Step 1: FX time progress
        t = self.time - fx["t0"]
        life = 0.18
        if t < 0 or t > life:
            return

        # Step 2: Progress 0..1
        p = t / life

        # Step 3: Pulse ring
        radius = int(6 + p * 26)
        alpha = int(160 * (1.0 - p))

        ring = pygame.Surface((radius * 2 + 2, radius * 2 + 2), pygame.SRCALPHA)
        pygame.draw.circle(ring, (fx["color"][0], fx["color"][1], fx["color"][2], alpha),
                           (radius + 1, radius + 1), radius, width=2)

        self.screen.blit(ring, (playline_x - radius - 1, y - radius - 1))

        # Step 4: Tiny vertical glow tick at playline
        glow_h = 34
        glow_w = 10
        glow = pygame.Surface((glow_w, glow_h), pygame.SRCALPHA)
        pygame.draw.rect(glow, (fx["color"][0], fx["color"][1], fx["color"][2], int(110 * (1.0 - p))),
                         glow.get_rect(), border_radius=8)
        self.screen.blit(glow, (playline_x - glow_w // 2, y - glow_h // 2))

    # --------------------------
    # Main loop
    # --------------------------
    def run(self):
        try:
            while True:
                dt = self.clock.tick(60) / 1000.0
                self._handle_events()
                self._update(dt)
                self._draw()
                pygame.display.flip()
        finally:
            self.audio.close()

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                raise SystemExit

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    raise SystemExit

                # SPACE = play/pause
                if event.key == pygame.K_SPACE:
                    self.playing = not self.playing
                    if self.playing:
                        self.prev_time = self.time
                        self.audio.start(self.time)
                        self._resync_fx_index(self.time)
                    else:
                        self.audio.stop_all_notes()

                # Left/Right seek (pauses)
                if event.key == pygame.K_LEFT:
                    self.time = clamp(self.time - 2.0, 0.0, self.song_length)
                    self.prev_time = self.time
                    self.playing = False
                    self.audio.seek(self.time)
                    self._resync_fx_index(self.time)

                if event.key == pygame.K_RIGHT:
                    self.time = clamp(self.time + 2.0, 0.0, self.song_length)
                    self.prev_time = self.time
                    self.playing = False
                    self.audio.seek(self.time)
                    self._resync_fx_index(self.time)

                # R restart
                if event.key == pygame.K_r:
                    self.time = 0.0
                    self.prev_time = 0.0
                    self.playing = False
                    self.audio.seek(self.time)
                    self._resync_fx_index(self.time)

    def _update(self, dt: float):
        # Step 1: Update time + audio when playing
        if self.playing:
            self.prev_time = self.time
            self.time += dt

            if self.time >= self.song_length:
                self.time = self.song_length
                self.playing = False
                self.audio.stop_all_notes()

            self.audio.update(self.time)

            # Step 2: Trigger hit FX for note-ons crossed this frame (efficient scan)
            while self.fx_scan_index < len(self.audio_notes):
                n = self.audio_notes[self.fx_scan_index]
                if n.start_sec <= self.time and n.start_sec > self.prev_time:
                    self._spawn_hit_fx_for_note(n)
                    self.fx_scan_index += 1
                elif n.start_sec <= self.prev_time:
                    self.fx_scan_index += 1
                else:
                    break

        # Step 3: Update FX lifetimes (even when paused, it looks fine)
        self._update_hit_fx(dt)

    # --------------------------
    # Draw
    # --------------------------
    def _draw(self):
        w, h = self.screen.get_size()

        top_rect = pygame.Rect(16, 14, w - 32, self.top_h)
        mid_rect = pygame.Rect(16, 14 + self.top_h + 12, w - 32, h - self.top_h - self.bottom_h - 40)
        bot_rect = pygame.Rect(16, h - self.bottom_h - 14, w - 32, self.bottom_h)

        self.screen.fill(self.COL_BG)
        self._draw_top(top_rect)
        self._draw_mid(mid_rect)
        self._draw_bottom(bot_rect)

    def _draw_top(self, r: pygame.Rect):
        self._card(r, self.COL_PANEL)

        title_surf = self.font_ui.render("JAMSPOT", True, self.COL_TEXT)
        self.screen.blit(title_surf, (r.left + 16, r.top + 16))

        sub = self.font_mono.render(self.title, True, self.COL_TEXT_DIM)
        self.screen.blit(sub, (r.left + 16, r.top + 46))

        x = r.left + 420
        x = self._chip(x, r.top + 18, f"PART: {self.part_name}")
        x = self._chip(x, r.top + 18, f"KEY: {self.key_sig}")
        x = self._chip(x, r.top + 18, f"BPM: {self.bpm:.1f}")
        x = self._chip(x, r.top + 18, f"TIME: {self.time_sig}")

    def _draw_mid(self, r: pygame.Rect):
        self._card(r, self.COL_PANEL)

        pad = 14
        inner = pygame.Rect(r.left + pad, r.top + pad, r.width - pad * 2, r.height - pad * 2)

        # Step 1: Only drums => only drums roll
        if self.has_drums and not self.has_pitched:
            self._draw_drums_only(inner)
            return

        # Step 2: Only pitched => only piano roll
        if self.has_pitched and not self.has_drums:
            self._draw_piano_only(inner)
            return

        # Step 3: Both => split vertically (same roll width => aligned grid lines)
        drum_h = int(inner.height * 0.28)
        pitched_h = inner.height - drum_h

        pitched_rect = pygame.Rect(inner.left, inner.top, inner.width, pitched_h)
        drums_rect = pygame.Rect(inner.left, inner.top + pitched_h, inner.width, drum_h)

        # Separator line
        pygame.draw.line(self.screen, (35, 40, 55), (drums_rect.left, drums_rect.top), (drums_rect.right, drums_rect.top), 1)

        # Shared roll geometry (same label width + same roll width)
        roll_width = inner.width - self.roll_label_w
        pps = self.pixels_per_second(roll_width)

        # Both rolls use the SAME playline x (aligned)
        roll_left = inner.left + self.roll_label_w
        playline_x = roll_left + int(roll_width * self.playline_ratio)

        self._draw_roll_piano(pitched_rect, pps, playline_x)
        self._draw_roll_drums(drums_rect, pps, playline_x)

    def _draw_piano_only(self, inner: pygame.Rect):
        roll_width = inner.width - self.roll_label_w
        pps = self.pixels_per_second(roll_width)
        roll_left = inner.left + self.roll_label_w
        playline_x = roll_left + int(roll_width * self.playline_ratio)
        self._draw_roll_piano(inner, pps, playline_x)

    def _draw_drums_only(self, inner: pygame.Rect):
        roll_width = inner.width - self.roll_label_w
        pps = self.pixels_per_second(roll_width)
        roll_left = inner.left + self.roll_label_w
        playline_x = roll_left + int(roll_width * self.playline_ratio)
        self._draw_roll_drums(inner, pps, playline_x)

    # --------------------------
    # Grid
    # --------------------------
    def _draw_grid(self, r: pygame.Rect, playline_x: int, pps: float):
        beat = self.beat_seconds()
        bar = self.bar_seconds()

        left_seconds = (playline_x - r.left) / max(1e-6, pps)
        window_start = self.time - left_seconds
        window_end = self.time + self.lookahead_seconds()

        # Beat lines
        first_beat_index = int(math.floor(window_start / beat))
        t = first_beat_index * beat
        while t <= window_end + beat:
            x = playline_x + int((t - self.time) * pps)
            if r.left <= x <= r.right:
                pygame.draw.line(self.screen, self.COL_BEAT, (x, r.top + 6), (x, r.bottom - 6), 1)
            t += beat

        # Bar lines
        first_bar_index = int(math.floor(window_start / bar))
        t = first_bar_index * bar
        while t <= window_end + bar:
            x = playline_x + int((t - self.time) * pps)
            if r.left <= x <= r.right:
                pygame.draw.line(self.screen, self.COL_BAR, (x, r.top + 6), (x, r.bottom - 6), 2)
            t += bar

    # --------------------------
    # Piano roll
    # --------------------------
    def _draw_roll_piano(self, r: pygame.Rect, pps: float, playline_x: int):
        label_rect = pygame.Rect(r.left, r.top, self.roll_label_w, r.height)
        roll_rect = pygame.Rect(r.left + self.roll_label_w, r.top, r.width - self.roll_label_w, r.height)

        pygame.draw.rect(self.screen, self.COL_PANEL_2, label_rect, border_radius=14)
        pygame.draw.rect(self.screen, (12, 14, 20), roll_rect, border_radius=14)

        # Left strip labels
        hi = self.piano_hi
        lo = self.piano_lo
        top_label = self.font_mono_small.render(f"HI {hi}", True, self.COL_TEXT_DIM)
        bot_label = self.font_mono_small.render(f"LO {lo}", True, self.COL_TEXT_DIM)
        self.screen.blit(top_label, (label_rect.left + 12, label_rect.top + 10))
        self.screen.blit(bot_label, (label_rect.left + 12, label_rect.bottom - 24))

        # Clip to roll window (smooth "walk off")
        self.screen.set_clip(roll_rect)

        # Grid + playline
        self._draw_grid(roll_rect, playline_x, pps)
        pygame.draw.line(self.screen, self.COL_PLAYLINE, (playline_x, roll_rect.top), (playline_x, roll_rect.bottom), 2)

        # Map pitch->y using stable range
        if self.piano_hi == self.piano_lo:
            self.piano_hi += 1

        def pitch_to_y(p: int) -> int:
            frac = (p - self.piano_lo) / (self.piano_hi - self.piano_lo)
            usable_h = roll_rect.height - 44
            return roll_rect.bottom - 22 - int(frac * usable_h)

        # Visible window in seconds
        left_seconds = (playline_x - roll_rect.left) / max(1e-6, pps)
        window_start = self.time - left_seconds
        window_end = self.time + self.lookahead_seconds()

        # Draw notes
        pitched_notes = [n for n in self.draw_notes if n.channel != 9]

        drawn: List[Tuple[pygame.Rect, Tuple[int, int, int]]] = []
        for n in pitched_notes:
            if n.start_sec > window_end:
                break
            if (n.start_sec + n.duration_sec) < window_start:
                continue

            x = playline_x + int((n.start_sec - self.time) * pps)
            w = max(8, int(n.duration_sec * pps))
            y = pitch_to_y(n.pitch) - 14
            rect = pygame.Rect(x, y, w, 28)

            base = self.channel_colors.get(n.channel, (125, 95, 210))
            edge = (min(255, base[0] + 40), min(255, base[1] + 40), min(255, base[2] + 40))

            note_surf = self._make_note_surface(rect.width, rect.height, base, edge, n.label)
            self._blit_clipped(note_surf, rect, roll_rect)

            vis = rect.clip(roll_rect)
            if vis.width > 0 and vis.height > 0:
                # Soft overlap blend
                for prev_rect, prev_col in drawn:
                    if vis.colliderect(prev_rect):
                        self._draw_soft_overlap(vis.clip(prev_rect), prev_col, base)
                drawn.append((vis, base))

        # Hit FX for pitched notes (at playline)
        for fx in self.hit_fx:
            if fx["kind"] != "piano":
                continue
            y = pitch_to_y(fx["pitch"])
            self._draw_hit_fx(fx, playline_x, y)

        self.screen.set_clip(None)

    # --------------------------
    # Drum roll
    # --------------------------
    def _draw_roll_drums(self, r: pygame.Rect, pps: float, playline_x: int):
        label_rect = pygame.Rect(r.left, r.top, self.roll_label_w, r.height)
        roll_rect = pygame.Rect(r.left + self.roll_label_w, r.top, r.width - self.roll_label_w, r.height)

        pygame.draw.rect(self.screen, self.COL_PANEL_2, label_rect, border_radius=14)
        pygame.draw.rect(self.screen, (12, 14, 20), roll_rect, border_radius=14)

        rows = self.drum_rows
        row_h = max(22, int(roll_rect.height / max(6, len(rows))))
        row_h = min(row_h, 44)

        # Draw the kit labels + simple physical icons on the left (no clip)
        self._draw_drum_kit_labels(label_rect, rows, row_h)

        # Clip to drum roll window
        self.screen.set_clip(roll_rect)

        # Grid + playline
        self._draw_grid(roll_rect, playline_x, pps)
        pygame.draw.line(self.screen, self.COL_PLAYLINE, (playline_x, roll_rect.top), (playline_x, roll_rect.bottom), 2)

        # Lane shading
        for i, _ in enumerate(rows):
            y0 = roll_rect.top + i * row_h
            y1 = y0 + row_h
            if y0 >= roll_rect.bottom:
                break

            if i % 2 == 0:
                lane = pygame.Rect(roll_rect.left, y0, roll_rect.width, row_h)
                pygame.draw.rect(self.screen, (14, 16, 22), lane)

            pygame.draw.line(self.screen, (30, 34, 48), (roll_rect.left, y1), (roll_rect.right, y1), 1)

        # Visible window in seconds
        left_seconds = (playline_x - roll_rect.left) / max(1e-6, pps)
        window_start = self.time - left_seconds
        window_end = self.time + self.lookahead_seconds()

        # Draw drum notes (channel 9)
        drum_notes = [n for n in self.audio_notes if n.channel == 9]
        drawn: List[Tuple[pygame.Rect, Tuple[int, int, int]]] = []

        for n in drum_notes:
            if n.start_sec > window_end:
                break
            if (n.start_sec + max(0.02, n.duration_sec)) < window_start:
                continue

            row = self.drum_pitch_to_row.get(n.pitch, len(rows) - 1)
            y0 = roll_rect.top + row * row_h + 4
            if y0 > roll_rect.bottom:
                continue

            x = playline_x + int((n.start_sec - self.time) * pps)
            w = max(10, int(max(0.03, n.duration_sec) * pps))
            rect = pygame.Rect(x, y0, w, row_h - 8)

            # Velocity makes drums "pop" slightly
            base = self.channel_colors.get(9, (210, 210, 215))
            vel = max(1, min(127, n.velocity))
            boost = int((vel / 127.0) * 35)
            base2 = (min(255, base[0] + boost), min(255, base[1] + boost), min(255, base[2] + boost))
            edge = (max(0, base2[0] - 30), max(0, base2[1] - 30), max(0, base2[2] - 30))

            lane_name = rows[row][0]
            note_surf = self._make_note_surface(rect.width, rect.height, base2, edge, lane_name)
            self._blit_clipped(note_surf, rect, roll_rect)

            vis = rect.clip(roll_rect)
            if vis.width > 0 and vis.height > 0:
                for prev_rect, prev_col in drawn:
                    if vis.colliderect(prev_rect):
                        self._draw_soft_overlap(vis.clip(prev_rect), prev_col, base2)
                drawn.append((vis, base2))

        # Hit FX for drums (at playline, centered on lane)
        for fx in self.hit_fx:
            if fx["kind"] != "drum":
                continue
            row = self.drum_pitch_to_row.get(fx["pitch"], len(rows) - 1)
            y_center = roll_rect.top + row * row_h + (row_h // 2)
            self._draw_hit_fx(fx, playline_x, y_center)

        self.screen.set_clip(None)

    def _draw_drum_kit_labels(self, label_rect: pygame.Rect, rows: List[Tuple[str, List[int]]], row_h: int):
        # Step 1: Draw lane labels + simple “kit” icons (physical interpretation)
        for i, (name, _) in enumerate(rows):
            cy = label_rect.top + i * row_h + row_h // 2
            if cy > label_rect.bottom:
                break

            icon_x = label_rect.left + 20
            text_x = label_rect.left + 44

            # Minimal icon set (readable + techy)
            if name == "Kick":
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x, cy), 10, width=2)
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x, cy), 3, width=0)
            elif name == "Snare":
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x, cy), 8, width=2)
            elif name == "HiHat":
                pygame.draw.line(self.screen, (205, 210, 220), (icon_x - 9, cy + 6), (icon_x + 9, cy + 6), 2)
                pygame.draw.line(self.screen, (205, 210, 220), (icon_x - 11, cy - 2), (icon_x + 11, cy - 2), 2)
            elif name in ("Crash", "Ride"):
                pygame.draw.arc(self.screen, (205, 210, 220), pygame.Rect(icon_x - 12, cy - 8, 24, 16), math.pi, 2 * math.pi, 2)
                pygame.draw.line(self.screen, (205, 210, 220), (icon_x, cy), (icon_x, cy + 10), 2)
            elif name == "Tom":
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x, cy), 6, width=2)
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x + 10, cy), 5, width=2)
            else:
                pygame.draw.circle(self.screen, (205, 210, 220), (icon_x, cy), 5, width=2)

            label = self.font_mono_small.render(name.upper(), True, self.COL_TEXT_DIM)
            self.screen.blit(label, (text_x, cy - 8))

    # --------------------------
    # Bottom bar
    # --------------------------
    def _draw_bottom(self, r: pygame.Rect):
        self._card(r, self.COL_PANEL)

        status = "PLAYING" if self.playing else "PAUSED"
        left = self.font_mono.render(f"{status}", True, self.COL_TEXT)
        self.screen.blit(left, (r.left + 16, r.top + 16))

        hint = self.font_mono_small.render(
            "SPACE play/pause   LEFT/RIGHT seek   R restart   ESC quit",
            True, self.COL_TEXT_DIM
        )
        self.screen.blit(hint, (r.left + 16, r.top + 44))

        # Timeline bar
        bar = pygame.Rect(r.left + 16, r.bottom - 34, r.width - 32, 14)
        pygame.draw.rect(self.screen, self.COL_PANEL_2, bar, border_radius=999)

        progress = int((self.time / max(0.1, self.song_length)) * bar.width)
        fill = pygame.Rect(bar.left, bar.top, progress, bar.height)
        pygame.draw.rect(self.screen, (160, 170, 200), fill, border_radius=999)

        # Thumb
        thumb_x = bar.left + progress
        thumb = pygame.Rect(thumb_x - 7, bar.top - 5, 14, bar.height + 10)
        pygame.draw.rect(self.screen, self.COL_PLAYLINE, thumb, border_radius=8)

        # Time text
        ttxt = self.font_mono.render(
            f"{self._fmt_time(self.time)} / {self._fmt_time(self.song_length)}",
            True, self.COL_TEXT_DIM
        )
        self.screen.blit(ttxt, (r.right - 16 - ttxt.get_width(), r.top + 16))
