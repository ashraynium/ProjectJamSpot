from dataclasses import asdict, dataclass
from typing import Any, Dict


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


@dataclass
class SongRecord:
    song_id: str
    title: str
    stored_filename: str
    original_filename: str
    bpm: float
    duration: float
    time_signature: str
    key_signature: str
    part_count: int
    imported_at: str
    last_played: str = ""
    favourite: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SongRecord":
        allowed = cls.__dataclass_fields__.keys()
        clean = {key: value for key, value in data.items() if key in allowed}
        return cls(**clean)


@dataclass
class PracticeOptions:
    target_part: int = 0
    mode: str = "piano"
    speed: float = 1.0
    count_in_bars: int = 1
    include_target: bool = True
    metronome: bool = False