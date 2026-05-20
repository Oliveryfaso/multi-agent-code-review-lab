from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def prepare_real_eval(
    source: str,
    input_path: Path,
    output_path: Path,
    repo_map_path: Path | None = None,
    limit: int | None = None,
    repo_prefix: str = "external_repos",
) -> dict[str, Any]:
    repo_map = _load_repo_map(repo_map_path)
    converters = {
        "swe-bench": _convert_swe_bench,
        "codesearchnet": _convert_codesearchnet,
        "github-issue": _convert_github_issue,
    }
    if source not in converters:
        raise ValueError(f"Unsupported real eval source: {source}")

    cases = []
    for row in _read_jsonl(input_path):
        case = converters[source](row, repo_map, repo_prefix)
        if case:
            cases.append(case)
        if limit and len(cases) >= limit:
            break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + ("\n" if cases else ""))
    return {
        "source": source,
        "input": str(input_path),
        "output": str(output_path),
        "case_count": len(cases),
        "repo_map_count": len(repo_map),
    }


def _convert_swe_bench(row: dict[str, Any], repo_map: dict[str, str], repo_prefix: str) -> dict[str, Any] | None:
    instance_id = row.get("instance_id")
    repo = row.get("repo")
    query = row.get("problem_statement")
    if not instance_id or not repo or not query:
        return None

    gold_patch_files = _diff_files(row.get("patch", ""))
    gold_test_files = _diff_files(row.get("test_patch", ""))
    expected_files = sorted(set(gold_patch_files + gold_test_files))
    expected_symbols = sorted(_diff_symbols(row.get("patch", "")) | _diff_symbols(row.get("test_patch", "")))

    return {
        "id": str(instance_id),
        "repo": _mapped_repo(repo, repo_map, repo_prefix),
        "query": query.strip(),
        "task_type": "real_issue_to_patch",
        "expected_files": expected_files,
        "expected_symbols": expected_symbols,
        "must_have_evidence": True,
        "test_selector": _test_selector(row),
        "metadata": {
            "source": "swe-bench",
            "upstream_repo": repo,
            "base_commit": row.get("base_commit"),
            "issue_url": row.get("issue_url"),
            "pr_url": row.get("pr_url"),
            "gold_patch_files": gold_patch_files,
            "gold_test_files": gold_test_files,
            "fail_to_pass": row.get("FAIL_TO_PASS"),
            "pass_to_pass": row.get("PASS_TO_PASS"),
        },
    }


def _convert_codesearchnet(row: dict[str, Any], repo_map: dict[str, str], repo_prefix: str) -> dict[str, Any] | None:
    path = row.get("path") or row.get("file_path")
    func_name = row.get("func_name") or row.get("function_name") or row.get("name")
    query = row.get("docstring") or row.get("comment") or " ".join(row.get("docstring_tokens", []) or [])
    repo = row.get("repo") or row.get("repository") or "codesearchnet/local"
    if not path or not query:
        return None

    return {
        "id": str(row.get("id") or row.get("url") or f"codesearchnet::{repo}::{path}::{func_name or 'unknown'}"),
        "repo": _mapped_repo(repo, repo_map, repo_prefix),
        "query": query.strip(),
        "task_type": "real_code_search",
        "expected_files": [path],
        "expected_symbols": [_short_symbol(func_name)] if func_name else [],
        "must_have_evidence": True,
        "metadata": {
            "source": "codesearchnet",
            "upstream_repo": repo,
            "language": row.get("language"),
            "url": row.get("url"),
        },
    }


def _convert_github_issue(row: dict[str, Any], repo_map: dict[str, str], repo_prefix: str) -> dict[str, Any] | None:
    repo = row.get("repo") or row.get("repository")
    title = row.get("title") or ""
    body = row.get("body") or row.get("problem_statement") or ""
    query = f"{title}\n\n{body}".strip()
    if not repo or not query:
        return None

    return {
        "id": str(row.get("id") or row.get("number") or row.get("issue_url") or f"github-issue::{repo}"),
        "repo": _mapped_repo(repo, repo_map, repo_prefix),
        "query": query,
        "task_type": row.get("task_type") or "real_github_issue",
        "expected_files": row.get("expected_files", []),
        "expected_symbols": row.get("expected_symbols", []),
        "must_have_evidence": bool(row.get("must_have_evidence", True)),
        "test_selector": row.get("test_selector"),
        "metadata": {
            "source": "github-issue",
            "upstream_repo": repo,
            "issue_url": row.get("issue_url") or row.get("html_url"),
            "labels": row.get("labels", []),
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _load_repo_map(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    return json.loads(path.read_text())


def _mapped_repo(repo: str, repo_map: dict[str, str], repo_prefix: str) -> str:
    if repo in repo_map:
        return repo_map[repo]
    return f"{repo_prefix}/{repo.replace('/', '__')}"


def _diff_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in diff_text.splitlines():
        match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if match:
            candidate = match.group(2)
            if candidate != "/dev/null" and candidate not in files:
                files.append(candidate)
    if files:
        return files

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            candidate = line.removeprefix("+++ b/")
            if candidate != "/dev/null" and candidate not in files:
                files.append(candidate)
    return files


def _diff_symbols(diff_text: str) -> set[str]:
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        hunk_match = re.match(r"@@ .* @@\s*(.*)", line)
        if hunk_match:
            symbols.update(_symbols_from_text(hunk_match.group(1)))
        if line[:1] in {"+", "-"} and not line.startswith(("+++", "---")):
            symbols.update(_symbols_from_text(line[1:]))
    return symbols


def _symbols_from_text(text: str) -> set[str]:
    symbols = set()
    for pattern in (r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)", r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"):
        symbols.update(re.findall(pattern, text))
    return symbols


def _short_symbol(symbol: str | None) -> str:
    if not symbol:
        return ""
    return symbol.split(".")[-1]


def _test_selector(row: dict[str, Any]) -> str | None:
    fail_to_pass = row.get("FAIL_TO_PASS")
    if isinstance(fail_to_pass, list) and fail_to_pass:
        return " ".join(str(item) for item in fail_to_pass[:8])
    if isinstance(fail_to_pass, str) and fail_to_pass.strip():
        return fail_to_pass.strip()
    return None
