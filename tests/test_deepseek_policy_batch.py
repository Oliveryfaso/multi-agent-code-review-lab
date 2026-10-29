"""Control-plane batch freezing and durable cell ownership, entirely offline."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_runner import run_policy_arm
from macr.evals.deepseek_policy_batch import (
    CORE_IMPLEMENTATION_FILES, PolicyBatchError, prepare_policy_batch,
    claim_policy_cell, record_policy_cell,
)
from macr.providers.deepseek import DeepSeekProvider


MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}
GOLD = b'{"secret":"GOLD_NEVER_SEND"}\n'
RUBRIC = b'{"review":"RUBRIC_NEVER_SEND"}\n'
UNKNOWN = {"verdict": "unknown", "cause": None, "when": None,
           "refs": [], "unknowns": ["The deployment property is not in the source."]}


def reply(content, *, calls=None, model="deepseek-flash", fingerprint="fp-one"):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message.update(reasoning_content="Scripted static-only fixture.", tool_calls=calls)
    return {"model": model, "system_fingerprint": fingerprint,
            "choices": [{"message": message,
                         "finish_reason": "tool_calls" if calls is not None else "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8,
                      "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 12,
                      "completion_tokens_details": {"reasoning_tokens": 2}}}


class FakeTransport:
    offline_fixture = True

    def __init__(self, *responses):
        self.responses = list(responses)

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class PolicyBatchTests(unittest.TestCase):
    def test_refs_v2_cell_identities_do_not_reuse_legacy_batch_cells(self):
        batch = self.prepare()
        for cell in batch["cells"]:
            legacy = sha256(json.dumps({"task_id": cell["task_id"],
                "repeat": cell["repeat"], "arm": cell["arm"]}, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()[:32]
            self.assertNotEqual(cell["cell_id"], legacy)

    def setUp(self):
        self.root = fixture_root()
        self.dataset = self.root / "dataset"
        self.dataset.mkdir()
        (self.dataset / "gold.json").write_bytes(GOLD)
        (self.dataset / "blind-review-format.json").write_bytes(RUBRIC)
        self.project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# frozen test implementation\n")
        self.manifests = []
        for number in (1, 2):
            case_id = f"p{number:02d}"
            snapshot = self.dataset / "snapshots" / case_id
            snapshot.mkdir(parents=True)
            (snapshot / "logic.py").write_text(f"def calc():\n    return {number}\n",
                                                encoding="utf-8")
            case = {"id": case_id, "snapshot_root": f"snapshots/{case_id}",
                "question": "Is the result correct?", "scenario": "Use the supplied source.",
                "assumptions": [], "authorized_files": ["logic.py"],
                "initial_evidence_files": ["logic.py"], "stratum": "full_context",
                "evidence_parity": "equal",
                "entrypoint": {"file": "logic.py", "symbol": "calc"}}
            manifest_dir = self.root / "manifests" / case_id
            manifest_dir.parent.mkdir(exist_ok=True)
            prepare_policy_manifest(case, dataset_root=self.dataset,
                                    out=manifest_dir, model=MODEL)
            self.manifests.append(manifest_dir)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.control = self.root / "control"

    def prepare(self, *, control=None, outputs=None, seed=17):
        return prepare_policy_batch(self.manifests, dataset_root=self.dataset,
            project_root=self.project, control_root=control or self.control,
            output_root=outputs or self.outputs, seed=seed,
            implementation_version="offline-candidate-v1")

    @staticmethod
    def identity(cell):
        return (cell["task_id"], cell["repeat"], cell["arm"])

    def claim(self, cell, *, control=None, out=None):
        return claim_policy_cell(control or self.control, task_id=cell["task_id"],
            repeat=cell["repeat"], arm=cell["arm"], out=out or Path(cell["out"]))

    def run_ticket(self, ticket, *, model="deepseek-flash", fingerprint="fp-one",
                   responses=None):
        if responses is None:
            answer = reply(json.dumps(UNKNOWN), model=model, fingerprint=fingerprint)
            if ticket["arm"] in "AB":
                responses = [answer]
            elif ticket["arm"] == "C":
                verify = reply(json.dumps({"status": "accept", "coverage": "complete",
                                           "reason": "fixture"}),
                               model=model, fingerprint=fingerprint)
                responses = [answer, verify]
            else:
                terminal = {"id": "finish-1", "type": "function",
                    "function": {"name": "finish", "arguments": json.dumps({"diagnosis": UNKNOWN})}}
                responses = [reply(None, calls=[terminal], model=model,
                                   fingerprint=fingerprint)]
        provider = DeepSeekProvider(model=MODEL["model_id"],
                                    transport=FakeTransport(*responses))
        return run_policy_arm(Path(ticket["manifest_dir"]), dataset_root=self.dataset,
                              arm=ticket["arm"], provider=provider, out=Path(ticket["out"]))

    def test_freezes_control_digests_and_deterministic_interleaved_cells(self):
        first = self.prepare()
        other_outputs = self.root / "other-outputs"
        other_outputs.mkdir()
        second = self.prepare(control=self.root / "other-control",
                              outputs=other_outputs)
        self.assertEqual(first["gold_sha256"], sha256(GOLD).hexdigest())
        self.assertEqual(first["rubric_sha256"], sha256(RUBRIC).hexdigest())
        self.assertEqual(first["implementation_version"], "offline-candidate-v1")
        self.assertEqual(first["response_model_allowlist"], ["deepseek-flash"])
        self.assertEqual(first["fingerprint_policy"], "pin_first")
        self.assertEqual([self.identity(cell) for cell in first["cells"]],
                         [self.identity(cell) for cell in second["cells"]])
        self.assertEqual(len(first["cells"]), 8)
        self.assertEqual(len({self.identity(cell) for cell in first["cells"]}), 8)
        for task_id in ("p01", "p02"):
            self.assertEqual({cell["arm"] for cell in first["cells"]
                              if cell["task_id"] == task_id}, set("ABCD"))
        ticket = self.claim(first["cells"][0])
        exposed = json.dumps(ticket)
        self.assertNotIn("GOLD_NEVER_SEND", exposed)
        self.assertNotIn("RUBRIC_NEVER_SEND", exposed)
        self.assertNotIn("gold.json", exposed)
        self.assertNotIn("blind-review-format.json", exposed)
        self.assertNotIn(str(self.control), exposed)

    def test_cell_claim_is_unique_across_output_directories_and_sequential(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        ticket = self.claim(first)
        with self.assertRaises(PolicyBatchError) as moved:
            self.claim(first, out=self.root / "elsewhere" / "same-cell")
        self.assertEqual(moved.exception.code, "batch_cell_output_mismatch")
        with self.assertRaises(PolicyBatchError) as duplicate:
            self.claim(first)
        self.assertEqual(duplicate.exception.code, "batch_cell_already_claimed")
        with self.assertRaises(PolicyBatchError) as unresolved:
            self.claim(second)
        self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")
        self.assertEqual(self.run_ticket(ticket)["status"], "semantic_pending")
        outcome = record_policy_cell(self.control, ticket)
        self.assertEqual(outcome["state"], "completed")
        self.assertEqual(self.claim(second)["task_id"], second["task_id"])

    def test_unknown_or_crash_claim_stops_entire_batch_without_retry(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        ticket = self.claim(first)
        with self.assertRaises(PolicyBatchError) as unresolved:
            self.claim(second)
        self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")
        self.assertEqual(self.run_ticket(ticket, responses=[TimeoutError()])["status"],
                         "remote_outcome_unknown")
        outcome = record_policy_cell(self.control, ticket)
        self.assertEqual(outcome["state"], "batch_halted_unknown")
        with self.assertRaises(PolicyBatchError) as halted:
            self.claim(second)
        self.assertEqual(halted.exception.code, "batch_halted")
        with self.assertRaises(PolicyBatchError) as duplicate:
            record_policy_cell(self.control, ticket)
        self.assertEqual(duplicate.exception.code, "batch_outcome_already_recorded")

    def test_response_model_or_fingerprint_drift_halts_batch(self):
        for suffix, model, fingerprint in (("missing", "deepseek-flash", None),
                                           ("wrong-model", "deepseek-other", "fp-one")):
            with self.subTest(suffix=suffix):
                control = self.root / ("control-" + suffix)
                outputs = self.root / ("outputs-" + suffix)
                outputs.mkdir()
                batch = self.prepare(control=control, outputs=outputs, seed=5)
                first, second = batch["cells"][:2]
                self.assertEqual(first["arm"], "C")
                ticket = self.claim(first, control=control)
                self.run_ticket(ticket, model=model, fingerprint=fingerprint)
                outcome = record_policy_cell(control, ticket)
                self.assertEqual(outcome["state"], "batch_halted_model_drift")
                with self.assertRaises(PolicyBatchError) as halted:
                    self.claim(second, control=control)
                self.assertEqual(halted.exception.code, "batch_halted")
        control = self.root / "control-fingerprint"
        outputs = self.root / "outputs-fingerprint"
        outputs.mkdir()
        batch = self.prepare(control=control, outputs=outputs)
        first, second, third = batch["cells"][:3]
        ticket = self.claim(first, control=control)
        self.run_ticket(ticket)
        record_policy_cell(control, ticket)
        ticket = self.claim(second, control=control)
        self.run_ticket(ticket, fingerprint="fp-two")
        outcome = record_policy_cell(control, ticket)
        self.assertEqual(outcome["state"], "batch_halted_model_drift")
        with self.assertRaises(PolicyBatchError) as halted:
            self.claim(third, control=control)
        self.assertEqual(halted.exception.code, "batch_halted")

    def test_gold_source_manifest_and_implementation_drift_refuse_dispatch_claim(self):
        for suffix in ("gold", "source", "implementation", "manifest"):
            with self.subTest(suffix=suffix):
                control = self.root / ("control-drift-" + suffix)
                outputs = self.root / ("outputs-drift-" + suffix)
                outputs.mkdir()
                batch = self.prepare(control=control, outputs=outputs)
                if suffix == "gold":
                    (self.dataset / "gold.json").write_bytes(GOLD + b"changed")
                elif suffix == "source":
                    source = self.dataset / "snapshots" / "p01" / "logic.py"
                    source.write_text("def calc():\n    return 99\n", encoding="utf-8")
                elif suffix == "implementation":
                    source = self.project / "src/macr/evals/deepseek_policy_runner.py"
                    source.write_bytes(b"# changed implementation\n")
                else:
                    source = self.manifests[0] / "manifest.json"
                    original_manifest = source.read_bytes()
                    source.write_bytes(b"{}\n")
                with self.assertRaises(PolicyBatchError) as drift:
                    self.claim(batch["cells"][0], control=control)
                self.assertIn(drift.exception.code,
                    {"batch_gold_drift", "batch_snapshot_drift", "batch_code_drift",
                     "batch_manifest_drift"})
                # Restore only files owned by this isolated test fixture.
                if suffix == "gold":
                    (self.dataset / "gold.json").write_bytes(GOLD)
                elif suffix == "source":
                    source.write_text("def calc():\n    return 1\n", encoding="utf-8")
                elif suffix == "implementation":
                    source.write_bytes(b"# frozen test implementation\n")
                else:
                    source.write_bytes(original_manifest)

    def test_control_root_inside_snapshot_is_rejected_before_write(self):
        inside = self.dataset / "snapshots" / "p01" / "control"
        with self.assertRaises(PolicyBatchError) as blocked:
            self.prepare(control=inside)
        self.assertEqual(blocked.exception.code, "batch_control_in_snapshot")
        self.assertFalse(inside.exists())

    def test_generation_paths_cannot_overlap_control_or_gold_roots(self):
        overlapping_control = self.outputs / "control"
        with self.assertRaises(PolicyBatchError) as blocked:
            self.prepare(control=overlapping_control)
        self.assertEqual(blocked.exception.code, "batch_control_overlaps_output")
        self.assertFalse(overlapping_control.exists())

        manifest_inside_dataset = self.dataset / "manifest-with-gold-sibling"
        manifest_inside_dataset.mkdir()
        (manifest_inside_dataset / "manifest.json").write_bytes(
            (self.manifests[0] / "manifest.json").read_bytes())
        with self.assertRaises(PolicyBatchError) as blocked:
            prepare_policy_batch([manifest_inside_dataset], dataset_root=self.dataset,
                project_root=self.project, control_root=self.control,
                output_root=self.outputs, seed=17,
                implementation_version="offline-candidate-v1")
        self.assertEqual(blocked.exception.code, "batch_manifest_overlaps_dataset")
        self.assertFalse(self.control.exists())

        control_inside_manifest = self.manifests[0] / "control"
        with self.assertRaises(PolicyBatchError) as blocked:
            self.prepare(control=control_inside_manifest)
        self.assertEqual(blocked.exception.code, "batch_control_overlaps_manifest")
        self.assertFalse(control_inside_manifest.exists())

        manifest_inside_control = self.root / "control-parent" / "manifests"
        manifest_inside_control.mkdir(parents=True)
        (manifest_inside_control / "manifest.json").write_bytes(
            (self.manifests[0] / "manifest.json").read_bytes())
        control_parent = self.root / "control-parent"
        with self.assertRaises(PolicyBatchError) as blocked:
            prepare_policy_batch([manifest_inside_control], dataset_root=self.dataset,
                project_root=self.project, control_root=control_parent,
                output_root=self.outputs, seed=17,
                implementation_version="offline-candidate-v1")
        self.assertEqual(blocked.exception.code, "batch_control_overlaps_manifest")

    def test_scope_helper_mutation_refuses_cell_claim(self):
        helper = self.project / "src/macr/investigation/scope.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_bytes(b"# frozen scope helper\n")
        batch = self.prepare()
        helper.write_bytes(b"# changed scope helper\n")
        with self.assertRaises(PolicyBatchError) as drift:
            self.claim(batch["cells"][0])
        self.assertEqual(drift.exception.code, "batch_code_drift")

    def test_batch_entry_and_dryrun_owner_mutation_refuse_claim(self):
        for suffix, relative in (
            ("entry", "src/macr/evals/deepseek_policy_batch_entry.py"),
            ("dryrun", "scripts/run_deepseek_policy_dryrun.py"),
        ):
            with self.subTest(suffix=suffix):
                path = self.project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# frozen owner\n")
                control = self.root / ("control-" + suffix)
                outputs = self.root / ("outputs-" + suffix)
                outputs.mkdir()
                batch = self.prepare(control=control, outputs=outputs)
                path.write_bytes(b"# changed owner\n")
                with self.assertRaises(PolicyBatchError) as drift:
                    self.claim(batch["cells"][0], control=control)
                self.assertEqual(drift.exception.code, "batch_code_drift")
                path.write_bytes(b"# frozen owner\n")

    def test_missing_result_and_forged_success_cannot_unlock_next_cell(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        ticket = self.claim(first)
        with self.assertRaises(PolicyBatchError) as missing:
            record_policy_cell(self.control, ticket)
        self.assertEqual(missing.exception.code, "batch_result_unresolved")
        with self.assertRaises(PolicyBatchError) as unresolved:
            self.claim(second)
        self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")

        self.run_ticket(ticket, responses=[TimeoutError()])
        path = Path(ticket["out"]) / "result.json"
        forged = json.loads(path.read_text(encoding="utf-8"))
        forged["status"] = "semantic_pending"
        forged["review_status"] = "pending"
        path.write_text(json.dumps(forged), encoding="utf-8")
        with self.assertRaises(PolicyBatchError) as rejected:
            record_policy_cell(self.control, ticket)
        self.assertEqual(rejected.exception.code, "batch_result_unresolved")
        with self.assertRaises(PolicyBatchError) as unresolved:
            self.claim(second)
        self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")

    def test_missing_response_receipt_and_within_cell_drift_never_unlock(self):
        for suffix in ("missing-receipt", "fingerprint-drift"):
            with self.subTest(suffix=suffix):
                control = self.root / ("control-" + suffix)
                outputs = self.root / ("outputs-" + suffix)
                outputs.mkdir()
                batch = self.prepare(control=control, outputs=outputs, seed=5)
                first, second = batch["cells"][:2]
                self.assertEqual(first["arm"], "C")
                ticket = self.claim(first, control=control)
                self.run_ticket(ticket)
                if suffix == "missing-receipt":
                    (Path(ticket["out"]) / "decision-01.response.json").unlink()
                    with self.assertRaises(PolicyBatchError) as rejected:
                        record_policy_cell(control, ticket)
                    self.assertEqual(rejected.exception.code, "batch_result_unresolved")
                    with self.assertRaises(PolicyBatchError) as unresolved:
                        self.claim(second, control=control)
                    self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")
                else:
                    # Change every redundant copy consistently; the two received
                    # decisions still disagree and cannot be scored as one model.
                    result_path = Path(ticket["out"]) / "result.json"
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    self.assertEqual(len(result["requests"]), 2)
                    result["requests"][1]["response"]["system_fingerprint"] = "fp-two"
                    result_path.write_text(json.dumps(result), encoding="utf-8")
                    receipt_path = Path(ticket["out"]) / "decision-02.response.json"
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                    receipt["system_fingerprint"] = "fp-two"
                    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                    outcome_path = Path(ticket["out"]) / "decision-02.outcome.json"
                    receipt_outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
                    receipt_outcome["system_fingerprint"] = "fp-two"
                    receipt_outcome["response_sha256"] = sha256(json.dumps(
                        receipt, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode()).hexdigest()
                    outcome_path.write_text(json.dumps(receipt_outcome), encoding="utf-8")
                    self.assertEqual(record_policy_cell(control, ticket)["state"],
                                     "batch_halted_model_drift")

    def test_registry_rejects_overfull_and_forged_completion_records(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        registry = self.control / "registry"
        for index in range(290):
            (registry / f"unexpected-{index}").write_text("x", encoding="utf-8")
        with self.assertRaises(PolicyBatchError) as overfull:
            self.claim(first)
        self.assertEqual(overfull.exception.code, "batch_registry_invalid")

        control = self.root / "control-forged"
        outputs = self.root / "outputs-forged"
        outputs.mkdir()
        batch = self.prepare(control=control, outputs=outputs)
        first, second = batch["cells"][:2]
        ticket = self.claim(first, control=control)
        self.run_ticket(ticket)
        record_policy_cell(control, ticket)
        outcome_path = control / "registry" / f'{ticket["cell_id"]}.outcome.json'
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        outcome["result_sha256"] = "0" * 64
        outcome_path.write_text(json.dumps(outcome), encoding="utf-8")
        with self.assertRaises(PolicyBatchError) as forged:
            self.claim(second, control=control)
        self.assertEqual(forged.exception.code, "batch_registry_invalid")

    def test_falsified_previous_unknown_outcome_cannot_unlock_batch(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        ticket = self.claim(first)
        self.run_ticket(ticket, responses=[TimeoutError()])
        self.assertEqual(record_policy_cell(self.control, ticket)["state"],
                         "batch_halted_unknown")
        outcome_path = self.control / "registry" / f'{ticket["cell_id"]}.outcome.json'
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        outcome["state"] = "completed"
        outcome_path.write_text(json.dumps(outcome), encoding="utf-8")
        with self.assertRaises(PolicyBatchError) as forged:
            self.claim(second)
        self.assertEqual(forged.exception.code, "batch_registry_invalid")

    def test_forged_persisted_request_cannot_close_cell(self):
        batch = self.prepare()
        first, second = batch["cells"][:2]
        ticket = self.claim(first)
        self.run_ticket(ticket)
        path = Path(ticket["out"]) / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["requests"][0]["request"]["messages"][1]["content"] = (
            '{"source":"FORGED_UNAUTHORIZED_SOURCE"}')
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(PolicyBatchError) as forged:
            record_policy_cell(self.control, ticket)
        self.assertEqual(forged.exception.code, "batch_result_unresolved")
        with self.assertRaises(PolicyBatchError) as unresolved:
            self.claim(second)
        self.assertEqual(unresolved.exception.code, "batch_unresolved_claim")


class RetainedRecoveryBatchTests(unittest.TestCase):
    def setUp(self):
        from test_deepseek_policy_recovery import make_recovery_source
        from macr.evals.deepseek_policy_recovery import build_recovery_plan
        source = make_recovery_source(fixture_root())
        self.__dict__.update(source.__dict__)
        self.target = self.root / "recovered"
        self.plan = build_recovery_plan(self.current,
            predecessor_batch_sha256=self.batch["batch_sha256"],
            target_control_root=self.target / "control",
            target_output_root=self.target / "outputs", recovery_version="recovery-test-v1")
        self.target.mkdir(mode=0o700)
        self.outputs = self.target / "outputs"
        self.outputs.mkdir(mode=0o700)

    def prepare(self, **changes):
        arguments = dict(dataset_root=self.dataset, project_root=self.project,
            control_root=self.target / "control", output_root=self.outputs, seed=0,
            implementation_version="recovery-test-v1", recovery_plan=self.plan)
        arguments.update(changes)
        directories = arguments.pop("manifest_dirs", [row["manifest_dir"]
                                                       for row in self.batch["manifests"]])
        return prepare_policy_batch(directories, **arguments)

    def test_only_unattempted_logical_cells_get_new_execution_identities(self):
        recovered = self.prepare()
        self.assertEqual(len(recovered["cells"]), 45)
        identities = {(row["task_id"], row["repeat"], row["arm"])
                      for row in recovered["cells"]}
        attempted = {(row["task_id"], row["repeat"], row["arm"])
                     for row in self.batch["cells"][:3]}
        self.assertEqual(len(identities), 45)
        self.assertFalse(identities.intersection(attempted))
        self.assertFalse({row["cell_id"] for row in recovered["cells"]}.intersection(
            row["cell_id"] for row in self.batch["cells"]))
        self.assertEqual(recovered["recovery_plan"], self.plan)
        blocked = self.batch["cells"][2]
        with self.assertRaises(PolicyBatchError) as replay:
            claim_policy_cell(self.target / "control", task_id=blocked["task_id"],
                repeat=1, arm=blocked["arm"], out=self.outputs / blocked["cell_id"])
        self.assertEqual(replay.exception.code, "batch_cell_unknown")
        first = recovered["cells"][0]
        ticket = claim_policy_cell(self.target / "control", task_id=first["task_id"],
            repeat=1, arm=first["arm"], out=first["out"])
        self.assertNotIn("recovery_plan", ticket)
        self.assertNotIn("opening", ticket)

    def test_plan_cannot_be_used_in_another_output_or_implementation_version(self):
        for changes in ({"implementation_version": "another-version"},
                        {"output_root": self.root / "other-outputs"}):
            with self.subTest(changes=changes):
                if "output_root" in changes:
                    changes["output_root"].mkdir(mode=0o700)
                with self.assertRaises(PolicyBatchError):
                    self.prepare(**changes)
                self.assertFalse((self.target / "control").exists())

    def test_recovery_does_not_change_reference_answers_or_authorized_snapshots(self):
        other = self.root / "other-dataset"
        shutil.copytree(self.dataset, other)
        (other / "gold.json").write_text('{"changed":"reference"}', encoding="utf-8")
        with self.assertRaises(PolicyBatchError):
            self.prepare(dataset_root=other)
        self.assertFalse((self.target / "control").exists())

    def test_same_source_bytes_do_not_allow_changed_question_or_assumptions(self):
        from macr.evals.deepseek_policy_manifest import load_policy_manifest
        directories = []
        for row in self.batch["manifests"]:
            manifest = load_policy_manifest(row["manifest_dir"], dataset_root=self.dataset)
            task = dict(manifest["task"])
            if row["task_id"] == "p01":
                task["question"] = "Changed question that was never approved."
                task["assumptions"] = ["Added assumption."]
            directory = self.root / "changed-manifests" / row["task_id"]
            directory.parent.mkdir(exist_ok=True)
            prepare_policy_manifest(task, dataset_root=self.dataset, out=directory, model=MODEL)
            directories.append(directory)
        with self.assertRaises(PolicyBatchError):
            self.prepare(manifest_dirs=directories)
        self.assertFalse((self.target / "control").exists())

    def test_rehashed_source_manifest_cannot_redefine_the_original_task(self):
        from macr.evals.deepseek_policy_batch import _digest
        from macr.evals.deepseek_policy_recovery import RecoveryError, build_recovery_plan
        path = Path(self.batch["manifests"][0]["manifest_dir"]) / "manifest.json"
        changed = json.loads(path.read_text())
        changed["task"]["question"] = "Changed original question."
        changed["manifest_sha256"] = _digest({key: value for key, value in changed.items()
                                             if key != "manifest_sha256"})
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises((RecoveryError, PolicyBatchError)):
            self.plan = build_recovery_plan(self.current,
                predecessor_batch_sha256=self.batch["batch_sha256"],
                target_control_root=self.target / "control",
                target_output_root=self.outputs, recovery_version="recovery-test-v1")
            self.prepare()


if __name__ == "__main__":
    unittest.main()
