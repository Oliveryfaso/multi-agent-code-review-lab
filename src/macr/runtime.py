from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from macr.memory.board import AgentBoard
from macr.memory.evidence_store import EvidenceStore
from macr.schemas import Plan, RunState, Trace, utc_now


@dataclass
class RunContext:
    """Mutable state shared by typed runtime steps."""

    repo_path: Path
    query: str
    trace: Trace
    board: AgentBoard
    plan: Plan | None = None
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    candidate_files: set[str] = field(default_factory=set)
    file_match_lines: dict[str, set[int]] = field(default_factory=dict)
    retry_count: int = 0
    generate_patch: bool = False
    test_selector: str | None = None


@dataclass
class StepExecution:
    name: str
    status: str
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_state(self) -> RunState:
        return RunState(
            name=self.name,
            status=self.status,
            detail=self.detail,
            metrics=self.metrics,
            created_at=self.created_at,
        )


class RuntimeRecorder:
    """Small runtime facade so agent phases are recorded consistently."""

    def __init__(self, trace: Trace) -> None:
        self.trace = trace

    def record(self, name: str, status: str, detail: str = "", metrics: dict[str, Any] | None = None) -> None:
        self.trace.state_timeline.append(
            StepExecution(name=name, status=status, detail=detail, metrics=metrics or {}).to_state()
        )
