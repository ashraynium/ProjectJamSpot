from typing import Dict, List, Tuple

import pygame.midi

from .models import NoteEvent
from .utils import clamp


class MidiAudioPlayer:
    """Schedules parsed notes through an available MIDI output device."""

    def __init__(self, programs_by_channel: Dict[int, int], volume: int = 100):
        self.programs_by_channel = programs_by_channel
        self.volume = int(clamp(volume, 0, 127))

        self.notes: List[NoteEvent] = []
        self.note_index = 0
        self.pending_offs: List[Tuple[float, int, int]] = []

        self.output = None
        self.available = False
        self.error_message = ""

        try:
            pygame.midi.init()

            output_id = pygame.midi.get_default_output_id()

            if output_id == -1:
                output_id = self._find_first_output_device()

            if output_id == -1:
                raise RuntimeError("No MIDI output device was found")

            self.output = pygame.midi.Output(output_id)
            self.available = True

            self._send_programs()
            self.set_volume(self.volume)

        except Exception as error:
            self.error_message = str(error)

    def _find_first_output_device(self) -> int:
        for index in range(pygame.midi.get_count()):
            info = pygame.midi.get_device_info(index)

            if info and info[3]:
                return index

        return -1

    def _send_programs(self) -> None:
        if not self.available:
            return

        for channel, program in self.programs_by_channel.items():
            if channel != 9:
                self.output.write_short(
                    0xC0 + channel,
                    int(clamp(program, 0, 127)),
                )

    def set_volume(self, volume: int) -> None:
        self.volume = int(clamp(volume, 0, 127))

        if not self.available:
            return

        for channel in range(16):
            self.output.write_short(0xB0 + channel, 7, self.volume)

    def load_notes(self, notes: List[NoteEvent]) -> None:
        self.notes = sorted(notes, key=lambda note: note.start_sec)
        self.seek(0.0)

    def start(self, current_time: float) -> None:
        self._send_programs()
        self.seek(current_time)

    def stop_all_notes(self) -> None:
        if self.available:
            for channel in range(16):
                self.output.write_short(0xB0 + channel, 123, 0)

        self.pending_offs = []

    def seek(self, new_time: float) -> None:
        self.stop_all_notes()
        self.note_index = 0

        # Notes that started before the new point are skipped when seeking.
        while (
            self.note_index < len(self.notes)
            and self.notes[self.note_index].start_sec < new_time
        ):
            self.note_index += 1

    def update(self, current_time: float) -> None:
        while self.pending_offs and self.pending_offs[0][0] <= current_time:
            _, pitch, channel = self.pending_offs.pop(0)
            self._note_off(channel, pitch)

        while self.note_index < len(self.notes):
            note = self.notes[self.note_index]

            if note.start_sec > current_time:
                break

            self._note_on(note)

            self.pending_offs.append(
                (
                    note.start_sec + note.duration_sec,
                    note.pitch,
                    note.channel,
                )
            )

            self.pending_offs.sort(key=lambda item: item[0])
            self.note_index += 1

    def click(self, accent: bool = False) -> None:
        if not self.available:
            return

        pitch = 37 if accent else 42
        velocity = 110 if accent else 75

        self.output.write_short(0x99, pitch, velocity)
        self.output.write_short(0x89, pitch, 0)

    def _note_on(self, note: NoteEvent) -> None:
        if not self.available:
            return

        channel = int(clamp(note.channel, 0, 15))
        pitch = int(clamp(note.pitch, 0, 127))
        velocity = int(clamp(note.velocity, 1, 127))

        self.output.write_short(0x90 + channel, pitch, velocity)

    def _note_off(self, channel: int, pitch: int) -> None:
        if self.available:
            self.output.write_short(0x80 + int(channel), int(pitch), 0)

    def close(self) -> None:
        self.stop_all_notes()

        if self.output is not None:
            self.output.close()
            self.output = None

        if pygame.midi.get_init():
            pygame.midi.quit()