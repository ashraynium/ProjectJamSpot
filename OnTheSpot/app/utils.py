import re
from pathlib import Path


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

PROGRAM_FAMILIES = [
    (0, 7, "Piano"),
    (8, 15, "Chromatic Percussion"),
    (16, 23, "Organ"),
    (24, 31, "Guitar"),
    (32, 39, "Bass"),
    (40, 47, "Strings"),
    (48, 55, "Ensemble"),
    (56, 63, "Brass"),
    (64, 71, "Reed"),
    (72, 79, "Pipe"),
    (80, 87, "Synth Lead"),
    (88, 95, "Synth Pad"),
    (96, 103, "Synth Effects"),
    (104, 111, "Ethnic"),
    (112, 119, "Percussive"),
    (120, 127, "Sound Effects"),
]


def midi_note_to_name(note_number: int) -> str:
    name = NOTE_NAMES[note_number % 12]
    octave = (note_number // 12) - 1
    return f"{name}{octave}"


def instrument_family(program: int) -> str:
    for low, high, name in PROGRAM_FAMILIES:
        if low <= program <= high:
            return name
    return "Instrument"


def clamp(value, low, high):
    return max(low, min(high, value))


def clean_path(text: str) -> str:
    return text.strip().strip('"').strip("'")


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip().title()


def safe_filename(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _-]", "", Path(filename).stem).strip()
    stem = re.sub(r"\s+", "_", stem) or "song"
    return stem + Path(filename).suffix.lower()