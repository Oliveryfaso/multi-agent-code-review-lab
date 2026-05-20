from __future__ import annotations

import re

from macr.schemas import AgentAnswer, Evidence, FinalReviewReport, Plan


class FinalReviewAgent:
    """Audits whether agent artifacts are coherent enough to trust or need fallback."""

    def review(
        self,
        query: str,
        plan: Plan,
        answer: AgentAnswer | None,
        evidence: list[Evidence],
        tool_calls: list[dict],
        board: dict | None = None,
        patch: object | None = None,
        contract_report: dict | None = None,
        retry_count: int = 0,
    ) -> FinalReviewReport:
        issues: list[str] = []
        questions: list[str] = []
        answer_confidence = answer.confidence if answer else 0.0
        answer_evidence = answer.evidence if answer else []
        evidence_count = len(answer_evidence or evidence)
        failed_tools = [call for call in tool_calls if not call.get("ok") and call.get("error_type") != "empty_recall"]
        empty_recall = [call for call in tool_calls if call.get("error_type") == "empty_recall"]

        if not answer:
            issues.append("solver did not produce an answer")
            questions.append("Can you provide the target file, function name, or failing test?")
        if answer_confidence < 0.55:
            issues.append(f"answer confidence too low: {answer_confidence:.2f}")
            questions.append("Which file, function, endpoint, or error log should be prioritized?")
        if evidence_count < 3:
            issues.append(f"evidence too thin: {evidence_count}")
            questions.append("Can you provide one more concrete symbol, filename, or stack trace line?")
        if failed_tools:
            issues.append(f"{len(failed_tools)} hard tool calls failed")
            questions.append("Should the failed tool result be ignored, or should the run be retried after fixing the tool input?")
        if len(empty_recall) >= 4 and evidence_count < 6:
            issues.append("retrieval had repeated empty recall with limited evidence")
            questions.append("Can you rephrase the task with concrete code identifiers?")
        uncovered_terms = self._uncovered_query_terms(query, answer_evidence or evidence)
        if uncovered_terms:
            issues.append(f"query terms not covered by evidence: {', '.join(uncovered_terms)}")
            questions.append(f"These terms were not found in evidence: {', '.join(uncovered_terms)}. Should they be ignored or should you provide the relevant file/function?")
        if contract_report and not contract_report.get("ok", True):
            issues.append("agent board contract validation failed")
            questions.append("Should this run be treated as incomplete and regenerated?")
        smell_report = self._code_smell_report(board)
        if board is not None and not smell_report:
            issues.append("code smell report missing from agent board")
            questions.append("Should the run be regenerated with the Code Smell Agent enabled?")
        if smell_report and smell_report.get("severity") == "high":
            ratio = float(smell_report.get("smell_ratio") or 0)
            issues.append(f"high code smell ratio detected: {ratio:.2f}")
            questions.append("Should high-maintainability-risk hotspots be refactored before accepting the review or patch?")
        if patch:
            verification = getattr(patch, "verification", {}) or {}
            if verification.get("patch_apply_check") == "failed":
                issues.append("selected patch does not apply")
                questions.append("Should the patch be regenerated with a narrower target file?")
            if verification.get("test_check") == "failed":
                issues.append("selected patch applies but tests failed")
                questions.append("Should the failing tests be treated as expected, or should the patch be regenerated?")

        ok = not issues
        return FinalReviewReport(
            ok=ok,
            confidence=self._confidence(answer_confidence, evidence_count, issues),
            issues=issues,
            retry_recommended=bool(issues) and retry_count == 0,
            human_review_required=bool(issues) and retry_count > 0,
            questions=list(dict.fromkeys(questions))[:4],
        )

    def _confidence(self, answer_confidence: float, evidence_count: int, issues: list[str]) -> float:
        score = min(0.95, answer_confidence * 0.7 + min(evidence_count, 12) / 12 * 0.3)
        score -= min(0.5, len(issues) * 0.12)
        return round(max(0.0, score), 3)

    def recovery_terms(self, query: str, plan: Plan, issues: list[str]) -> list[str]:
        terms = list(plan.search_terms)
        for token in query.replace("？", " ").replace("?", " ").replace("，", " ").split():
            normalized = token.strip("`'\".,:;()[]{}").lower()
            if len(normalized) >= 4:
                terms.append(normalized)
        if any("evidence too thin" in issue for issue in issues):
            terms.extend(["handler", "process", "validate", "test"])
        return [term for term in dict.fromkeys(terms) if term][:8]

    def _code_smell_report(self, board: dict | None) -> dict | None:
        if not board:
            return None
        for item in board.get("code_quality", []):
            if item.get("kind") == "smell_report" and isinstance(item.get("payload"), dict):
                return item["payload"]
        return None

    def _uncovered_query_terms(self, query: str, evidence: list[Evidence]) -> list[str]:
        stopwords = {
            "where",
            "what",
            "which",
            "when",
            "should",
            "would",
            "could",
            "with",
            "from",
            "into",
            "handler",
            "process",
            "payment",
            "order",
            "profile",
            "dashboard",
            "login",
            "token",
            "test",
            "tests",
        }
        terms = []
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", query):
            term = raw.lower()
            if term not in stopwords and term not in terms:
                terms.append(term)
        if not terms:
            return []
        chunks: list[str] = []
        for item in evidence:
            chunks.extend(
                [
                    item.file,
                    item.symbol or "",
                    item.source_tool,
                    item.reason,
                    item.snippet,
                ]
            )
        haystack = " ".join(chunks).lower()
        suspicious = [term for term in terms if term not in haystack]
        return suspicious[:3] if len(suspicious) >= 1 and any(term in {"oauth", "redis", "graphql"} for term in suspicious) else []
