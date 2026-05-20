from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContractViolation:
    section: str
    kind: str
    message: str
    severity: str = "error"


@dataclass
class ContractReport:
    ok: bool
    required_sections: list[str]
    present_sections: list[str]
    violations: list[ContractViolation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "required_sections": self.required_sections,
            "present_sections": self.present_sections,
            "violations": [violation.__dict__ for violation in self.violations],
        }


class BoardContractValidator:
    """Validates the minimum Agent Board artifacts needed for a trustworthy run."""

    BASE_REQUIRED = [
        "task",
        "plan",
        "policy",
        "routing",
        "repo_map",
        "retrieval",
        "retrieval_critique",
        "code_intelligence",
        "code_graph",
        "code_quality",
        "evidence",
        "review",
    ]
    PATCH_REQUIRED = ["patch", "verification"]
    DIFF_REVIEW_REQUIRED = ["policy", "repo_map", "code_quality", "pr_review"]

    REQUIRED_PAYLOAD_FIELDS = {
        "task:user_query": ["query", "repo_path", "generate_patch"],
        "plan:execution_plan": ["intent", "risk_level", "search_terms", "steps"],
        "policy:execution_policy": ["mode", "use_llm_planner", "use_llm_solver", "executed_steps", "skipped_steps"],
        "routing:tool_schedule": ["calls"],
        "repo_map:repository_map": ["total_python_files", "mapped_files", "focus_files", "symbols"],
        "retrieval_critique:retrieval_quality": [
            "quality",
            "empty_recall_count",
            "candidate_file_count",
            "evidence_count",
            "actions",
            "rationale",
        ],
        "code_graph:local_graph": ["ok", "summary", "node_count", "edge_count", "neighborhoods"],
        "code_quality:smell_report": ["smell_ratio", "severity", "hotspots", "suggestions"],
        "evidence:evidence_set": ["count", "items"],
        "review:final_review": ["answer", "confidence", "next_steps"],
        "pr_review:diff_review": ["risk_level", "changed_files", "comments", "test_suggestions", "summary"],
        "patch:patch_ranking": ["ranking", "selected_source"],
        "verification:patch_verification": ["verification", "source"],
    }

    def validate(self, board: dict[str, list[dict[str, Any]]], generate_patch: bool = False, diff_review: bool = False) -> ContractReport:
        required = list(self.DIFF_REVIEW_REQUIRED if diff_review else self.BASE_REQUIRED)
        if generate_patch and not diff_review:
            required.extend(self.PATCH_REQUIRED)
        violations: list[ContractViolation] = []
        for section in required:
            if not board.get(section):
                violations.append(ContractViolation(section, "*", "required board section is missing"))

        for section, items in board.items():
            if not isinstance(items, list):
                violations.append(ContractViolation(section, "*", "section must contain a list of board items"))
                continue
            for item in items:
                kind = str(item.get("kind") or "")
                payload = item.get("payload")
                if not item.get("agent"):
                    violations.append(ContractViolation(section, kind, "board item missing agent"))
                if not kind:
                    violations.append(ContractViolation(section, kind, "board item missing kind"))
                if not isinstance(payload, dict):
                    violations.append(ContractViolation(section, kind, "board item payload must be an object"))
                    continue
                key = f"{section}:{kind}"
                for field_name in self.REQUIRED_PAYLOAD_FIELDS.get(key, []):
                    if field_name not in payload:
                        violations.append(ContractViolation(section, kind, f"payload missing `{field_name}`"))
        return ContractReport(
            ok=not any(violation.severity == "error" for violation in violations),
            required_sections=required,
            present_sections=sorted(board.keys()),
            violations=violations,
        )
