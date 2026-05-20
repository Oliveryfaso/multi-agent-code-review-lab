from __future__ import annotations

from dataclasses import asdict

from macr.schemas import Evidence


class EvidenceStore:
    """Deduplicates evidence while keeping the strongest confidence per location."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []
        self._index: dict[tuple[str, int, int, str, str], int] = {}

    def add(self, evidence: Evidence) -> bool:
        key = self._key(evidence)
        existing_index = self._index.get(key)
        if existing_index is None:
            self._index[key] = len(self._items)
            self._items.append(evidence)
            return True

        existing = self._items[existing_index]
        existing.confidence = max(existing.confidence, evidence.confidence)
        if evidence.snippet and not existing.snippet:
            existing.snippet = evidence.snippet
        if evidence.reason and evidence.reason not in existing.reason:
            existing.reason = f"{existing.reason}; {evidence.reason}"
        return False

    def extend(self, evidence_items: list[Evidence]) -> None:
        for evidence in evidence_items:
            self.add(evidence)

    def items(self) -> list[Evidence]:
        return list(self._items)

    def to_payload_items(self) -> list[dict]:
        return [
            {
                "file": item.file,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "symbol": item.symbol,
                "source_tool": item.source_tool,
                "reason": item.reason,
                "confidence": item.confidence,
            }
            for item in self._items
        ]

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self._items:
            counts[item.source_tool or "unknown"] = counts.get(item.source_tool or "unknown", 0) + 1
        return counts

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __repr__(self) -> str:
        return f"EvidenceStore({[asdict(item) for item in self._items]!r})"

    def _key(self, evidence: Evidence) -> tuple[str, int, int, str, str]:
        return (
            evidence.file,
            evidence.line_start,
            evidence.line_end,
            evidence.symbol or "",
            evidence.source_tool or "",
        )
