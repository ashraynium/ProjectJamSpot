from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mido

from .models import NoteEvent, TempoEvent
from .utils import instrument_family, midi_note_to_name


class MidiParser:
    """Reads a MIDI file and separates it into selectable track/channel parts."""

    def __init__(self, midi_path: str):
        self.midi_path = str(midi_path)
        self.mid: Optional[mido.MidiFile] = None
        self.ticks_per_beat = 480
        self.tempo_map: List[TempoEvent] = []
        self.time_signatures: List[str] = []
        self.key_signatures: List[str] = []
        self.initial_bpm = 120.0
        self.song_length_seconds = 0.0
        self.parts_summary: List[dict] = []
        self.parts_notes: List[List[NoteEvent]] = []
        self.parts_programs: List[Dict[int, int]] = []

    def parse(self) -> "MidiParser":
        if not Path(self.midi_path).exists():
            raise FileNotFoundError(f"MIDI file not found: {self.midi_path}")

        self.mid = mido.MidiFile(self.midi_path)
        self.ticks_per_beat = self.mid.ticks_per_beat
        self._build_tempo_map()
        self._extract_meta_info()
        self._extract_parts_by_track_and_channel()
        self._estimate_song_length()

        if not self.parts_summary:
            raise ValueError("This MIDI file does not contain any complete notes.")
        return self

    def _build_tempo_map(self) -> None:
        current_tempo = 500000
        abs_tick = 0
        abs_sec = 0.0
        events = [TempoEvent(0, current_tempo, 0.0)]

        for msg in mido.merge_tracks(self.mid.tracks):
            abs_tick += msg.time
            abs_sec += mido.tick2second(msg.time, self.ticks_per_beat, current_tempo)
            if msg.type == "set_tempo":
                current_tempo = msg.tempo
                events.append(TempoEvent(abs_tick, current_tempo, abs_sec))

        cleaned: Dict[int, TempoEvent] = {}
        for event in events:
            cleaned[event.tick] = event
        self.tempo_map = [cleaned[tick] for tick in sorted(cleaned)]
        self.initial_bpm = round(mido.tempo2bpm(self.tempo_map[0].tempo_us_per_beat), 3)

    def tick_to_seconds(self, tick: int) -> float:
        last = self.tempo_map[0]
        for event in self.tempo_map:
            if event.tick > tick:
                break
            last = event
        remaining = tick - last.tick
        return last.seconds_at_tick + mido.tick2second(
            remaining, self.ticks_per_beat, last.tempo_us_per_beat
        )

    def _extract_meta_info(self) -> None:
        self.time_signatures = []
        self.key_signatures = []
        for msg in mido.merge_tracks(self.mid.tracks):
            if msg.type == "time_signature":
                value = f"{msg.numerator}/{msg.denominator}"
                if value not in self.time_signatures:
                    self.time_signatures.append(value)
            elif msg.type == "key_signature" and msg.key not in self.key_signatures:
                self.key_signatures.append(msg.key)

    def _extract_parts_by_track_and_channel(self) -> None:
        self.parts_summary = []
        self.parts_notes = []
        self.parts_programs = []

        for track_index, track in enumerate(self.mid.tracks):
            abs_tick = 0
            track_name = f"Track {track_index + 1}"
            notes_by_channel: Dict[int, List[NoteEvent]] = {}
            programs_by_channel: Dict[int, int] = {}
            active: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

            for msg in track:
                abs_tick += msg.time
                if msg.type == "track_name" and msg.name.strip():
                    track_name = msg.name.strip()
                elif msg.type == "program_change":
                    programs_by_channel[msg.channel] = msg.program
                elif msg.type == "note_on" and msg.velocity > 0:
                    active.setdefault((msg.channel, msg.note), []).append((abs_tick, msg.velocity))
                elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    key = (msg.channel, msg.note)
                    if not active.get(key):
                        continue
                    start_tick, velocity = active[key].pop(0)
                    start = self.tick_to_seconds(start_tick)
                    end = self.tick_to_seconds(abs_tick)
                    notes_by_channel.setdefault(msg.channel, []).append(
                        NoteEvent(
                            pitch=msg.note,
                            velocity=velocity,
                            channel=msg.channel,
                            start_sec=start,
                            duration_sec=max(0.01, end - start),
                            label=midi_note_to_name(msg.note),
                        )
                    )

            for channel, notes in notes_by_channel.items():
                notes.sort(key=lambda note: note.start_sec)
                stats = self._track_stats(notes)
                is_drums = channel == 9
                program = programs_by_channel.get(channel, 0)
                instrument = "Drums" if is_drums else instrument_family(program)
                display_name = track_name
                if instrument.lower() not in track_name.lower():
                    display_name = f"{track_name} - {instrument}"

                part_index = len(self.parts_summary)
                self.parts_summary.append(
                    {
                        "part_index": part_index,
                        "source_track": track_index,
                        "channel": channel,
                        "name": display_name,
                        "track_name": track_name,
                        "instrument": instrument,
                        "program": program,
                        "is_drums": is_drums,
                        "note_count": len(notes),
                        "lowest_pitch": stats["lowest_pitch"],
                        "highest_pitch": stats["highest_pitch"],
                        "max_polyphony": stats["max_polyphony"],
                    }
                )
                self.parts_notes.append(notes)
                self.parts_programs.append({channel: program} if not is_drums else {})

    def _track_stats(self, notes: List[NoteEvent]) -> dict:
        pitches = [note.pitch for note in notes]
        events = []
        for note in notes:
            events.append((note.start_sec, 1))
            events.append((note.start_sec + note.duration_sec, -1))
        events.sort(key=lambda item: (item[0], -item[1]))

        current = 0
        maximum = 0
        for _, change in events:
            current += change
            maximum = max(maximum, current)
        return {
            "lowest_pitch": min(pitches),
            "highest_pitch": max(pitches),
            "max_polyphony": maximum,
        }

    def _estimate_song_length(self) -> None:
        note_end = max(
            note.start_sec + note.duration_sec
            for part in self.parts_notes
            for note in part
        )
        self.song_length_seconds = max(note_end, float(getattr(self.mid, "length", 0.0)))

    def auto_pick_part(self) -> int:
        pitched = [part for part in self.parts_summary if not part["is_drums"]]
        choices = pitched or self.parts_summary
        melody = [part for part in choices if part["max_polyphony"] <= 2]
        choices = melody or choices
        return max(choices, key=lambda part: part["note_count"])["part_index"]

    def print_summary(self) -> None:
        print(f"File: {self.midi_path}")
        print(f"BPM: {self.initial_bpm} | Length: {self.song_length_seconds:.2f}s")
        for part in self.parts_summary:
            print(
                f'{part["part_index"]}: {part["name"]} | '
                f'{part["note_count"]} notes | polyphony {part["max_polyphony"]}'
            )