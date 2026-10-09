from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from copy import deepcopy
from macr.investigation.records import RoleMessage, RoleName
from macr.investigation.validation import ContractError, decode_record
from typing import get_args

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

    def __init__(self, *, evidence=None, hypothesis_ids=None, request_ids=None) -> None:
        self._sections: dict[str, list[BoardItem]] = {section: [] for section in self.SECTIONS}
        self._messages: list[RoleMessage] = []
        self.evidence = evidence
        self.hypothesis_ids = set(hypothesis_ids or [])
        self.request_ids = set(request_ids or [])

    def post_message(self, message: RoleMessage) -> str:
        message = decode_record(RoleMessage, asdict(message))
        if message.sequence != len(self._messages) + 1 or any(m.message_id == message.message_id for m in self._messages):
            raise ContractError('message_sequence')
        if message.request_id is not None and message.request_id not in self.request_ids:
            raise ContractError('unknown_request')
        if any(h not in self.hypothesis_ids for h in message.hypothesis_ids):
            raise ContractError('unknown_hypothesis')
        for evidence_id in message.evidence_ids:
            if self.evidence is None:
                raise ContractError('unknown_evidence')
            self.evidence.get_record(evidence_id)
        self._messages.append(deepcopy(message))
        return message.message_id

    def consume(self, role: str, after: int = 0) -> list[RoleMessage]:
        if role not in get_args(RoleName) or type(after) is not int or after < 0:
            raise ContractError('message_cursor')
        return deepcopy([m for m in self._messages if m.recipient_role == role and m.sequence > after and m.consumption_status == 'pending'])

    def acknowledge(self, role: str, message_ids: list[str]) -> None:
        selected = [m for m in self._messages if m.message_id in message_ids]
        if len(selected) != len(set(message_ids)) or any(m.recipient_role != role for m in selected):
            raise ContractError('message_recipient')
        for message in selected:
            message.consumption_status = 'consumed'

    def messages(self) -> list[RoleMessage]:
        return deepcopy(self._messages)

    @classmethod
    def from_messages(cls, messages, **kwargs):
        board = cls(**kwargs)
        for message in messages:
            board.post_message(message)
        return board

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
