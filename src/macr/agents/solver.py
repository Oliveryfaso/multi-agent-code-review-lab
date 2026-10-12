from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

from macr.providers.base import LLMProvider, LLMResponse, ModelBudget, ModelRequest, ProviderError, invoke
from macr.prompts import system_prompt
from macr.schemas import AgentAnswer, Evidence, Plan

_DEFAULT_DIAGNOSTIC_VERSION = object()


class SolverAgent:
    OUTPUT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "evidence", "next_steps"],
        "properties": {
            "answer": {"type": "string", "minLength": 1},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file", "line_start", "line_end", "symbol", "source_tool"],
                    "properties": {
                        "file": {"type": "string"},
                        "line_start": {"type": "integer", "minimum": 1},
                        "line_end": {"type": "integer", "minimum": 1},
                        "symbol": {"type": ["string", "null"]},
                        "source_tool": {"type": "string"},
                    },
                },
            },
            "next_steps": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def solve(
        self, query: str, plan: Plan, evidence: list[Evidence],
        *, validation_feedback: list[str] | None = None,
        model_budget: ModelBudget | None = None,
    ) -> tuple[AgentAnswer, LLMResponse | None]:
        if not evidence:
            return AgentAnswer(
                answer=(
                    "没有找到足够的代码证据。建议放宽搜索词，或补充具体文件名、函数名、"
                    "错误日志和测试命令。"
                ),
                evidence=[],
                confidence=0.2,
                next_steps=["try semantic_search after an embedding index is available"],
                status="incomplete", error_type="missing_context",
                failure_kind="missing_context", retryable=True,
            ), None

        ranked_evidence = sorted(
            evidence,
            key=lambda item: (
                0 if "found relevant" in item.reason else 1,
                0 if item.source_tool == "parse_ast" else 1,
                -item.confidence,
                item.file,
                item.line_start,
            ),
        )
        files = Counter(item.file for item in ranked_evidence)
        top_files = ", ".join(file for file, _ in files.most_common(3))
        primary = ranked_evidence[0]
        answer = (
            f"当前任务被识别为 `{plan.intent}`。最相关的证据集中在 {top_files}。"
            f"优先查看 {primary.file}:{primary.line_start}，原因是：{primary.reason}。"
        )
        next_steps = [
            "review the cited evidence before editing",
            "run targeted tests for the touched module",
        ]
        if plan.intent in {"bug_investigation", "test_failure_analysis"}:
            next_steps.insert(0, "expand from matched symbols to callers and callees")
        fallback = AgentAnswer(
            answer=answer,
            evidence=ranked_evidence[:12],
            confidence=self._evidence_confidence(ranked_evidence[:12]),
            next_steps=next_steps,
        )
        if not self.provider:
            return fallback, None
        # Select from the full evidence set before clipping the provider context.
        # The deterministic answer keeps its original ranking and twelve-item limit.
        visible_evidence = self._compact_evidence(sorted(ranked_evidence, key=lambda item: not item.snippet.strip()))
        provider_context = replace(fallback, evidence=visible_evidence)
        try:
            llm_response = self._generate_with_llm(query, plan, provider_context,
                validation_feedback=validation_feedback, model_budget=model_budget)
        except ProviderError as exc:
            kind, retryable = self._failure_classification(exc.code)
            return self._failure(exc.code, kind, retryable), exc.response
        if not isinstance(llm_response.finish_reason, str):
            return self._failure("provider_contract", "provider_error", False), llm_response
        if llm_response.finish_reason not in {"stop", "end_turn"}:
            return self._failure("model_output_incomplete", "model_invalid", True), llm_response
        try:
            answer = self._parse_answer(llm_response.content, visible_evidence)
        except ValueError as exc:
            return self._failure(str(exc), "model_invalid", True), llm_response
        return answer, llm_response

    def _generate_with_llm(
        self, query: str, plan: Plan, answer: AgentAnswer,
        *, validation_feedback: list[str] | None = None,
        model_budget: ModelBudget | None = None,
    ) -> LLMResponse:
        evidence_payload = [
            {
                "file": item.file,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "symbol": item.symbol,
                "reason": item.reason,
                "source_tool": item.source_tool,
                "confidence": item.confidence,
                "snippet": item.snippet[:180] if item.snippet else "",
            }
            for item in self._compact_evidence(answer.evidence)
        ]
        messages = [
            {
                "role": "system",
                "content": system_prompt(
                    "你是代码审查 Agent 的 Solver。只能基于给定 evidence 回答，"
                    "返回符合给定 schema 的 JSON；evidence 必须逐项复制所用证据的五个引用字段，"
                    "不得借用未引用的检索证据。证据不足时明确说明不确定，并允许 evidence 为空。"
                    "不要提供自报置信度。回答要简洁，适合工程师阅读。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "intent": plan.intent,
                        "evidence": evidence_payload,
                        "required_format": self.OUTPUT_SCHEMA,
                        "validation_feedback": validation_feedback or [],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        request = ModelRequest(messages, response_schema=self.OUTPUT_SCHEMA)
        return invoke(self.provider, model_budget.apply(request) if model_budget else request)

    def _parse_answer(self, content: str, evidence: list[Evidence]) -> AgentAnswer:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("solver_duplicate_json_key")
                result[key] = value
            return result

        try:
            content.encode("utf-8")
            payload = json.loads(content, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise ValueError("solver_output_invalid") from None
        try:
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("solver_output_invalid") from None
        if not isinstance(payload, dict) or set(payload) != {"answer", "evidence", "next_steps"}:
            raise ValueError("solver_output_invalid")
        if not isinstance(payload["answer"], str) or not payload["answer"].strip():
            raise ValueError("solver_output_invalid")
        if not isinstance(payload["evidence"], list) or not isinstance(payload["next_steps"], list):
            raise ValueError("solver_output_invalid")
        if not all(isinstance(step, str) for step in payload["next_steps"]):
            raise ValueError("solver_output_invalid")
        authority = {self._citation_key(item): item for item in evidence}
        bound: list[Evidence] = []
        fields = {"file", "line_start", "line_end", "symbol", "source_tool"}
        # Validate every raw reference before deduplication; no repair or dropping foreign references.
        for citation in payload["evidence"]:
            if not isinstance(citation, dict) or set(citation) != fields:
                raise ValueError("solver_citation_invalid")
            if (not isinstance(citation["file"], str) or not isinstance(citation["source_tool"], str)
                    or type(citation["line_start"]) is not int or type(citation["line_end"]) is not int
                    or citation["line_start"] < 1 or citation["line_end"] < citation["line_start"]
                    or citation["symbol"] is not None and not isinstance(citation["symbol"], str)):
                raise ValueError("solver_citation_invalid")
            key = (citation["file"], citation["line_start"], citation["line_end"],
                   citation["symbol"], citation["source_tool"])
            if key not in authority:
                raise ValueError("solver_citation_outside_context")
            bound.append(authority[key])
        unique: list[Evidence] = []
        seen = set()
        for item in bound:
            key = self._citation_key(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return AgentAnswer(payload["answer"].strip(), unique,
                           self._evidence_confidence(unique) if unique else 0.2, payload["next_steps"])

    def diagnostic_candidate(self, scope, *, question: str, scenario: str,
                             assumptions: list[str], model_budget: ModelBudget,
                             prompt_variant: str = 'baseline', version=_DEFAULT_DIAGNOSTIC_VERSION):
        from macr.evals.diagnostic_compact import VERSION, build_candidate_request, parse_candidate
        if version is _DEFAULT_DIAGNOSTIC_VERSION:
            version = VERSION
        request = build_candidate_request(scope, question=question, scenario=scenario,
                                          assumptions=assumptions, prompt_variant=prompt_variant, version=version)
        request.metadata.update({"role": "diagnostic_candidate", "run_id": scope.case_id,
                                 "attempt_id": scope.attempt_id})
        return self._diagnostic_call(
            request, lambda content: parse_candidate(content, scope, version=version), model_budget)

    def diagnostic_verification(self, scope, candidate_request: ModelRequest, candidate: dict,
                                *, model_budget: ModelBudget):
        from macr.evals.diagnostic_compact import build_verification_request, validate_verification
        request = build_verification_request(candidate_request, scope, candidate)
        request.metadata.update({"role": "diagnostic_verifier", "run_id": scope.case_id,
                                 "attempt_id": scope.attempt_id})
        return self._diagnostic_call(request, lambda content: validate_verification(
            content, candidate, scope, version=request.metadata['diagnostic_protocol']), model_budget)

    def _diagnostic_call(self, request: ModelRequest, parse, budget: ModelBudget):
        response = None
        try:
            request = budget.apply(request)
            if self.provider is None:
                raise ProviderError("provider_unavailable", failure_kind="invalid_contract")
            response = invoke(self.provider, request)
            if (not isinstance(response.finish_reason, str)
                    or type(response.tool_calls) is not list or response.tool_calls):
                raise ProviderError("provider_contract", response=response, failure_kind="invalid_contract")
            if response.finish_reason not in {"stop", "end_turn"}:
                raise ProviderError("model_output_incomplete", response=response, failure_kind="incomplete")
            return parse(response.content), response, None, request
        except ProviderError as error:
            # Failed output keeps its known envelope; it never becomes a candidate/final report.
            return None, error.response or response, {
                "error_type": error.code, "failure_kind": error.failure_kind or "invalid_contract",
            }, request

    def _failure(self, code: str, kind: str, retryable: bool) -> AgentAnswer:
        return AgentAnswer(
            f"模型步骤未完成（{code}）。该结果不构成代码诊断。", [], 0.0,
            ["retry the failed model step within the run budget" if retryable
             else "check the configured provider and inspect the recorded failure"],
            status="failed", error_type=code, failure_kind=kind, retryable=retryable,
        )

    def _failure_classification(self, code: str) -> tuple[str, bool]:
        if code in {"model_timeout", "model_transport", "model_busy"}:
            return "transient", True
        if code in {"model_output_invalid", "model_output_incomplete"}:
            return "model_invalid", True
        return "provider_error", False

    def _citation_key(self, item: Evidence) -> tuple[str, int, int, str | None, str]:
        return (item.file, item.line_start, item.line_end, item.symbol, item.source_tool)

    def _evidence_confidence(self, evidence: list[Evidence]) -> float:
        positions = {(item.file, item.line_start, item.line_end) for item in evidence}
        return min(0.9, 0.45 + len(positions) * 0.05)

    def _compact_evidence(self, evidence: list[Evidence]) -> list[Evidence]:
        compacted: list[Evidence] = []
        seen: set[tuple[str, int, int, str | None, str]] = set()
        for item in evidence:
            key = self._citation_key(item)
            if key in seen:
                continue
            seen.add(key)
            compacted.append(item)
            if len(compacted) >= 8:
                break
        return compacted
