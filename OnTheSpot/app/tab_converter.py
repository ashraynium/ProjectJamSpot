from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .models import NoteEvent


STANDARD_TUNING: Sequence[Tuple[str, int]] = (
    ("e", 64),
    ("B", 59),
    ("G", 55),
    ("D", 50),
    ("A", 45),
    ("E", 40),
)


@dataclass
class TabEvent:
    start_sec: float
    duration_sec: float
    pitch: int
    string_index: int
    fret: Optional[int]


def possible_positions(pitch: int, max_fret: int = 24) -> List[Tuple[int, int]]:
    positions = []

    for string_index, (_, open_pitch) in enumerate(STANDARD_TUNING):
        fret = pitch - open_pitch
        if 0 <= fret <= max_fret:
            positions.append((string_index, fret))

    return positions


def _group_notes(notes: List[NoteEvent], tolerance: float = 0.035) -> List[List[NoteEvent]]:
    groups: List[List[NoteEvent]] = []

    for note in sorted(notes, key=lambda item: (item.start_sec, item.pitch)):
        if groups and abs(groups[-1][0].start_sec - note.start_sec) <= tolerance:
            groups[-1].append(note)
        else:
            groups.append([note])

    return groups


def _best_chord_positions(
    chord: List[NoteEvent],
    previous_fret: float,
) -> List[Tuple[NoteEvent, int, Optional[int]]]:
    best: Optional[List[Tuple[NoteEvent, int, int]]] = None
    best_cost = float("inf")

    def search(index: int, used_strings: set, current, frets: List[int]) -> None:
        nonlocal best, best_cost

        if index == len(chord):
            average = sum(frets) / len(frets) if frets else previous_fret
            stretch = max(frets) - min(frets) if len(frets) > 1 else 0
            open_bonus = sum(1 for fret in frets if fret == 0) * 0.3
            cost = abs(average - previous_fret) + max(0, stretch - 4) * 2 - open_bonus

            if cost < best_cost:
                best_cost = cost
                best = list(current)

            return

        note = chord[index]

        for string_index, fret in possible_positions(note.pitch):
            if string_index in used_strings:
                continue

            used_strings.add(string_index)
            current.append((note, string_index, fret))
            frets.append(fret)

            search(index + 1, used_strings, current, frets)

            frets.pop()
            current.pop()
            used_strings.remove(string_index)

    if len(chord) <= 6:
        search(0, set(), [], [])

    if best is not None:
        return best

    result = []
    used = set()

    for note in chord:
        choices = [
            position
            for position in possible_positions(note.pitch)
            if position[0] not in used
        ]

        if choices:
            string_index, fret = min(
                choices,
                key=lambda position: abs(position[1] - previous_fret),
            )
            used.add(string_index)
            result.append((note, string_index, fret))
        else:
            result.append((note, -1, None))

    return result


def build_guitar_tab(notes: List[NoteEvent]) -> List[TabEvent]:
    result: List[TabEvent] = []
    previous_fret = 3.0

    for chord in _group_notes(notes):
        positions = _best_chord_positions(chord, previous_fret)
        played_frets = []

        for note, string_index, fret in positions:
            result.append(
                TabEvent(
                    start_sec=note.start_sec,
                    duration_sec=note.duration_sec,
                    pitch=note.pitch,
                    string_index=string_index,
                    fret=fret,
                )
            )

            if fret is not None:
                played_frets.append(fret)

        if played_frets:
            previous_fret = sum(played_frets) / len(played_frets)

    return sorted(result, key=lambda event: event.start_sec)


def tab_statistics(tab: List[TabEvent]) -> Dict[str, int]:
    return {
        "mapped": sum(event.fret is not None for event in tab),
        "unplayable": sum(event.fret is None for event in tab),
        "highest_fret": max((event.fret or 0 for event in tab), default=0),
    }