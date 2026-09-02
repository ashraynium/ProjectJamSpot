import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pygame


@dataclass(frozen=True)
class Theme:
    background: Tuple[int, int, int] = (7, 9, 14)
    sidebar: Tuple[int, int, int] = (10, 13, 20)

    panel: Tuple[int, int, int] = (15, 19, 28)
    panel_light: Tuple[int, int, int] = (21, 26, 38)
    panel_hover: Tuple[int, int, int] = (28, 34, 48)
    panel_selected: Tuple[int, int, int] = (31, 39, 57)

    text: Tuple[int, int, int] = (241, 244, 249)
    text_dim: Tuple[int, int, int] = (139, 150, 169)
    text_dark: Tuple[int, int, int] = (72, 82, 100)

    accent: Tuple[int, int, int] = (104, 126, 255)
    accent_light: Tuple[int, int, int] = (143, 181, 255)
    accent_soft: Tuple[int, int, int] = (53, 67, 116)

    cyan: Tuple[int, int, int] = (100, 219, 228)
    green: Tuple[int, int, int] = (90, 207, 157)
    orange: Tuple[int, int, int] = (238, 169, 92)
    red: Tuple[int, int, int] = (222, 91, 108)

    border: Tuple[int, int, int] = (39, 47, 65)
    border_light: Tuple[int, int, int] = (54, 66, 89)

    piano_white: Tuple[int, int, int] = (220, 226, 235)
    piano_black: Tuple[int, int, int] = (25, 29, 39)

    roll_background: Tuple[int, int, int] = (10, 13, 20)
    grid: Tuple[int, int, int] = (31, 38, 53)
    grid_strong: Tuple[int, int, int] = (48, 58, 78)


class FontBook:
    def __init__(self, project_root: Path):
        self.project_root = project_root

        self.title = self._load(32, True)
        self.heading = self._load(24, True)
        self.subheading = self._load(18, True)

        self.body = self._load(16)
        self.body_bold = self._load(16, True)

        self.small = self._load(13)
        self.small_bold = self._load(13, True)

        self.tiny = self._load(11)
        self.tiny_bold = self._load(11, True)

        self.large_number = self._load(36, True)

        self.note = self._load(18, True)
        self.note_large = self._load(22, True)

        self.mono = pygame.font.SysFont("consolas", 14)

    def _load(self, size: int, bold: bool = False):
        font_path = (
            self.project_root
            / "assets"
            / "fonts"
            / "ZalandoSansExpanded-Bold.ttf"
        )

        if font_path.exists():
            try:
                return pygame.font.Font(str(font_path), size)
            except pygame.error:
                pass

        fallback = pygame.font.match_font("bahnschrift", bold=bold)

        if fallback:
            try:
                return pygame.font.Font(fallback, size)
            except pygame.error:
                pass

        return pygame.font.SysFont("arial", size, bold=bold)


class BackgroundParticles:
    def __init__(self, amount: int = 22):
        self.width = 1280
        self.height = 800

        self.particles = [
            self._new_particle()
            for _ in range(amount)
        ]

    def _new_particle(self):
        return {
            "x": random.uniform(0, self.width),
            "y": random.uniform(0, self.height),
            "speed_x": random.uniform(-3.0, 3.0),
            "speed_y": random.uniform(-5.0, -1.5),
            "radius": random.choice((1, 1, 1, 2)),
        }

    def update(self, dt: float) -> None:
        for particle in self.particles:
            particle["x"] += particle["speed_x"] * dt
            particle["y"] += particle["speed_y"] * dt

            if particle["y"] < -8:
                particle["y"] = self.height + random.randint(4, 25)
                particle["x"] = random.uniform(0, self.width)

            if particle["x"] < -8:
                particle["x"] = self.width + 5

            elif particle["x"] > self.width + 8:
                particle["x"] = -5

    def draw(self, surface: pygame.Surface) -> None:
        self.width, self.height = surface.get_size()

        for particle in self.particles:
            pygame.draw.circle(
                surface,
                (28, 37, 56),
                (
                    int(particle["x"]),
                    int(particle["y"]),
                ),
                particle["radius"],
            )


def mix_colour(first, second, amount: float):
    amount = max(0.0, min(1.0, amount))

    return tuple(
        int(first[index] + (second[index] - first[index]) * amount)
        for index in range(3)
    )


def draw_card(
    surface: pygame.Surface,
    rect: pygame.Rect,
    colour,
    radius: int = 14,
    border_colour=None,
) -> None:
    shadow = rect.move(0, 4)

    pygame.draw.rect(
        surface,
        (4, 6, 10),
        shadow,
        border_radius=radius,
    )

    pygame.draw.rect(
        surface,
        colour,
        rect,
        border_radius=radius,
    )

    if border_colour:
        pygame.draw.rect(
            surface,
            border_colour,
            rect,
            width=1,
            border_radius=radius,
        )


def draw_glow_rect(
    surface: pygame.Surface,
    rect: pygame.Rect,
    colour,
    radius: int = 10,
    strength: int = 2,
) -> None:
    background = surface.get_at((0, 0))[:3]

    for level in range(strength, 0, -1):
        glow_rect = rect.inflate(level * 5, level * 5)

        glow_colour = mix_colour(
            background,
            colour,
            0.08 + level * 0.05,
        )

        pygame.draw.rect(
            surface,
            glow_colour,
            glow_rect,
            width=1,
            border_radius=radius + level * 2,
        )


def draw_text(
    surface: pygame.Surface,
    text: str,
    font,
    colour,
    position,
    anchor: str = "topleft",
) -> pygame.Rect:
    rendered = font.render(str(text), True, colour)

    rect = rendered.get_rect()
    setattr(rect, anchor, position)

    surface.blit(rendered, rect)

    return rect


def wrapped_lines(text: str, font, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []

    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()

        if current and font.size(candidate)[0] > width:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    return lines


def draw_wrapped_text(
    surface,
    text,
    font,
    colour,
    x,
    y,
    width,
    line_gap=4,
) -> int:
    for line in wrapped_lines(text, font, width):
        draw_text(surface, line, font, colour, (x, y))
        y += font.get_height() + line_gap

    return y