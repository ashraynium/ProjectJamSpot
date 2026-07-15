from typing import Dict, List, Optional, Set, Tuple

from .midi_parser import MidiParser
from .models import NoteEvent


def build_mix(
    parser: MidiParser,
    excluded_parts: Optional[Set[int]] = None,
    included_parts: Optional[Set[int]] = None,
) -> Tuple[List[NoteEvent], Dict[int, int]]:
    """Combines virtual parts while preventing their MIDI channels from clashing."""

    excluded_parts = excluded_parts or set()
    free_channels = [channel for channel in range(16) if channel != 9]
    next_channel = 0
    mixed_notes: List[NoteEvent] = []
    programs: Dict[int, int] = {}

    for part_index, part in enumerate(parser.parts_summary):
        if part_index in excluded_parts:
            continue
        if included_parts is not None and part_index not in included_parts:
            continue

        if part["is_drums"]:
            output_channel = 9
        else:
            output_channel = free_channels[next_channel % len(free_channels)]
            next_channel += 1
            programs[output_channel] = int(part.get("program", 0))

        for note in parser.parts_notes[part_index]:
            mixed_notes.append(
                NoteEvent(
                    pitch=note.pitch,
                    velocity=note.velocity,
                    channel=output_channel,
                    start_sec=note.start_sec,
                    duration_sec=note.duration_sec,
                    label=note.label,
                )
            )

    mixed_notes.sort(key=lambda note: note.start_sec)
    return mixed_notes, programs