from __future__ import annotations

import difflib
import json
from pathlib import Path

from macr.providers.base import LLMProvider, LLMResponse
from macr.prompts import system_prompt
from macr.schemas import Evidence, PatchArtifact, Plan


class PatchAgent:
    """Generates conservative patch artifacts without mutating the target repo."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def propose(
        self,
        repo_path: Path,
        query: str,
        plan: Plan,
        evidence: list[Evidence],
        prefer_llm: bool = False,
    ) -> tuple[PatchArtifact, LLMResponse | None]:
        if prefer_llm and self.provider:
            artifact, response = self._llm_patch(repo_path, query, plan, evidence)
            if artifact.diff.strip():
                return artifact, response
        return self.template_patch(repo_path, query), None

    def propose_candidates(
        self,
        repo_path: Path,
        query: str,
        plan: Plan,
        evidence: list[Evidence],
        prefer_llm: bool = False,
    ) -> tuple[list[PatchArtifact], LLMResponse | None]:
        candidates: list[PatchArtifact] = []
        response = None
        if prefer_llm and self.provider:
            llm_artifact, response = self._llm_patch(repo_path, query, plan, evidence)
            llm_artifact.source = "llm"
            candidates.append(llm_artifact)
        template = self.template_patch(repo_path, query)
        if not any(candidate.diff == template.diff and candidate.source == template.source for candidate in candidates):
            candidates.append(template)
        if not candidates:
            candidates.append(self.template_patch(repo_path, query))
        return candidates, response

    def template_patch(self, repo_path: Path, query: str) -> PatchArtifact:
        lower_query = query.lower()
        if "402" in lower_query or "银行卡" in query or "扣款" in query:
            return self._payment_error_patch(repo_path)
        if "总价" in query or "价格" in query or "calculate_total" in lower_query:
            return self._calculate_total_patch(repo_path)
        return PatchArtifact(
            summary="No safe patch template matched this query. Returning an analysis-only patch artifact.",
            diff="",
            target_files=[],
            source="template",
            verification={"patch_apply_check": "skipped", "reason": "no matching safe patch template"},
        )

    def _llm_patch(
        self,
        repo_path: Path,
        query: str,
        plan: Plan,
        evidence: list[Evidence],
    ) -> tuple[PatchArtifact, LLMResponse | None]:
        complete_sync = getattr(self.provider, "complete_sync", None)
        if not complete_sync:
            return self.template_patch(repo_path, query), None
        context = self._file_context(repo_path, evidence)
        messages = [
            {
                "role": "system",
                "content": system_prompt(
                    "你是 Patch Agent。只基于给定文件上下文生成最小 unified diff。"
                    "不要解释，不要 Markdown。只输出 JSON: "
                    '{"summary": "...", "target_files": ["..."], "diff": "..."}。'
                    "diff 必须使用 a/path 和 b/path。不要修改未提供的文件。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": query,
                        "intent": plan.intent,
                        "evidence": [
                            {
                                "file": item.file,
                                "line_start": item.line_start,
                                "line_end": item.line_end,
                                "symbol": item.symbol,
                                "reason": item.reason,
                            }
                            for item in evidence[:12]
                        ],
                        "files": context,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = complete_sync(messages, response_schema={"type": "object"})
            data = json.loads(response.content)
        except Exception:
            return self.template_patch(repo_path, query), None
        return PatchArtifact(
            summary=str(data.get("summary") or "LLM-generated patch candidate"),
            diff=str(data.get("diff") or ""),
            target_files=[str(item) for item in data.get("target_files", []) if isinstance(item, str)],
            source="llm",
        ), response

    def _file_context(self, repo_path: Path, evidence: list[Evidence]) -> dict[str, str]:
        files: dict[str, str] = {}
        for item in evidence:
            if len(files) >= 4:
                break
            if not item.file.endswith(".py") or item.file in files:
                continue
            path = repo_path / item.file
            try:
                lines = path.read_text().splitlines()
            except OSError:
                continue
            start = max(1, item.line_start - 20)
            end = min(len(lines), item.line_end + 20)
            numbered = [f"{line_no}: {lines[line_no - 1]}" for line_no in range(start, end + 1)]
            files[item.file] = "\n".join(numbered)
        return files

    def _payment_error_patch(self, repo_path: Path) -> PatchArtifact:
        rel = "backend/payments.py"
        path = repo_path / rel
        original = path.read_text().splitlines(keepends=True)
        updated = []
        for line in original:
            if 'return {"status": 402, "error": charge["error"]}' in line:
                updated.append('        return {"status": 402, "error": charge["error"], "retryable": True}\n')
            else:
                updated.append(line)
        return PatchArtifact(
            summary="Add retryable metadata to card charge failures so callers can distinguish payment retry cases.",
            diff=self._diff(rel, original, updated),
            target_files=[rel],
            source="template",
        )

    def _calculate_total_patch(self, repo_path: Path) -> PatchArtifact:
        rel = "backend/orders.py"
        path = repo_path / rel
        original = path.read_text().splitlines(keepends=True)
        updated = []
        for line in original:
            if "return prices.get(item_id, 0) * quantity" in line:
                updated.append("    if quantity <= 0:\n")
                updated.append('        raise ValueError("quantity must be positive")\n')
                updated.append("    return prices.get(item_id, 0) * quantity\n")
            else:
                updated.append(line)
        return PatchArtifact(
            summary="Validate positive quantities before calculating an order total.",
            diff=self._diff(rel, original, updated),
            target_files=[rel],
            source="template",
        )

    def _diff(self, rel_file: str, original: list[str], updated: list[str]) -> str:
        return "".join(
            difflib.unified_diff(
                original,
                updated,
                fromfile=f"a/{rel_file}",
                tofile=f"b/{rel_file}",
            )
        )
