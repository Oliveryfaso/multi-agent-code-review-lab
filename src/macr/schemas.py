from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanStep:
    goal: str
    tool_family: str
    query: str | None = None


@dataclass
class Plan:
    intent: str
    risk_level: str
    steps: list[PlanStep]
    max_tool_calls: int = 12
    search_terms: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    file: str
    line_start: int
    line_end: int
    reason: str
    snippet: str = ""
    symbol: str | None = None
    source_tool: str = ""
    confidence: float = 0.5


@dataclass
class ToolResult:
    ok: bool
    tool: str
    data: Any
    summary: str
    latency_ms: int
    error_type: str | None = None
    message: str | None = None
    retryable: bool = False
    suggested_fallback: str | None = None


@dataclass
class ToolCallSpec:
    tool_family: str
    action: str
    reason: str
    inputs: dict[str, Any]


@dataclass
class RetrievalCritique:
    quality: str
    empty_recall_count: int
    candidate_file_count: int
    evidence_count: int
    suggested_terms: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class FinalReviewReport:
    ok: bool
    confidence: float
    issues: list[str] = field(default_factory=list)
    retry_recommended: bool = False
    human_review_required: bool = False
    questions: list[str] = field(default_factory=list)


@dataclass
class AgentAnswer:
    answer: str
    evidence: list[Evidence]
    confidence: float
    next_steps: list[str]


@dataclass
class PatchArtifact:
    summary: str
    diff: str
    target_files: list[str]
    source: str = "template"
    verification: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    name: str
    status: str
    detail: str = ""
    created_at: str = field(default_factory=utc_now)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    query: str
    repo_path: str
    task_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    plan: Plan | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    state_timeline: list[RunState] = field(default_factory=list)
    board: dict[str, Any] = field(default_factory=dict)
    answer: AgentAnswer | None = None
    patch: PatchArtifact | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)
