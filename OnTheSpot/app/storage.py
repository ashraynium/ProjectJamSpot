import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .midi_parser import MidiParser
from .models import SongRecord
from .utils import safe_filename, title_from_filename


DEFAULT_SETTINGS = {
    "default_speed": 1.0,
    "count_in_bars": 1,
    "metronome": False,
    "master_volume": 100,
    "practice_seconds": 0.0,
    "sessions_completed": 0,
}


class JsonStore:
    def __init__(self, path: Path, default):
        self.path = path
        self.default = default
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.path.exists():
            return self.default.copy() if isinstance(self.default, dict) else list(self.default)
        try:
            with self.path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, OSError):
            return self.default.copy() if isinstance(self.default, dict) else list(self.default)

    def save(self, data) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        temporary.replace(self.path)


class SettingsStore:
    def __init__(self, data_directory: Path):
        self.store = JsonStore(data_directory / "settings.json", DEFAULT_SETTINGS)
        loaded = self.store.load()
        self.values = {**DEFAULT_SETTINGS, **loaded}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value) -> None:
        self.values[key] = value
        self.store.save(self.values)

    def record_session(self, seconds: float) -> None:
        if seconds < 1:
            return
        self.values["practice_seconds"] += float(seconds)
        self.values["sessions_completed"] += 1
        self.store.save(self.values)


class SongLibrary:
    def __init__(self, data_directory: Path):
        self.data_directory = data_directory
        self.songs_directory = data_directory / "songs"
        self.songs_directory.mkdir(parents=True, exist_ok=True)
        self.store = JsonStore(data_directory / "library.json", [])
        self.records = []
        for item in self.store.load():
            try:
                self.records.append(SongRecord.from_dict(item))
            except (TypeError, KeyError):
                continue

    def _save(self) -> None:
        self.store.save([record.to_dict() for record in self.records])

    def import_song(self, source_path: str) -> SongRecord:
        source = Path(source_path)
        if source.suffix.lower() not in {".mid", ".midi"}:
            raise ValueError("JamSpot can only import .mid or .midi files.")

        parser = MidiParser(str(source)).parse()
        song_id = uuid.uuid4().hex[:10]
        destination_name = f"{song_id}_{safe_filename(source.name)}"
        destination = self.songs_directory / destination_name
        shutil.copy2(source, destination)

        record = SongRecord(
            song_id=song_id,
            title=title_from_filename(source.name),
            stored_filename=destination_name,
            original_filename=source.name,
            bpm=parser.initial_bpm,
            duration=parser.song_length_seconds,
            time_signature=parser.time_signatures[0] if parser.time_signatures else "4/4",
            key_signature=parser.key_signatures[0] if parser.key_signatures else "Unknown",
            part_count=len(parser.parts_summary),
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        self.records.append(record)
        self._save()
        return record

    def path_for(self, record: SongRecord) -> Path:
        return self.songs_directory / record.stored_filename

    def get(self, song_id: str) -> Optional[SongRecord]:
        return next((record for record in self.records if record.song_id == song_id), None)

    def sorted_records(self, query: str = "") -> List[SongRecord]:
        query = query.strip().lower()
        records = [
            record
            for record in self.records
            if not query
            or query in record.title.lower()
            or query in record.original_filename.lower()
        ]
        return sorted(
            records,
            key=lambda record: (record.favourite, record.last_played, record.imported_at),
            reverse=True,
        )

    def toggle_favourite(self, song_id: str) -> None:
        record = self.get(song_id)
        if record:
            record.favourite = not record.favourite
            self._save()

    def mark_played(self, song_id: str) -> None:
        record = self.get(song_id)
        if record:
            record.last_played = datetime.now(timezone.utc).isoformat()
            self._save()

    def delete(self, song_id: str) -> None:
        record = self.get(song_id)
        if not record:
            return
        path = self.path_for(record)
        if path.exists():
            path.unlink()
        self.records = [item for item in self.records if item.song_id != song_id]
        self._save()