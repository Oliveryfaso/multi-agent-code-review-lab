"""Shared records. Facts, model proposals and execution observations stay separate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 1
RunId = ActionId = EvidenceId = HypothesisId = SymbolId = str
Grade = Literal["candidate", "static_supported", "behavior_reproduced", "causal_supported"]
BackendJobState = Literal["running", "completed", "stopped", "outcome_unknown"]
RoleName = Literal["coordinator", "investigator", "falsifier", "retrieval_critic", "verifier", "report"]


@dataclass(kw_only=True)
class Record:
    schema_version: int = SCHEMA_VERSION


@dataclass
class RunError(Record):
    code: str
    detail: str = ""
    action_id: str | None = None
    retryable: bool = False


@dataclass
class SnapshotRef(Record):
    snapshot_id: str
    content_id: str
    content_manifest_ref: str
    root_ref: str
    scope_version: str = "1"
    base_commit: str | None = None
    dirty_manifest: list[str] = field(default_factory=list)


@dataclass
class TaskSpec(Record):
    run_id: str
    mode: Literal["locate", "scan"]
    snapshot_ref: SnapshotRef | None = None
    symptom: dict[str, Any] | None = None
    target_grade: Grade = "causal_supported"
    scope_profile: str = "default"
    execution_profile: str = "none"
    model_profile: str = "rules"
    budget_profile: str = "default"
    patch_enabled: bool = False


@dataclass
class ScopeProfile(Record):
    profile_id: str = "default"
    version: str = "1"
    max_python_files: int = 2000
    max_python_lines: int = 100_000
    max_text_bytes: int = 64 * 1024 * 1024
    max_file_bytes: int = 512 * 1024
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class FileEntry(Record):
    path: str
    kind: str = "file"
    size_bytes: int = 0
    language: str = "python"
    reason: str = ""
    lines: int | None = None


@dataclass
class ScopeManifest(Record):
    scope_version: str = "1"
    profile: ScopeProfile = field(default_factory=ScopeProfile)
    eligible: list[FileEntry] = field(default_factory=list)
    excluded: list[FileEntry] = field(default_factory=list)
    unsupported: list[FileEntry] = field(default_factory=list)
    unreadable: list[FileEntry] = field(default_factory=list)
    errors: list[RunError] = field(default_factory=list)
    bytes_read: int = 0


@dataclass
class CapturedFile:
    entry: FileEntry
    content: bytes
    digest: str


@dataclass
class ContentManifest(Record):
    scope_version: str
    files: list[dict[str, Any]]
    scope_profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotBundle:
    ref: SnapshotRef
    scope: ScopeManifest
    files: dict[str, CapturedFile]


@dataclass
class Location(Record):
    file: str
    start: int
    end: int
    symbol_id: str | None = None


@dataclass
class SymbolRecord(Record):
    symbol_id: str
    file: str
    module: str
    qualified_name: str
    kind: str
    start: int
    end: int
    signature: str = ""


@dataclass
class RelationRecord(Record):
    source_id: str
    target_ref: str
    relation_type: str
    location: Location
    resolution: Literal["resolved_import", "resolved_local", "syntactic_candidate", "unresolved"]


@dataclass
class CoverageRecord(Record):
    inventory: int = 0
    eligible: int = 0
    indexed: int = 0
    parsed: int = 0
    read: int = 0
    investigated: int = 0
    checked: int = 0
    errors: list[RunError] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)


@dataclass
class IndexSummary(Record):
    content_id: str
    coverage: CoverageRecord
    errors: list[RunError] = field(default_factory=list)
    usage: ActionUsage = field(default_factory=lambda: ActionUsage())


@dataclass
class IndexQuery(Record):
    kind: Literal["symbols", "relations"] = "symbols"
    symbol_ids: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    cursor: int = 0
    limit: int = 50


@dataclass
class IndexPage(Record):
    symbols: list[SymbolRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    next_cursor: int | None = None


@dataclass
class EvidenceRecord(Record):
    evidence_id: str
    snapshot_id: str
    observation: str
    location: Location | None = None
    excerpt: str | None = None
    origin: str = "source"
    tool_run_id: str = ""
    validity: Literal["valid", "stale", "invalid"] = "valid"
    redaction: bool = False


@dataclass
class ContextRequest(Record):
    locations: list[Location]
    max_bytes: int = 32 * 1024
    max_tokens: int = 8192


@dataclass
class ContextPack(Record):
    snapshot_ref: SnapshotRef
    snippets: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    truncations: list[str] = field(default_factory=list)
    bytes_read: int = 0
    usage: ActionUsage = field(default_factory=lambda: ActionUsage())


@dataclass
class HypothesisRecord(Record):
    hypothesis_id: str
    claim: str
    causal_chain: list[str] = field(default_factory=list)
    support_ids: list[str] = field(default_factory=list)
    counter_ids: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    status: Literal["candidate", "supported", "refuted", "inconclusive", "behavior_reproduced", "causal_supported"] = "candidate"


@dataclass
class Limits(Record):
    cpu: float
    memory_bytes: int
    pids: int
    wall_seconds: float
    output_bytes: int


@dataclass
class ExecutionProfile(Record):
    profile_id: str
    runner: str
    limits: Limits
    selectors_policy: list[str] = field(default_factory=list)
    dependency_manifest: list[str] = field(default_factory=list)
    environment_ref: str = ""


@dataclass
class BackendCapabilities(Record):
    network_disabled: bool = False
    readonly_input: bool = False
    isolated_writes: bool = False
    secrets_absent: bool = False
    cpu_limit: bool = False
    memory_limit: bool = False
    pid_limit: bool = False
    wall_limit: bool = False
    output_limit: bool = False
    process_tree_stop: bool = False
    architecture: str = "unknown"
    environment_ref: str = ""


@dataclass
class CheckSpec(Record):
    check_id: str
    purpose: str = "test"
    argv_profile: str = "unittest"
    selectors: list[str] = field(default_factory=list)
    input_artifacts: list[str] = field(default_factory=list)
    prediction: dict[str, Any] | None = None
    execution_profile: str = "none"
    limits: Limits | None = None


@dataclass
class CheckResult(Record):
    check_id: str
    execution_status: Literal["completed", "timeout", "resource_limit", "error", "blocked", "cancelled", "outcome_unknown"]
    observed_status: Literal["passed", "failed", "skipped", "zero_tests", "unknown", "not_applicable"] = "unknown"
    exit_code: int | None = None
    test_counts: dict[str, int] | None = None
    stdout_ref: str = ""
    stderr_ref: str = ""
    expectation_result: Literal["matches", "contradicts", "inconclusive", "not_requested"] = "not_requested"
    environment_ref: str = ""
    limits_observed: dict[str, Any] = field(default_factory=dict)
    output_truncated: bool = False
    error: RunError | None = None


@dataclass
class BudgetProfile(Record):
    profile_id: str = "locate-default-v1"
    wall_seconds: float = 720.0
    tool_calls: int = 80
    model_calls: int = 8
    input_tokens: int = 64_000
    output_tokens: int = 8000
    dynamic_checks: int = 4
    check_wall_seconds: float = 60.0
    check_output_bytes: int = 1024 * 1024


@dataclass
class ActionUsage(Record):
    tool_calls: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    checks: int = 0
    bytes_read: int = 0
    active_seconds: float = 0.0
    usage_kind: Literal["measured", "estimated", "unknown"] = "measured"


@dataclass
class Reservation(Record):
    action_id: str
    upper_bounds: ActionUsage
    state: Literal["reserved", "settled", "unknown"] = "reserved"


@dataclass
class BudgetView(Record):
    profile: BudgetProfile = field(default_factory=BudgetProfile)
    used: ActionUsage = field(default_factory=ActionUsage)
    reserved: ActionUsage = field(default_factory=ActionUsage)
    active_intervals: list[tuple[float, float | None]] = field(default_factory=list)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    captured_at: float | None = None
    bounds_exceeded: bool = False


@dataclass
class ReadArgs(Record):
    locations: list[Location]
    max_bytes: int = 32 * 1024
    max_tokens: int = 8192


@dataclass
class QueryArgs(Record):
    query: IndexQuery


@dataclass
class SearchArgs(Record):
    query: str
    max_results: int = 20
    cursor: int = 0


@dataclass
class CheckArgs(Record):
    check: CheckSpec


@dataclass
class ModelArgs(Record):
    request: dict[str, Any]


@dataclass
class ActionRequest(Record):
    action_id: str
    requester_role: RoleName
    action_type: Literal["read_context", "query_index", "search_text", "run_check", "call_model"]
    typed_args: ReadArgs | QueryArgs | SearchArgs | CheckArgs | ModelArgs
    hypothesis_id: str | None = None
    expected_information: str = ""
    budget_reservation: Reservation | None = None


@dataclass
class ActionResult(Record):
    action_id: str
    status: Literal["completed", "blocked", "error", "cancelled", "outcome_unknown"]
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    usage: ActionUsage = field(default_factory=ActionUsage)
    error: RunError | None = None
    new_information: bool = False


@dataclass
class RoleInput(Record):
    role: RoleName
    task_summary: str = ""
    hypothesis_ids: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)


@dataclass
class RoleProposal(Record):
    hypotheses: list[HypothesisRecord] = field(default_factory=list)
    counterclaims: list[dict[str, Any]] = field(default_factory=list)
    proposed_actions: list[ActionRequest] = field(default_factory=list)
    cited_evidence_ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class GradeDecision(Record):
    hypothesis_id: str
    grade: str
    support_ids: list[str] = field(default_factory=list)
    counter_ids: list[str] = field(default_factory=list)
    check_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RoleMessage(Record):
    message_id: str
    sequence: int
    sender: RoleName
    recipient_role: RoleName
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    hypothesis_ids: list[str] = field(default_factory=list)
    consumption_status: Literal["pending", "consumed"] = "pending"


@dataclass
class FindingRecord(Record):
    finding_id: str
    root_location: Location
    symptom: str
    grade: Grade = "candidate"
    trigger_conditions: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    counter_ids: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    severity: str = "unknown"


@dataclass
class InvestigationState(Record):
    task: TaskSpec
    phase: str = "received"
    scope: ScopeManifest | None = None
    index: IndexSummary | None = None
    queue: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    hypotheses: dict[str, HypothesisRecord] = field(default_factory=dict)
    messages: list[RoleMessage] = field(default_factory=list)
    cursors: dict[str, int] = field(default_factory=dict)
    budget: BudgetView = field(default_factory=BudgetView)
    checks: dict[str, CheckResult] = field(default_factory=dict)
    inflight: dict[str, ActionRequest] = field(default_factory=dict)
    completed: dict[str, ActionResult] = field(default_factory=dict)
    stop_reason: str = ""
    cancel_requested: bool = False


@dataclass
class RunEvent(Record):
    sequence: int
    run_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    action_id: str | None = None


@dataclass
class CheckpointRef(Record):
    run_id: str
    sequence: int
    path: str


@dataclass
class ResumeResult(Record):
    state: InvestigationState
    uncertain_actions: list[str] = field(default_factory=list)
    errors: list[RunError] = field(default_factory=list)
    usage: ActionUsage = field(default_factory=ActionUsage)


@dataclass
class RunReport(Record):
    run_id: str
    terminal_state: str
    investigation_outcome: Literal["target_met", "partial", "inconclusive"]
    findings: list[FindingRecord] = field(default_factory=list)
    coverage: CoverageRecord = field(default_factory=CoverageRecord)
    budget: BudgetView = field(default_factory=BudgetView)
    errors: list[RunError] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    hypotheses: list[HypothesisRecord] = field(default_factory=list)
    model_profile: str = "rules"
    environment_ref: str = "none"
    checkpoint_ref: CheckpointRef | None = None
