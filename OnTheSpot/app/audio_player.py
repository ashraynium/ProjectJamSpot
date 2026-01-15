import pygame.midi
from typing import Dict, List, Tuple

from .models import NoteEvent
from .utils import clamp


# ============================================================
# MIDI audio player (very basic)
# ============================================================

class MidiAudioPlayer:
    """
    Plays note events using pygame.midi
    Works if system has a MIDI synth output (Windows usually does).
    """

    def __init__(self, programs_by_channel: Dict[int, int]):
        # Step 1: Start midi system
        pygame.midi.init()

        # Step 2: Get default output device
        out_id = pygame.midi.get_default_output_id()

        # Step 3: If no default, find the first output device
        if out_id == -1:
            out_id = self._find_first_output_device()

        # Step 4: If still none, error
        if out_id == -1:
            raise RuntimeError("No MIDI output device found on this computer.")

        # Step 5: Create output object
        self.output = pygame.midi.Output(out_id)

        # Step 6: Save programs and scheduling variables
        self.programs_by_channel = programs_by_channel
        self.notes: List[NoteEvent] = []
        self.note_index = 0

        # pending_offs stores: (end_time, pitch, channel)
        self.pending_offs: List[Tuple[float, int, int]] = []

    def _find_first_output_device(self) -> int:
        # Step 1: Search device list
        for i in range(pygame.midi.get_count()):
            info = pygame.midi.get_device_info(i)
            if info:
                _, _, is_input, is_output, _ = info
                if is_output:
                    return i
        return -1

    def load_notes(self, notes: List[NoteEvent]):
        # Store notes (assume sorted by start_sec)
        self.notes = notes
        self.note_index = 0
        self.pending_offs = []

    def start(self, current_time: float):
        # Step 1: Send instrument programs for non-drum channels
        for ch, prog in self.programs_by_channel.items():
            if ch == 9:
                # drums are channel-based, ignore program changes
                continue
            self.output.write_short(0xC0 + ch, prog)

        # Step 2: Sync scheduling to current time
        self.seek(current_time)

    def stop_all_notes(self):
        # Step 1: Send All Notes Off to all channels
        for ch in range(16):
            self.output.write_short(0xB0 + ch, 123, 0)

        # Step 2: Clear pending offs
        self.pending_offs = []

    def seek(self, new_time: float):
        # Step 1: Stop any ringing notes
        self.stop_all_notes()

        # Step 2: Reset index to first note at/after new_time
        self.note_index = 0
        while self.note_index < len(self.notes) and self.notes[self.note_index].start_sec < new_time:
            self.note_index += 1

        # Step 3 (optional): turn on notes that should already be held
        for n in self.notes:
            end_t = n.start_sec + n.duration_sec
            if n.start_sec <= new_time < end_t:
                self._note_on(n.channel, n.pitch, n.velocity)
                self.pending_offs.append((end_t, n.pitch, n.channel))

        # Keep offs sorted
        self.pending_offs.sort(key=lambda x: x[0])

    def update(self, current_time: float):
        # Step 1: Turn off notes that finished
        while self.pending_offs and self.pending_offs[0][0] <= current_time:
            end_t, pitch, ch = self.pending_offs.pop(0)
            self._note_off(ch, pitch)

        # Step 2: Turn on notes that start now
        while self.note_index < len(self.notes) and self.notes[self.note_index].start_sec <= current_time:
            n = self.notes[self.note_index]

            self._note_on(n.channel, n.pitch, n.velocity)

            end_time = n.start_sec + n.duration_sec
            self.pending_offs.append((end_time, n.pitch, n.channel))
            self.pending_offs.sort(key=lambda x: x[0])

            self.note_index += 1

    def _note_on(self, ch: int, pitch: int, vel: int):
        ch = int(clamp(ch, 0, 15))
        pitch = int(clamp(pitch, 0, 127))
        vel = int(clamp(vel, 1, 127))  # avoid silent "0"
        self.output.write_short(0x90 + ch, pitch, vel)

    def _note_off(self, ch: int, pitch: int):
        ch = int(clamp(ch, 0, 15))
        pitch = int(clamp(pitch, 0, 127))
        self.output.write_short(0x80 + ch, pitch, 0)

    def close(self):
        self.stop_all_notes()
        self.output.close()
        pygame.midi.quit()
