"""Explicit offline-only entry for a frozen policy batch.

A fixture cannot enable the remote transport or run in a live-policy batch.
One claim is consumed before a model step.
"""
from __future__ import annotations

from pathlib import Path

from macr.evals.deepseek_policy_batch import (PolicyBatchError, _load_batch,
    claim_policy_cell, record_policy_cell)
from macr.evals.deepseek_policy_runner import run_policy_arm
from macr.providers.deepseek import DeepSeekProvider


def run_next_offline_policy_cell(control_root, *, dataset_root, task_id, repeat, arm,
                                 out, provider):
    """Claim the frozen next cell, execute one fixture run and close from receipts."""
    if (not isinstance(provider, DeepSeekProvider) or provider.allow_remote
            or provider.transport is None
            or getattr(provider.transport, "offline_fixture", False) is not True
            or provider._api_key is not None):
        raise PolicyBatchError("batch_live_dispatch_disabled")
    if provider.base_url != "https://api.deepseek.com" or provider.model != "deepseek-flash":
        raise PolicyBatchError("batch_endpoint_or_model_invalid")
    root, batch = _load_batch(control_root)
    if (root / "spend").exists() or (root / "spend").is_symlink():
        raise PolicyBatchError("batch_live_policy_active")
    if (Path(dataset_root).absolute() != Path(batch["dataset_root"])
            or batch["model"]["model_id"] != provider.model):
        raise PolicyBatchError("batch_dataset_or_model_drift")
    ticket = claim_policy_cell(control_root, task_id=task_id, repeat=repeat, arm=arm,
                               out=out)
    result = run_policy_arm(ticket["manifest_dir"], dataset_root=dataset_root,
                            arm=arm, provider=provider, out=ticket["out"])
    outcome = record_policy_cell(control_root, ticket)
    return {"result": result, "outcome": outcome}
