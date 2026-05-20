from __future__ import annotations

import re

from macr.schemas import Evidence, Plan, RetrievalCritique


class RetrievalCriticAgent:
    """Evaluates retrieval quality and proposes corrective retrieval actions."""

    def critique(
        self,
        query: str,
        plan: Plan,
        tool_calls: list[dict],
        candidate_files: set[str],
        evidence: list[Evidence],
    ) -> RetrievalCritique:
        empty = [call for call in tool_calls if call.get("error_type") == "empty_recall"]
        actions: list[str] = []
        suggested_terms = self._rewrite_terms(query, plan.search_terms)
        quality = "good"
        rationale = "retrieval produced candidate files and evidence"
        if not candidate_files:
            quality = "poor"
            actions.append("query_rewrite")
            rationale = "no candidate files found"
        elif len(empty) >= max(2, len(plan.search_terms) // 2) and suggested_terms:
            quality = "partial"
            actions.append("query_rewrite")
            rationale = "multiple exploratory queries had empty recall"
        symbol_evidence = [item for item in evidence if item.symbol]
        needs_symbol_expansion = not symbol_evidence
        if (
            symbol_evidence
            and len(symbol_evidence) < 2
            and any(token in query for token in ("调用链", "涉及哪些模块", "失败", "影响"))
        ):
            needs_symbol_expansion = True
        if needs_symbol_expansion:
            if "symbol_expansion" not in actions:
                actions.append("symbol_expansion")
            if quality == "good":
                quality = "partial"
            rationale = "symbol-level evidence is thin"
        return RetrievalCritique(
            quality=quality,
            empty_recall_count=len(empty),
            candidate_file_count=len(candidate_files),
            evidence_count=len(evidence),
            suggested_terms=suggested_terms,
            actions=actions,
            rationale=rationale,
        )

    def _rewrite_terms(self, query: str, existing: list[str]) -> list[str]:
        mapping = {
            "调用链": ["process_payment", "mark_order_paid", "send_payment_receipt"],
            "涉及哪些模块": ["process_payment", "orders", "notifications"],
            "收据": ["send_payment_receipt", "send_email"],
            "总价": ["calculate_total", "total"],
            "扣款": ["charge_card", "402"],
            "鉴权": ["require_auth", "login_handler"],
        }
        terms: list[str] = []
        for key, values in mapping.items():
            if key in query:
                terms.extend(values)
        terms.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query))
        return [term for term in dict.fromkeys(terms) if term not in existing][:5]
