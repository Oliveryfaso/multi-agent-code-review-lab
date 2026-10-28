"""Offline contracts for explicit approval, ephemeral Key ingress and log retention."""
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import stat
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import CORE_IMPLEMENTATION_FILES, prepare_policy_batch
from macr.evals.deepseek_policy_live_scope import (LiveApproval, LiveScopeError,
    acquire_runtime_inputs, validate_live_scope, write_log_policy)
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest


MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}


class LiveScopeTests(unittest.TestCase):
    def test_predecessor_is_explicit_approved_scope_and_must_be_a_digest(self):
        approval = replace(self.approval, predecessor_batch_sha256="a" * 64)
        scope = validate_live_scope(approval, self.batch, dataset_root=self.dataset)
        self.assertEqual(scope.predecessor_batch_sha256, "a" * 64)
        for value in ("not-a-digest", self.batch["batch_sha256"], True):
            with self.subTest(value=value):
                with self.assertRaises(LiveScopeError):
                    validate_live_scope(replace(approval, predecessor_batch_sha256=value),
                                        self.batch, dataset_root=self.dataset)

    def setUp(self):
        self.root = fixture_root()
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        (self.dataset / "gold.json").write_text('{"secret":"GOLD_NEVER_SEND"}', encoding="utf-8")
        (self.dataset / "blind-review-format.json").write_text(
            '{"rubric":"RUBRIC_NEVER_SEND"}', encoding="utf-8")
        self.project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# frozen test implementation\n", encoding="utf-8")
        manifest_dirs = []
        sources = []
        for number in range(1, 13):
            case_id = f"p{number:02d}"
            snapshot = self.dataset / "snapshots" / case_id
            snapshot.mkdir(parents=True)
            if number <= 6:
                files = ["entry.py", "helper.py"]
                initial = ["entry.py"]
                stratum, parity = "retrieval_required", "unequal_by_design"
            else:
                files = initial = ["logic.py"]
                stratum, parity = "full_context", "equal"
            for name in files:
                (snapshot / name).write_text(f"def value():\n    return {number}\n", encoding="utf-8")
                sources.append(f"snapshots/{case_id}/{name}")
            case = {"id": case_id, "snapshot_root": f"snapshots/{case_id}",
                    "question": "Is the result correct?", "scenario": "Use the source.",
                    "assumptions": [], "authorized_files": files,
                    "initial_evidence_files": initial, "stratum": stratum,
                    "evidence_parity": parity,
                    "entrypoint": {"file": initial[0], "symbol": "value"}}
            manifest_dir = self.root / "manifests" / case_id
            manifest_dir.parent.mkdir(exist_ok=True)
            prepare_policy_manifest(case, dataset_root=self.dataset,
                                    out=manifest_dir, model=MODEL)
            manifest_dirs.append(manifest_dir)
        self.sources = tuple(sorted(sources))
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(mode=0o700)
        self.control = self.root / "control"
        self.batch = prepare_policy_batch(manifest_dirs, dataset_root=self.dataset,
            project_root=self.project, control_root=self.control, output_root=self.outputs,
            seed=41, implementation_version="scope-fixture-v1")
        self.approval = LiveApproval(granted=True, approval_ref="DUMMY_APPROVAL_TEST_ONLY",
            batch_sha256=self.batch["batch_sha256"], model_id="deepseek-flash",
            endpoint="https://api.deepseek.com/chat/completions", max_requests=84,
            max_usd="3.00", price_snapshot_id="flash-peak-20261028",
            price_checked_on="2026-10-28", approved_sources=self.sources,
            log_root=self.outputs, retention_days=14)

    def test_exact_twelve_case_scope_is_immutable_and_reads_no_gold(self):
        # The batch control has already frozen gold. This generation-facing
        # validation only reads neutral manifests and their source snapshots.
        (self.dataset / "gold.json").unlink()
        scope = validate_live_scope(self.approval, self.batch,
                                    dataset_root=self.dataset)
        self.assertEqual(scope.approved_sources, self.sources)
        self.assertEqual(scope.max_requests, 84)
        self.assertEqual(scope.price_snapshot_id, "flash-peak-20261028")
        self.assertFalse(hasattr(scope, "api_key"))
        with self.assertRaises(FrozenInstanceError):
            scope.max_usd = "999.00"

    def test_01_missing_or_drifted_approval_fails_before_any_key_input(self):
        attempts = []

        def dummy_reader():
            attempts.append("read")
            return "KEY_SENTINEL_NEVER_LOG"

        wrong = [
            {"granted": False}, {"approval_ref": ""},
            {"batch_sha256": "0" * 64}, {"model_id": "deepseek-chat"},
            {"endpoint": "https://elsewhere.example/chat/completions"},
            {"max_requests": 85}, {"max_usd": "0.00"},
            {"price_snapshot_id": ""}, {"price_checked_on": "not-a-date"},
            {"approved_sources": self.sources[:-1]},
            {"approved_sources": self.sources + ("gold.json",)},
            {"approved_sources": self.sources + (self.sources[0],)},
            {"retention_days": 0}, {"retention_days": 15},
        ]
        for changes in wrong:
            with self.subTest(changes=changes):
                with self.assertRaises(LiveScopeError):
                    acquire_runtime_inputs(replace(self.approval, **changes), self.batch,
                        dataset_root=self.dataset, key_reader=dummy_reader)
        self.assertEqual(attempts, [])

    def test_log_root_must_be_exact_private_frozen_output_directory(self):
        other = self.root / "other"
        other.mkdir(mode=0o700)
        with self.assertRaises(LiveScopeError):
            validate_live_scope(replace(self.approval, log_root=other), self.batch,
                                dataset_root=self.dataset)
        os.chmod(self.outputs, 0o755)
        try:
            with self.assertRaises(LiveScopeError):
                validate_live_scope(self.approval, self.batch, dataset_root=self.dataset)
        finally:
            os.chmod(self.outputs, 0o700)

    def test_key_is_ephemeral_and_policy_file_records_retention_without_deletion(self):
        secret = "KEY_SENTINEL_NEVER_LOG"
        calls = []

        def dummy_reader():
            self.assertTrue((self.outputs / "retention.json").is_file())
            calls.append(1)
            return secret

        scope, key = acquire_runtime_inputs(self.approval, self.batch,
            dataset_root=self.dataset, key_reader=dummy_reader)
        self.assertEqual((key, len(calls)), (secret, 1))
        self.assertNotIn(secret, repr(scope))
        policy_path = write_log_policy(scope)
        self.assertEqual(policy_path, self.outputs / "retention.json")
        self.assertEqual(stat.S_IMODE(policy_path.stat().st_mode), 0o600)
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["retention_days"], 14)
        self.assertEqual(payload["contents"], ["source", "model_output",
                                                   "reasoning_content", "usage", "receipts"])
        self.assertFalse(payload["auto_delete"])
        self.assertFalse(payload["upload"])
        self.assertTrue(payload["purge_requires_user_approval"])
        self.assertNotIn(secret, policy_path.read_text(encoding="utf-8"))
        self.assertEqual(write_log_policy(scope), policy_path)

    def test_tampered_or_exposed_policy_blocks_key_reader(self):
        scope = validate_live_scope(self.approval, self.batch, dataset_root=self.dataset)
        policy_path = write_log_policy(scope)
        attempts = []

        def dummy_reader():
            attempts.append(1)
            return "KEY_SENTINEL_NEVER_LOG"

        policy_path.write_text('{"retention_days":999}', encoding="utf-8")
        with self.assertRaises(LiveScopeError):
            acquire_runtime_inputs(self.approval, self.batch,
                dataset_root=self.dataset, key_reader=dummy_reader)
        self.assertEqual(attempts, [])
        policy_path.unlink()
        write_log_policy(scope)
        os.chmod(policy_path, 0o644)
        try:
            with self.assertRaises(LiveScopeError):
                acquire_runtime_inputs(self.approval, self.batch,
                    dataset_root=self.dataset, key_reader=dummy_reader)
            self.assertEqual(attempts, [])
        finally:
            os.chmod(policy_path, 0o600)

    def test_invalid_key_reader_or_value_is_rejected_after_policy(self):
        for reader in (None, lambda: "", lambda: "line\nbreak"):
            with self.subTest(reader=reader):
                with self.assertRaises(LiveScopeError):
                    acquire_runtime_inputs(self.approval, self.batch,
                        dataset_root=self.dataset, key_reader=reader)
        self.assertTrue((self.outputs / "retention.json").is_file())


if __name__ == "__main__":
    unittest.main()
