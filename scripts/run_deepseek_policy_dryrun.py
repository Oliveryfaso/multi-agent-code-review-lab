"""Run the frozen twelve-case batch with scripted, offline wire replies.

The control plane hashes gold/rubric, but no gold bytes enter model messages.
Scripted answers are protocol fixtures, not model quality scores.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import monotonic

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_batch import prepare_policy_batch
from macr.evals.deepseek_policy_batch_entry import run_next_offline_policy_cell
from macr.evals.deepseek_policy_runner import ARM_REQUESTS
from macr.providers.deepseek import DeepSeekProvider
from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.scope import open_directory, read_safe, validate_workspace_path
from macr.investigation.validation import ContractError

DATASET = PROJECT / "evals" / "deepseek-policy-dev-v1"
MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}
UNKNOWN = {"verdict": "unknown", "cause": None, "when": None, "refs": [],
           "unknowns": ["Scripted fixture: a human must assess the actual task evidence."]}


def _reply(content, *, calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message.update(reasoning_content="Scripted fixture reasoning.", tool_calls=calls)
    return {"model": MODEL["model_id"], "system_fingerprint": "offline-fixture",
        "choices": [{"message": message,
                     "finish_reason": "tool_calls" if calls is not None else "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 12,
                  "completion_tokens_details": {"reasoning_tokens": 2}}}


def _call(identifier, name, arguments):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class FixtureTransport:
    offline_fixture = True

    def __init__(self, case, arm):
        answer = json.dumps(UNKNOWN, ensure_ascii=False)
        if arm in ("A", "B"):
            self.replies = [_reply(answer)]
        elif arm == "C":
            self.replies = [_reply(answer), _reply(json.dumps({"status": "accept",
                "coverage": "complete", "reason": "Scripted protocol fixture only"}))]
        else:
            self.replies = [_reply(None, calls=[_call("read-1", "search",
                {"query": case["entrypoint"]["symbol"]})]),
                _reply(None, calls=[_call("finish-2", "finish", {"diagnosis": UNKNOWN})])]
        self.calls = []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.calls.append(json.loads(body))
        return self.replies.pop(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="New, caller-owned output directory outside source snapshots")
    args = parser.parse_args(argv)
    out = args.out.absolute()
    if (out.exists() or out.is_symlink() or not out.parent.is_dir()
            or out.is_relative_to(PROJECT) or out.is_relative_to(DATASET)):
        parser.error("output directory must be new and outside the project/dataset")
    try:
        dataset = json.loads(read_safe(DATASET, "cases.json", 64 * 1024).decode("utf-8"))
    except (OSError, ContractError, BudgetExceeded, ValueError, UnicodeError):
        parser.error("neutral dataset is invalid or too large")
    if (type(dataset) is not dict or dataset.get("version") != "deepseek-policy-dev-v1"
            or dataset.get("split") != "development"):
        parser.error("unexpected dataset version or split")
    cases = dataset.get("cases")
    if type(cases) is not list or len(cases) != 12:
        parser.error("dry-run requires exactly twelve neutral development cases")
    try:
        validate_workspace_path(out, output=True)
        with open_directory(out.parent) as parent_fd:
            os.mkdir(out.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except (OSError, ContractError):
        parser.error("output directory parent is invalid")
    started = monotonic()
    manifests = out / "manifests"
    outputs = out / "outputs"
    manifests.mkdir(mode=0o700)
    outputs.mkdir(mode=0o700)
    manifest_dirs = []
    by_id = {case["id"]: case for case in cases}
    for case in cases:
        manifest_dir = manifests / case["id"]
        prepare_policy_manifest(case, dataset_root=DATASET, out=manifest_dir, model=MODEL)
        manifest_dirs.append(manifest_dir)
    seed = 41
    batch = prepare_policy_batch(manifest_dirs, dataset_root=DATASET, project_root=PROJECT,
        control_root=out / "control", output_root=outputs, seed=seed,
        implementation_version="offline-policy-candidate-v1")
    rows = []
    for cell in batch["cells"]:
        transport = FixtureTransport(by_id[cell["task_id"]], cell["arm"])
        provider = DeepSeekProvider(model=MODEL["model_id"], transport=transport)
        completed = run_next_offline_policy_cell(out / "control", dataset_root=DATASET,
            task_id=cell["task_id"], repeat=cell["repeat"], arm=cell["arm"],
            out=cell["out"], provider=provider)
        result = completed["result"]
        rows.append({"case_id": cell["task_id"], "repeat": cell["repeat"],
            "arm": cell["arm"], "cell_id": cell["cell_id"],
            "status": result["status"], "batch_state": completed["outcome"]["state"],
            "error_code": result["error_code"], "requests": result["requests_attempted"],
            "tool_reads": result["tool_reads"], "elapsed_seconds": result["elapsed_seconds"]})
    summary = {"schema": "macr.deepseek.policy.dryrun.v1", "dataset_version": dataset["version"],
        "case_count": len(cases), "planned_task_runs": len(cases) * 4,
        "completed_task_runs": len(rows), "scripted_requests": sum(row["requests"] for row in rows),
        "max_requests": len(cases) * sum(ARM_REQUESTS.values()), "model_calls_live": 0,
        "batch_sha256": batch["batch_sha256"], "seed": seed,
        "semantic_scores": "pending_not_measured",
        "elapsed_seconds": round(monotonic() - started, 6), "rows": rows}
    data = json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if len(data) > 1024 * 1024:
        raise ValueError("dryrun_summary_limit")
    with (out / "summary.json").open("xb") as stream:
        stream.write(data)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False))
    return 0 if all(row["status"] == "semantic_pending" and
                    row["batch_state"] == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
