from __future__ import annotations

import json
from pathlib import Path

from macr.schemas import Trace


class TraceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, trace: Trace) -> Path:
        path = self.root / f"{trace.task_id}.json"
        path.write_text(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
        latest = self.root / "latest.json"
        latest.write_text(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
        return path

