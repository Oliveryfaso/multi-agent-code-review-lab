from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from macr.schemas import utc_now


@dataclass
class BoardItem:
    agent: str
    kind: str
    title: str
    payload: dict[str, Any]
    created_at: str = field(default_factory=utc_now)


class AgentBoard:
    """Shared blackboard for explicit multi-agent communication."""

    SECTIONS = [
        "task",
        "workflow",
        "plan",
        "policy",
        "routing",
        "repo_map",
        "retrieval",
        "retrieval_critique",
        "recovery",
        "code_intelligence",
        "code_graph",
        "code_quality",
        "evidence",
        "review",
        "pr_review",
        "final_review",
        "patch",
        "verification",
        "monitor",
    ]

    def __init__(self) -> None:
        self._sections: dict[str, list[BoardItem]] = {section: [] for section in self.SECTIONS}

    def post(self, section: str, agent: str, kind: str, title: str, payload: dict[str, Any]) -> None:
        if section not in self._sections:
            self._sections[section] = []
        self._sections[section].append(
            BoardItem(
                agent=agent,
                kind=kind,
                title=title,
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            section: [asdict(item) for item in items]
            for section, items in self._sections.items()
            if items
        }
