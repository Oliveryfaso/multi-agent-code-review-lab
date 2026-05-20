from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from macr.agents.code_smell import CodeSmellAgent
from macr.agents.orchestrator import Orchestrator
from macr.costs import estimate_llm_cost
from macr.evals.real_data import prepare_real_eval
from macr.evals.runner import EvalRunner
from macr.pr_outputs import to_github_comments, to_sarif
from macr.providers.deepseek import DeepSeekProvider
from macr.viewer import TraceViewer


def _provider(name: str):
    if name == "deepseek":
        return DeepSeekProvider()
    return None


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-review")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="Run one codebase question.")
    ask_parser.add_argument("query")
    ask_parser.add_argument("--repo", required=True, type=Path)
    ask_parser.add_argument("--test-selector")
    ask_parser.add_argument("--provider", default="mock", choices=["mock", "deepseek"])
    ask_parser.add_argument("--llm-planner", action="store_true")
    ask_parser.add_argument("--clarification", help="Human clarification from a prior final-review question.")
    ask_parser.add_argument("--json", action="store_true")

    patch_parser = subparsers.add_parser("patch", help="Generate and verify a safe patch artifact.")
    patch_parser.add_argument("query")
    patch_parser.add_argument("--repo", required=True, type=Path)
    patch_parser.add_argument("--test-selector")
    patch_parser.add_argument("--provider", default="mock", choices=["mock", "deepseek"])
    patch_parser.add_argument("--llm-planner", action="store_true")
    patch_parser.add_argument("--llm-patch", action="store_true")
    patch_parser.add_argument("--clarification", help="Human clarification from a prior final-review question.")
    patch_parser.add_argument("--out-dir", default=Path("patches"), type=Path)
    patch_parser.add_argument("--json", action="store_true")

    diff_parser = subparsers.add_parser("review-diff", help="Review a unified diff and produce PR-style findings.")
    diff_parser.add_argument("--repo", required=True, type=Path)
    diff_parser.add_argument("--diff-file", type=Path, help="Unified diff file. Defaults to `git diff` from --repo.")
    diff_parser.add_argument("--format", default="text", choices=["text", "json", "sarif", "github"], help="Output format.")
    diff_parser.add_argument("--output", type=Path, help="Optional output file for json, sarif, or github formats.")
    diff_parser.add_argument("--json", action="store_true")

    eval_parser = subparsers.add_parser("eval", help="Run an offline eval set.")
    eval_parser.add_argument("--eval-file", required=True, type=Path)
    eval_parser.add_argument("--repo-root", default=Path("."), type=Path)
    eval_parser.add_argument("--report", default=Path("reports/eval_report.md"), type=Path)
    eval_parser.add_argument("--provider", default="mock", choices=["mock", "deepseek"])
    eval_parser.add_argument("--llm-planner", action="store_true")

    real_eval_parser = subparsers.add_parser("prepare-real-eval", help="Convert public real-world datasets into MACR eval JSONL.")
    real_eval_parser.add_argument("--source", required=True, choices=["swe-bench", "codesearchnet", "github-issue"])
    real_eval_parser.add_argument("--input", required=True, type=Path)
    real_eval_parser.add_argument("--output", required=True, type=Path)
    real_eval_parser.add_argument("--repo-map", type=Path, help="JSON mapping from upstream repo name to a local repo path.")
    real_eval_parser.add_argument("--repo-prefix", default="external_repos")
    real_eval_parser.add_argument("--limit", type=int)

    smell_parser = subparsers.add_parser("code-smell", help="Analyze code smell ratio and maintainability hotspots.")
    smell_parser.add_argument("--repo", required=True, type=Path)
    smell_parser.add_argument("--json", action="store_true")

    check_parser = subparsers.add_parser("llm-check", help="Check a configured LLM provider.")
    check_parser.add_argument("--provider", default="deepseek", choices=["deepseek"])
    check_parser.add_argument("--prompt", default="Reply with JSON: {\"ok\": true}")

    view_parser = subparsers.add_parser("view", help="Start the local trace viewer.")
    view_parser.add_argument("--host", default="127.0.0.1")
    view_parser.add_argument("--port", default=8765, type=int)
    view_parser.add_argument("--trace", default=Path("traces/latest.json"), type=Path)
    view_parser.add_argument("--report", default=Path("reports/phase1_eval.md"), type=Path)
    view_parser.add_argument("--real-report", default=Path("reports/real_data_showcase.md"), type=Path)
    view_parser.add_argument("--patch-dir", default=Path("patches"), type=Path)

    args = parser.parse_args()
    if args.command == "ask":
        query = _with_clarification(args.query, args.clarification)
        trace = Orchestrator(provider=_provider(args.provider), use_llm_planner=args.llm_planner).run(
            args.repo,
            query,
            args.test_selector,
        )
        _print_trace(trace, args.json)
    elif args.command == "patch":
        query = _with_clarification(args.query, args.clarification)
        trace = Orchestrator(provider=_provider(args.provider), use_llm_planner=args.llm_planner).run_patch(
            args.repo,
            query,
            args.test_selector,
            prefer_llm_patch=args.llm_patch,
        )
        args.out_dir.mkdir(parents=True, exist_ok=True)
        patch_path = args.out_dir / f"{trace.task_id}.patch"
        if trace.patch and trace.patch.diff:
            patch_path.write_text(trace.patch.diff)
        if args.json:
            print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_trace(trace, False)
            if trace.patch:
                print("\nPatch:")
                print(f"- Summary: {trace.patch.summary}")
                print(f"- Source: {trace.patch.source}")
                print(f"- Target files: {', '.join(trace.patch.target_files) or 'none'}")
                print(f"- Verification: {json.dumps(trace.patch.verification, ensure_ascii=False)}")
                if trace.patch.diff:
                    print(f"- Patch file: {patch_path}")
    elif args.command == "review-diff":
        diff_text = _read_diff(args.repo, args.diff_file)
        trace = Orchestrator().run_diff_review(args.repo, diff_text)
        output_format = "json" if args.json else args.format
        if output_format == "json":
            _emit_json(trace.to_dict(), args.output)
        elif output_format == "sarif":
            _emit_json(to_sarif(trace), args.output)
        elif output_format == "github":
            _emit_json(to_github_comments(trace), args.output)
        else:
            _print_diff_review(trace)
    elif args.command == "eval":
        metrics = EvalRunner(
            orchestrator=Orchestrator(provider=_provider(args.provider), use_llm_planner=args.llm_planner)
        ).run(
            args.eval_file,
            args.repo_root,
            args.report,
        )
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        print(f"Report: {args.report}")
    elif args.command == "prepare-real-eval":
        summary = prepare_real_eval(
            source=args.source,
            input_path=args.input,
            output_path=args.output,
            repo_map_path=args.repo_map,
            limit=args.limit,
            repo_prefix=args.repo_prefix,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "code-smell":
        report = CodeSmellAgent().analyze(args.repo.resolve())
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_code_smell(report)
    elif args.command == "llm-check":
        if args.provider == "deepseek":
            provider = DeepSeekProvider()
            try:
                response = provider.complete_sync(
                    [
                        {"role": "system", "content": "You are a concise API connectivity checker."},
                        {"role": "user", "content": args.prompt},
                    ],
                    response_schema={"type": "object"},
                )
            except ValueError as exc:
                raise SystemExit(
                    f"{exc}\n"
                    "Set it with:\n"
                    "  set -a\n"
                    "  source .env\n"
                    "  set +a"
                ) from exc
            print(json.dumps({
                "provider": response.provider,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "usage": response.usage,
                "cost": estimate_llm_cost(response),
                "content": response.content,
            }, ensure_ascii=False, indent=2))
    elif args.command == "view":
        TraceViewer(
            root=Path("."),
            trace_path=args.trace,
            report_path=args.report,
            real_report_path=args.real_report,
            patch_dir=args.patch_dir,
        ).serve(args.host, args.port)


def _print_trace(trace, as_json: bool) -> None:
    if as_json:
        print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2))
        return
    metrics = trace.metrics or {}
    final_review = metrics.get("final_review", {})
    code_smell = metrics.get("code_smell", {})
    contract = metrics.get("contract_validation", {})
    tool_total = metrics.get("tool_call_count", len(trace.tool_calls))
    failed_tools = metrics.get("failed_tool_calls", 0)
    empty_recall = metrics.get("empty_recall_count", 0)

    print("\n# Multi-Agent Review Result")
    print(_metric_grid([
        ("Intent", trace.plan.intent if trace.plan else "n/a"),
        ("Confidence", _bar(float(trace.answer.confidence if trace.answer else 0))),
        ("Final Audit", "ok" if final_review.get("ok") else "review"),
        ("Contract", "ok" if contract.get("ok") else "check"),
        ("Code Smell", f"{int(float(code_smell.get('smell_ratio') or 0) * 100)}% / {code_smell.get('severity', 'n/a')}"),
        ("Tools", f"{tool_total} calls, {failed_tools} failed, {empty_recall} miss"),
    ]))
    print("\n## Answer")
    print(trace.answer.answer if trace.answer else "No answer generated.")
    if trace.answer:
        print("\n## Evidence Map")
        source_counts: dict[str, int] = {}
        for item in trace.answer.evidence:
            source_counts[item.source_tool or "unknown"] = source_counts.get(item.source_tool or "unknown", 0) + 1
        for source, count in sorted(source_counts.items(), key=lambda pair: pair[1], reverse=True):
            print(f"- {source}: {_bar(count / max(1, len(trace.answer.evidence)))} {count}")
        print("\n## Top Evidence")
        for item in trace.answer.evidence[:8]:
            symbol = f" `{item.symbol}`" if item.symbol else ""
            print(f"- {item.file}:{item.line_start}{symbol} - {item.reason}")
    if code_smell:
        print("\n## Code Smell Risk")
        print(f"- Ratio: {_bar(float(code_smell.get('smell_ratio') or 0))} {code_smell.get('smell_ratio', 0)}")
        print(f"- Severity: {code_smell.get('severity', 'n/a')}")
        for hotspot in (code_smell.get("hotspots") or [])[:5]:
            symbol = f" `{hotspot.get('symbol')}`" if hotspot.get("symbol") else ""
            print(f"- {hotspot.get('file')}:{hotspot.get('line')}{symbol} [{hotspot.get('kind')}] {hotspot.get('reason')}")
    if final_review.get("human_review_required"):
        print("\n## Human Review Required")
        for question in final_review.get("questions", []):
            print(f"- {question}")
        print("\nRerun with --clarification \"...\" after answering the relevant question.")
    print(f"\nTrace: traces/{trace.task_id}.json")


def _read_diff(repo: Path, diff_file: Path | None) -> str:
    if diff_file:
        return diff_file.read_text()
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", "--unified=80"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git diff failed for {repo}: {result.stderr.strip()}")
    if not result.stdout.strip():
        raise SystemExit("No diff content found. Pass --diff-file or create local git changes first.")
    return result.stdout


def _emit_json(payload, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        print(f"Wrote: {output}")
        return
    print(text)


def _print_diff_review(trace) -> None:
    board = trace.board or {}
    review_items = board.get("pr_review") or []
    report = review_items[-1]["payload"] if review_items else {}
    final_review = (trace.metrics or {}).get("final_review", {})
    print("\n# Multi-Agent Diff Review")
    print(_metric_grid([
        ("Risk", report.get("risk_level", "n/a")),
        ("Changed Files", report.get("changed_file_count", 0)),
        ("Lines", f"+{report.get('added_lines', 0)} / -{report.get('removed_lines', 0)}"),
        ("Comments", len(report.get("comments", []) or [])),
        ("Final Audit", "ok" if final_review.get("ok") else "review"),
    ]))
    print("\n## Summary")
    print(report.get("summary", "No diff review summary generated."))
    comments = report.get("comments") or []
    if comments:
        print("\n## Review Comments")
        for item in comments[:12]:
            print(f"- [{item.get('severity')}] {item.get('file')}:{item.get('line')} - {item.get('message')}")
    print("\n## Test Suggestions")
    for item in report.get("test_suggestions", []) or []:
        print(f"- {item}")
    if final_review.get("human_review_required"):
        print("\n## Human Review Required")
        for question in final_review.get("questions", []):
            print(f"- {question}")
    print(f"\nTrace: traces/{trace.task_id}.json")


def _with_clarification(query: str, clarification: str | None) -> str:
    if not clarification:
        return query
    return f"{query}\n\nHuman clarification: {clarification}"


def _print_code_smell(report: dict) -> None:
    print("\n# Code Smell Report")
    print(f"- Ratio: {_bar(float(report.get('smell_ratio') or 0))} {report.get('smell_ratio', 0)}")
    print(f"- Severity: {report.get('severity', 'n/a')}")
    print(f"- Units: {report.get('smelly_units', 0)} smelly / {report.get('total_units', 0)} total")
    print("\n## Hotspots")
    hotspots = report.get("hotspots") or []
    if not hotspots:
        print("- No major hotspots detected.")
    for item in hotspots[:8]:
        symbol = f" `{item.get('symbol')}`" if item.get("symbol") else ""
        print(f"- {item.get('file')}:{item.get('line')}{symbol} [{item.get('kind')}] {item.get('reason')}")
    if report.get("suggestions"):
        print("\n## Suggestions")
        for item in report["suggestions"]:
            print(f"- {item}")


def _bar(value: float, width: int = 14) -> str:
    value = max(0.0, min(1.0, value))
    filled = int(round(value * width))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {int(value * 100)}%"


def _metric_grid(items: list[tuple[str, object]]) -> str:
    return "\n".join(f"- {label}: {value}" for label, value in items)


if __name__ == "__main__":
    main()
