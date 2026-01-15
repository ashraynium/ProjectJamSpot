import mido
from typing import Dict, List, Optional, Tuple

from .models import TempoEvent, NoteEvent
from .utils import midi_note_to_name


# ============================================================
#  MIDI parser (OOP)
# ============================================================

class MidiParser:
    """
    This class proves how the data is extracted from the MIDI file.

    Output includes:
    - Tempo map (supports tempo changes)
    - Time signature + key signature (if present)
    - "Virtual tracks" where each part is Track + Channel
    - Notes for each part with start time and duration in SECONDS
    """

    def __init__(self, midi_path: str):
        # Step 1: Store path
        self.midi_path = midi_path

        # Step 2: Will store the loaded MIDI
        self.mid: Optional[mido.MidiFile] = None

        # Step 3: Song-level values
        self.ticks_per_beat: int = 480
        self.tempo_map: List[TempoEvent] = []
        self.time_signatures: List[str] = []
        self.key_signatures: List[str] = []
        self.initial_bpm: float = 120.0
        self.song_length_seconds: float = 0.0

        # Step 4: Virtual track data (these are what the user selects)
        self.parts_summary: List[dict] = []
        self.parts_notes: List[List[NoteEvent]] = []
        self.parts_programs: List[Dict[int, int]] = []  # For selected part: channel -> program

    # --------------------------------------------------------
    # Main method
    # --------------------------------------------------------
    def parse(self):
        # Step 1: Load the MIDI file
        self.mid = mido.MidiFile(self.midi_path)
        self.ticks_per_beat = self.mid.ticks_per_beat

        # Step 2: Build tempo map (needed for tick -> seconds)
        self._build_tempo_map()

        # Step 3: Extract meta info (time signature + key)
        self._extract_meta_info()

        # Step 4: Extract notes and split them into "parts" (track+channel)
        self._extract_parts_by_track_and_channel()

        # Step 5: Estimate song length
        self._estimate_song_length()

        # Step 6: Return self so caller can use results
        return self

    # --------------------------------------------------------
    # Tempo map (handles tempo changes)
    # --------------------------------------------------------
    def _build_tempo_map(self):
        # Step 1: Default MIDI tempo (120 BPM)
        current_tempo = 500000  # microseconds per beat

        # Step 2: Merge tracks so we see tempo changes in real timeline order
        merged = mido.merge_tracks(self.mid.tracks)

        abs_tick = 0
        abs_sec = 0.0

        # Step 3: Start tempo map with default at tick 0
        events = [TempoEvent(0, current_tempo, 0.0)]

        # Step 4: Walk through merged messages and add tempo changes
        for msg in merged:
            abs_tick += msg.time

            # Convert delta ticks to seconds using current tempo
            abs_sec += mido.tick2second(msg.time, self.ticks_per_beat, current_tempo)

            if msg.type == "set_tempo":
                current_tempo = msg.tempo
                events.append(TempoEvent(abs_tick, current_tempo, abs_sec))

        # Step 5: Remove duplicates if multiple tempo events share same tick
        cleaned: Dict[int, TempoEvent] = {}
        for ev in events:
            cleaned[ev.tick] = ev
        self.tempo_map = [cleaned[t] for t in sorted(cleaned.keys())]

        # Step 6: Store initial BPM
        self.initial_bpm = float(round(mido.tempo2bpm(self.tempo_map[0].tempo_us_per_beat), 3))

    def tick_to_seconds(self, tick: int) -> float:
        # Step 1: Find the last tempo event at or before this tick (simple scan)
        last = self.tempo_map[0]
        for ev in self.tempo_map:
            if ev.tick <= tick:
                last = ev
            else:
                break

        # Step 2: Convert the remaining ticks into seconds
        delta_ticks = tick - last.tick
        delta_sec = mido.tick2second(delta_ticks, self.ticks_per_beat, last.tempo_us_per_beat)

        # Step 3: Return absolute seconds
        return last.seconds_at_tick + delta_sec

    # --------------------------------------------------------
    # Meta info (time signature + key signature)
    # --------------------------------------------------------
    def _extract_meta_info(self):
        merged = mido.merge_tracks(self.mid.tracks)

        for msg in merged:
            if msg.type == "time_signature":
                ts = f"{msg.numerator}/{msg.denominator}"
                if ts not in self.time_signatures:
                    self.time_signatures.append(ts)

            if msg.type == "key_signature":
                if msg.key not in self.key_signatures:
                    self.key_signatures.append(msg.key)

    # --------------------------------------------------------
    # The important part: splitting into "parts"
    # --------------------------------------------------------
    def _extract_parts_by_track_and_channel(self):
        # Step 1: Reset outputs
        self.parts_summary = []
        self.parts_notes = []
        self.parts_programs = []

        # Step 2: Loop through every real track
        for real_track_index, track in enumerate(self.mid.tracks):
            abs_tick = 0
            track_name = f"Track {real_track_index}"

            # Step 3: Collect notes grouped by channel INSIDE this track
            notes_by_channel: Dict[int, List[NoteEvent]] = {}

            # Step 4: Store program changes (instrument) by channel
            programs_by_channel: Dict[int, int] = {}

            # Step 5: Pair note-on and note-off
            active_notes: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

            # Step 6: Read each message in this track
            for msg in track:
                abs_tick += msg.time

                # Track name
                if msg.type == "track_name":
                    track_name = msg.name

                # Program change (instrument sound)
                if msg.type == "program_change":
                    programs_by_channel[msg.channel] = msg.program

                # Note ON start
                if msg.type == "note_on" and msg.velocity > 0:
                    key = (msg.channel, msg.note)
                    if key not in active_notes:
                        active_notes[key] = []
                    active_notes[key].append((abs_tick, msg.velocity))

                # Note OFF end (or note_on velocity 0)
                if msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                    key = (msg.channel, msg.note)

                    if key in active_notes and len(active_notes[key]) > 0:
                        start_tick, velocity = active_notes[key].pop()
                        end_tick = abs_tick

                        start_sec = self.tick_to_seconds(start_tick)
                        end_sec = self.tick_to_seconds(end_tick)
                        duration = max(0.0, end_sec - start_sec)

                        ch = msg.channel
                        if ch not in notes_by_channel:
                            notes_by_channel[ch] = []

                        notes_by_channel[ch].append(NoteEvent(
                            pitch=msg.note,
                            velocity=velocity,
                            channel=ch,
                            start_sec=start_sec,
                            duration_sec=duration,
                            label=midi_note_to_name(msg.note)
                        ))

            # Step 7: Create a "virtual part" for each channel with notes
            for ch, notes in notes_by_channel.items():
                # Sort notes in time order
                notes.sort(key=lambda n: n.start_sec)

                # Work out basic stats for auto-labelling
                stats = self._track_stats(notes)

                # Drums are usually channel 9 (10th channel)
                is_drums = (ch == 9)

                # Create readable label
                part_label = f"{track_name} | ch {ch}"
                if is_drums:
                    part_label += " | DRUMS"
                else:
                    if ch in programs_by_channel:
                        part_label += f" | prog {programs_by_channel[ch]}"

                # Save summary
                part_index = len(self.parts_summary)
                self.parts_summary.append({
                    "part_index": part_index,
                    "source_track": real_track_index,
                    "channel": ch,
                    "name": part_label,
                    "is_drums": is_drums,
                    "note_count": len(notes),
                    "lowest_pitch": stats["lowest_pitch"],
                    "highest_pitch": stats["highest_pitch"],
                    "max_polyphony": stats["max_polyphony"],
                })

                # Save notes
                self.parts_notes.append(notes)

                # Save programs for audio (only for this channel)
                single_program = {}
                if ch in programs_by_channel:
                    single_program[ch] = programs_by_channel[ch]
                self.parts_programs.append(single_program)

    def _track_stats(self, notes: List[NoteEvent]) -> dict:
        # Step 1: If empty, return defaults
        if not notes:
            return {"lowest_pitch": None, "highest_pitch": None, "max_polyphony": 0}

        # Step 2: Pitch range
        pitches = [n.pitch for n in notes]
        lowest = min(pitches)
        highest = max(pitches)

        # Step 3: Polyphony estimate (overlap count)
        events = []
        for n in notes:
            events.append((n.start_sec, +1))
            events.append((n.start_sec + n.duration_sec, -1))

        # Sort starts before ends when same time
        events.sort(key=lambda x: (x[0], -x[1]))

        cur = 0
        max_poly = 0
        for _, delta in events:
            cur += delta
            if cur > max_poly:
                max_poly = cur

        return {"lowest_pitch": lowest, "highest_pitch": highest, "max_polyphony": max_poly}

    def _estimate_song_length(self):
        # Step 1: Find latest note end time
        max_end = 0.0
        for notes in self.parts_notes:
            for n in notes:
                max_end = max(max_end, n.start_sec + n.duration_sec)

        # Step 2: mido also gives mid.length sometimes, so use the larger value
        self.song_length_seconds = max(max_end, getattr(self.mid, "length", 0.0))

    # --------------------------------------------------------
    # Printing (for proving to teacher/examiner)
    # --------------------------------------------------------
    def print_summary(self):
        print("\n=== MIDI FILE SUMMARY ===")
        print("File:", self.midi_path)
        print("Type:", self.mid.type)  # MIDI type 0 or 1 etc.
        print("Ticks per beat:", self.ticks_per_beat)
        print("Initial BPM:", self.initial_bpm)
        print("Time signatures:", self.time_signatures if self.time_signatures else "None found")
        print("Key signatures:", self.key_signatures if self.key_signatures else "None found")
        print("Tempo events:", len(self.tempo_map))
        print("Estimated song length (s):", round(self.song_length_seconds, 3))

        print("\n=== PARTS (virtual tracks) ===")
        for p in self.parts_summary:
            print(
                f'{p["part_index"]}: {p["name"]} | '
                f'notes={p["note_count"]} | drums={p["is_drums"]} | '
                f'poly={p["max_polyphony"]} | pitch={p["lowest_pitch"]}->{p["highest_pitch"]}'
            )

    def auto_pick_part(self) -> int:
        # Step 1: Prefer non-drums parts with some notes
        candidates = []
        for p in self.parts_summary:
            if p["note_count"] == 0:
                continue
            if p["is_drums"]:
                continue
            candidates.append(p)

        # Step 2: If none, just pick 0
        if not candidates:
            return 0

        # Step 3: Prefer melody-like (low polyphony)
        melody_like = [p for p in candidates if p["max_polyphony"] <= 2]
        if melody_like:
            # pick the one with most notes among melody-like
            melody_like.sort(key=lambda x: x["note_count"], reverse=True)
            return melody_like[0]["part_index"]

        # Step 4: Otherwise pick most notes overall
        candidates.sort(key=lambda x: x["note_count"], reverse=True)
        return candidates[0]["part_index"]
