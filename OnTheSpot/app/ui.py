from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pygame


@dataclass(frozen=True)
class Theme:
    background: Tuple[int, int, int] = (9, 11, 17)
    sidebar: Tuple[int, int, int] = (13, 16, 24)
    panel: Tuple[int, int, int] = (20, 24, 35)
    panel_light: Tuple[int, int, int] = (28, 33, 47)
    panel_hover: Tuple[int, int, int] = (36, 42, 59)
    text: Tuple[int, int, int] = (242, 243, 247)
    text_dim: Tuple[int, int, int] = (158, 164, 180)
    accent: Tuple[int, int, int] = (139, 105, 230)
    accent_light: Tuple[int, int, int] = (178, 151, 247)
    green: Tuple[int, int, int] = (87, 194, 151)
    orange: Tuple[int, int, int] = (235, 157, 91)
    red: Tuple[int, int, int] = (222, 93, 111)
    border: Tuple[int, int, int] = (46, 52, 69)


class FontBook:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.title = self._load(34, True)
        self.heading = self._load(25, True)
        self.subheading = self._load(19, True)
        self.body = self._load(16)
        self.body_bold = self._load(16, True)
        self.small = self._load(13)
        self.small_bold = self._load(13, True)
        self.large_number = self._load(38, True)
        self.mono = pygame.font.SysFont("consolas", 15)

    def _load(self, size: int, bold: bool = False):
        font_path = self.project_root / "assets" / "fonts" / "ZalandoSansExpanded-Bold.ttf"
        if font_path.exists():
            try:
                return pygame.font.Font(str(font_path), size)
            except pygame.error:
                pass
        return pygame.font.SysFont("arial", size, bold=bold)


def draw_card(surface: pygame.Surface, rect: pygame.Rect, colour, radius: int = 16) -> None:
    shadow = rect.move(0, 4)
    pygame.draw.rect(surface, (4, 5, 8), shadow, border_radius=radius)
    pygame.draw.rect(surface, colour, rect, border_radius=radius)


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


def draw_wrapped_text(surface, text, font, colour, x, y, width, line_gap=4) -> int:
    for line in wrapped_lines(text, font, width):
        draw_text(surface, line, font, colour, (x, y))
        y += font.get_height() + line_gap
    return y