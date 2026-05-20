from __future__ import annotations

from typing import Any


def diff_review_report(trace) -> dict[str, Any]:
    board = trace.board or {}
    items = board.get("pr_review") or []
    return items[-1].get("payload", {}) if items else {}


def to_github_comments(trace) -> list[dict[str, Any]]:
    report = diff_review_report(trace)
    comments = []
    for item in report.get("comments", []) or []:
        body = f"[{item.get('severity', 'info')}] {item.get('message', '')}"
        if item.get("evidence"):
            body = f"{body}\n\nEvidence: `{item['evidence']}`"
        comments.append(
            {
                "path": item.get("file"),
                "line": item.get("line"),
                "side": "RIGHT",
                "body": body,
                "severity": item.get("severity", "low"),
                "category": item.get("category", "review"),
            }
        )
    return comments


def to_sarif(trace) -> dict[str, Any]:
    report = diff_review_report(trace)
    rules: dict[str, dict[str, Any]] = {}
    results = []
    for item in report.get("comments", []) or []:
        rule_id = str(item.get("category") or "macr.review")
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "name": rule_id.replace("_", " ").title(),
                "shortDescription": {"text": rule_id},
            },
        )
        results.append(
            {
                "ruleId": rule_id,
                "level": _sarif_level(str(item.get("severity") or "low")),
                "message": {"text": str(item.get("message") or "")},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": str(item.get("file") or "")},
                            "region": {"startLine": int(item.get("line") or 1)},
                        }
                    }
                ],
                "properties": {
                    "severity": item.get("severity"),
                    "evidence": item.get("evidence", ""),
                },
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Multi-Agent Code Review Lab",
                        "informationUri": "https://github.com/",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "properties": {
                    "risk_level": report.get("risk_level", "n/a"),
                    "changed_files": report.get("changed_files", []),
                    "summary": report.get("summary", ""),
                    "test_suggestions": report.get("test_suggestions", []),
                },
            }
        ],
    }


def _sarif_level(severity: str) -> str:
    if severity == "high":
        return "error"
    if severity == "medium":
        return "warning"
    return "note"
