import math

# ============================================================
# Small helper functions
# ============================================================

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_note_to_name(note_number: int) -> str:
    # Step 1: Work out note name (0=C, 1=C#, etc.)
    name = NOTE_NAMES[note_number % 12]
    # Step 2: Work out octave number
    octave = (note_number // 12) - 1
    # Step 3: Return string
    return f"{name}{octave}"

def clamp(value, low, high):
    return max(low, min(high, value))

def clean_path(text: str) -> str:
    # Step 1: Strip spaces
    text = text.strip()
    # Step 2: If user pasted quotes, remove them
    text = text.strip('"').strip("'")
    return text
