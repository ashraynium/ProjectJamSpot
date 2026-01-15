import pygame  # keeps pygame available for the visualiser window

from typing import Dict, List, Tuple

from app.midi_parser import MidiParser
from app.models import NoteEvent
from app.utils import clean_path
from app.visualiser import Visualiser


def build_backing_track(parser: MidiParser) -> Tuple[List[NoteEvent], Dict[int, int], str]:
    # Step 1: Reserve channels for playback (drums stay on 9)
    free_channels = [ch for ch in range(16) if ch != 9]
    next_free_index = 0

    # Step 2: Build remapped notes + programs
    mixed_notes: List[NoteEvent] = []
    mixed_programs: Dict[int, int] = {}

    # Step 3: Walk every virtual part and assign a playback channel
    for part_index, part in enumerate(parser.parts_summary):
        is_drums = part["is_drums"]

        # Step 4: Pick playback channel
        if is_drums:
            play_ch = 9
        else:
            if next_free_index >= len(free_channels):
                play_ch = 0
                print("WARNING: Too many parts for unique MIDI channels. Some parts will clash.")
            else:
                play_ch = free_channels[next_free_index]
                next_free_index += 1

        # Step 5: Copy program into remapped channel
        prog_map = parser.parts_programs[part_index]
        if (not is_drums) and prog_map:
            original_ch = list(prog_map.keys())[0]
            prog = prog_map[original_ch]
            mixed_programs[play_ch] = prog

        # Step 6: Copy notes into remapped channel
        for n in parser.parts_notes[part_index]:
            mixed_notes.append(NoteEvent(
                pitch=n.pitch,
                velocity=n.velocity,
                channel=play_ch,
                start_sec=n.start_sec,
                duration_sec=n.duration_sec,
                label=n.label
            ))

    # Step 7: Sort notes
    mixed_notes.sort(key=lambda n: n.start_sec)

    # Step 8: Return
    return mixed_notes, mixed_programs, "FULL BACKING TRACK"


# ============================================================
# MAIN (run it)
# ============================================================

if __name__ == "__main__":
    # Step 1: Ask for MIDI path
    midi_path = clean_path(input("Enter MIDI file path: "))

    # Step 2: Parse it
    parser = MidiParser(midi_path).parse()

    # Step 3: Print proof output (this is the examiner/teacher evidence)
    parser.print_summary()

    # Step 4: Auto pick a sensible part
    auto_part = parser.auto_pick_part()
    print("\nAuto-picked part:", auto_part, "-", parser.parts_summary[auto_part]["name"])

    # Step 5: Allow manual override OR full backing track
    print("\nType a part number to override,")
    print("or type B to play the FULL BACKING TRACK (all parts),")
    choice = input("or press Enter to keep auto: ").strip()

    play_backing = False
    if choice != "":
        if choice.lower() == "b":
            play_backing = True
        else:
            try:
                auto_part = int(choice)
            except:
                pass

    # Step 6: Choose display meta
    ts = parser.time_signatures[0] if parser.time_signatures else "4/4"
    ks = parser.key_signatures[0] if parser.key_signatures else "Unknown"
    bpm = parser.initial_bpm

    # Step 7: Choose notes/programs based on mode
    if play_backing:
        # Step 1: Build backing track by remapping parts to unique channels
        selected_notes, selected_programs, selected_name = build_backing_track(parser)
    else:
        selected_notes = parser.parts_notes[auto_part]
        selected_name = parser.parts_summary[auto_part]["name"]
        selected_programs = parser.parts_programs[auto_part]

    # Step 8: Launch visualiser
    app = Visualiser(
        title="JamSpot Prototype (MIDI -> Notes -> Visual + Audio)",
        part_name=selected_name,
        bpm=bpm,
        time_sig=ts,
        key_sig=ks,
        notes=selected_notes,
        song_length=parser.song_length_seconds,
        programs_by_channel=selected_programs
    )
    app.run()

#\\BEX-FILE-01\studenthome$\20\20karki_a\Wham_-_Careless_Whisper.mid
