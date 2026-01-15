from dataclasses import dataclass

# ============================================================
# Data models
# ============================================================

@dataclass
class TempoEvent:
    tick: int
    tempo_us_per_beat: int
    seconds_at_tick: float

@dataclass
class NoteEvent:
    pitch: int
    velocity: int
    channel: int
    start_sec: float
    duration_sec: float
    label: str
