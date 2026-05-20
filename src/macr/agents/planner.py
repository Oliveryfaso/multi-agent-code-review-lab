from __future__ import annotations

import json
import re

from macr.providers.base import LLMProvider, LLMResponse
from macr.prompts import system_prompt
from macr.schemas import Plan, PlanStep


class RuleBasedPlanner:
    """Deterministic planner used before the LLM API is connected."""

    def plan(self, query: str) -> Plan:
        terms = self._search_terms(query)
        intent = self._intent(query)
        steps = [
            PlanStep("find textual entrypoints and candidate files", "text_search", " ".join(terms)),
            PlanStep("extract functions, classes, imports, and call hints", "ast"),
            PlanStep("build symbol definitions and references", "symbol_graph"),
        ]
        if intent in {"bug_investigation", "test_failure_analysis", "patch_planning", "light_patch"}:
            steps.append(PlanStep("inspect recent changes related to candidate files", "git"))
        if intent in {"test_failure_analysis", "light_patch"}:
            steps.append(PlanStep("run the relevant tests when a selector is available", "test_runner"))
        return Plan(
            intent=intent,
            risk_level="medium" if intent != "code_qa" else "low",
            steps=steps,
            search_terms=terms,
        )

    def _intent(self, query: str) -> str:
        q = query.lower()
        if any(token in q for token in ["pytest", "test", "测试", "失败日志", "报错"]):
            return "test_failure_analysis"
        if any(token in q for token in ["修复", "fix", "patch", "修改"]):
            return "light_patch"
        if any(token in q for token in ["为什么", "bug", "失败", "不能", "异常"]):
            return "bug_investigation"
        return "code_qa"

    def _search_terms(self, query: str) -> list[str]:
        mapping = {
            "登录": ["login", "auth", "token"],
            "鉴权": ["auth", "authorization", "require_auth"],
            "接口": ["api", "route", "handler"],
            "调用": ["call", "reference"],
            "测试": ["test", "pytest"],
            "失败": ["error", "fail", "exception"],
            "权限": ["permission", "auth"],
            "资料": ["profile", "user"],
            "仪表盘": ["dashboard", "streak"],
            "订单": ["order", "payment"],
            "支付": ["payment", "checkout"],
            "库存": ["inventory", "stock"],
            "通知": ["notification", "email"],
            "总价": ["total", "calculate_total"],
            "价格": ["price", "calculate_total"],
            "计算": ["calculate", "calculate_total"],
            "银行卡": ["card", "charge_card"],
            "扣款": ["charge", "charge_card"],
            "402": ["402", "charge_card"],
            "全局状态": ["INVENTORY", "ORDERS", "OUTBOX", "append", "status"],
            "状态码": ["status", "400", "401", "402", "404", "200"],
        }
        english_mapping = {
            "escape": ["escape", "_escape_inner", "Markup"],
            "escaping": ["escape", "_escape_inner", "Markup"],
            "fallback": ["_native", "_escape_inner"],
            "pure python": ["_native", "_escape_inner"],
            "proxy": ["Proxy", "test_proxy", "__class__", "escape"],
            "format": ["format", "__html_format__", "EscapeFormatter", "Markup"],
            "formatting": ["format", "__html_format__", "EscapeFormatter", "Markup"],
            "striptags": ["striptags", "test_escaping"],
            "comments": ["striptags", "test_escaping"],
            "unescape": ["unescape", "striptags"],
        }
        terms: list[str] = []
        q_lower = query.lower()
        for key, values in mapping.items():
            if key in query:
                terms.extend(values)
        for key, values in english_mapping.items():
            if key in q_lower:
                terms.extend(values)
        terms.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", q_lower))
        deduped = []
        for term in terms or [query.strip()]:
            if term and term not in deduped:
                deduped.append(term)
        return deduped[:8]


class LlmPlanner:
    """LLM-enhanced planner with deterministic fallback."""

    def __init__(self, provider: LLMProvider, fallback: RuleBasedPlanner | None = None) -> None:
        self.provider = provider
        self.fallback = fallback or RuleBasedPlanner()

    def plan(self, query: str) -> tuple[Plan, LLMResponse | None]:
        fallback_plan = self.fallback.plan(query)
        complete_sync = getattr(self.provider, "complete_sync", None)
        if not complete_sync:
            return fallback_plan, None
        messages = [
            {
                "role": "system",
                "content": system_prompt(
                    "你是代码库多 Agent 系统的 Planner。"
                    "请只输出 JSON，不要 Markdown。"
                    "可用 tool_family: text_search, ast, symbol_graph, git, test_runner。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "allowed_intents": [
                            "code_qa",
                            "bug_investigation",
                            "test_failure_analysis",
                            "patch_planning",
                            "light_patch",
                        ],
                        "output_schema": {
                            "intent": "string",
                            "risk_level": "low|medium|high",
                            "search_terms": ["string"],
                            "steps": [{"goal": "string", "tool_family": "string", "query": "string"}],
                            "max_tool_calls": 12,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = complete_sync(messages, response_schema={"type": "object"})
            plan = self._parse_response(response.content, fallback_plan)
        except Exception:
            return fallback_plan, None
        return plan, response

    def _parse_response(self, content: str, fallback: Plan) -> Plan:
        data = json.loads(content)
        allowed_tools = {"text_search", "ast", "symbol_graph", "git", "test_runner"}
        steps = []
        for item in data.get("steps", []):
            tool_family = item.get("tool_family")
            if tool_family not in allowed_tools:
                continue
            steps.append(
                PlanStep(
                    goal=str(item.get("goal") or tool_family),
                    tool_family=tool_family,
                    query=item.get("query"),
                )
            )
        if not steps:
            steps = fallback.steps
        if not any(step.tool_family == "text_search" for step in steps):
            steps.insert(0, PlanStep("find textual entrypoints and candidate files", "text_search"))
        if not any(step.tool_family == "ast" for step in steps):
            steps.append(PlanStep("extract functions, classes, imports, and call hints", "ast"))
        return Plan(
            intent=str(data.get("intent") or fallback.intent),
            risk_level=str(data.get("risk_level") or fallback.risk_level),
            steps=steps[:12],
            max_tool_calls=int(data.get("max_tool_calls") or fallback.max_tool_calls),
            search_terms=self._terms(data.get("search_terms"), fallback.search_terms),
        )

    def _terms(self, raw_terms, fallback: list[str]) -> list[str]:
        if not isinstance(raw_terms, list):
            return fallback
        terms = []
        for term in raw_terms:
            if isinstance(term, str) and term.strip() and term.strip() not in terms:
                terms.append(term.strip())
        return terms[:8] or fallback
