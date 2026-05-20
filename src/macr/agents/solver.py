from __future__ import annotations

import json
from collections import Counter

from macr.providers.base import LLMProvider, LLMResponse
from macr.prompts import system_prompt
from macr.schemas import AgentAnswer, Evidence, Plan


class SolverAgent:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def solve(self, query: str, plan: Plan, evidence: list[Evidence]) -> tuple[AgentAnswer, LLMResponse | None]:
        if not evidence:
            return AgentAnswer(
                answer=(
                    "没有找到足够的代码证据。建议放宽搜索词，或补充具体文件名、函数名、"
                    "错误日志和测试命令。"
                ),
                evidence=[],
                confidence=0.2,
                next_steps=["try semantic_search after an embedding index is available"],
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
            confidence=min(0.9, 0.45 + len(ranked_evidence[:12]) * 0.05),
            next_steps=next_steps,
        )
        if not self.provider:
            return fallback, None
        try:
            llm_response = self._generate_with_llm(query, plan, fallback)
        except Exception as exc:
            fallback.answer = (
                f"{fallback.answer} LLM Provider 调用失败，已使用确定性 fallback。"
                f"错误类型：{type(exc).__name__}。"
            )
            fallback.next_steps.insert(0, "run `agent-review llm-check --provider deepseek` to verify API connectivity")
            return fallback, None
        if not llm_response.content.strip():
            return fallback, llm_response
        return AgentAnswer(
            answer=llm_response.content.strip(),
            evidence=fallback.evidence,
            confidence=fallback.confidence,
            next_steps=fallback.next_steps,
        ), llm_response

    def _generate_with_llm(self, query: str, plan: Plan, answer: AgentAnswer) -> LLMResponse:
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
                    "必须引用文件路径和行号；证据不足时明确说明不确定。"
                    "回答要简洁，适合工程师阅读。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "intent": plan.intent,
                        "evidence": evidence_payload,
                        "required_format": {
                            "summary": "one paragraph",
                            "evidence": "cite file:line",
                            "next_steps": "short actionable list",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        complete_sync = getattr(self.provider, "complete_sync", None)
        if complete_sync:
            return complete_sync(messages)
        raise TypeError("Configured provider must expose complete_sync for the sync orchestrator.")

    def _compact_evidence(self, evidence: list[Evidence]) -> list[Evidence]:
        compacted: list[Evidence] = []
        seen: set[tuple[str, int, str | None, str]] = set()
        for item in evidence:
            key = (item.file, item.line_start, item.symbol, item.source_tool)
            if key in seen:
                continue
            seen.add(key)
            compacted.append(item)
            if len(compacted) >= 8:
                break
        return compacted
