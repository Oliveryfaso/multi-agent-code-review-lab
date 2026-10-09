from __future__ import annotations

import json
from pathlib import Path

from macr.schemas import Trace
from macr.investigation.validation import validate_identifier
from macr.memory.atomic_file import atomic_write


class TraceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, trace: Trace) -> Path:
        validate_identifier(trace.task_id)
        data = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        path = self.root / f"{trace.task_id}.json"
        atomic_write(path, data)
        latest = self.root / "latest.json"
        # Each file is atomic; failure of latest keeps the previous complete value.
        atomic_write(latest, data)
        return path

    def read_legacy(self, path: Path) -> dict:
        from macr.memory.run_store import RunStore
        # Reading legacy data has no effect on new investigation/check grades.
        reader = RunStore.__new__(RunStore)
        reader.allowed = (Path(__file__).resolve().parents[3] / 'artifacts').resolve()
        return reader.read_legacy(path)
