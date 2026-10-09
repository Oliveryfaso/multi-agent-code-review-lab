import unittest
from dataclasses import asdict

try:
    from macr.investigation import records as r, validation as v
except ModuleNotFoundError:
    r = v = None


class InvestigationContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(v, "new strict investigation contract API is missing")
        self.ref = r.SnapshotRef("snapshot-1", "content-1", "manifest.json", "snapshot")
        self.state = r.InvestigationState(r.TaskSpec("run-1", "scan", snapshot_ref=self.ref))
        self.state.scope = r.ScopeManifest(eligible=[r.FileEntry("a.py", size_bytes=10, lines=2)])
        self.state.evidence["e1"] = r.EvidenceRecord("e1", "snapshot-1", observation="fact", location=r.Location("a.py", 1, 2))

    def test_task_defaults_and_strict_fields(self):
        task = v.validate_task({"run_id": "run-1", "mode": "scan"})
        self.assertFalse(task.patch_enabled)
        self.assertEqual(task.target_grade, "causal_supported")
        for patch in ({"mode": "classify"}, {"mode": "locate"}, {"shell": "x"}, {"schema_version": 99}, {"patch_enabled": "false"}):
            with self.subTest(patch=patch), self.assertRaises(v.ContractError):
                v.validate_task({"run_id": "run-1", "mode": "scan", **patch})

    def test_budget_types_ranges_and_unknown_fields(self):
        for patch in ({"tool_calls": -1}, {"model_calls": True}, {"wall_seconds": float("inf")}, {"input_tokens": "100"}, {"override": 1}):
            with self.subTest(patch=patch), self.assertRaises(v.ContractError):
                v.decode_record(r.BudgetProfile, {**asdict(r.BudgetProfile()), **patch})

    def test_action_whitelist_paths_and_ranges(self):
        raw = {"action_id": "act-1", "requester_role": "investigator", "action_type": "read_context", "typed_args": {"locations": [{"file": "a.py", "start": 1, "end": 2}]}}
        self.assertIsInstance(v.validate_action(raw, self.state).typed_args, r.ReadArgs)
        for path in ("../secret", "/tmp/a.py", "a\\b.py", ".env", "a.py/../a.py"):
            bad = {**raw, "typed_args": {"locations": [{"file": path, "start": 1, "end": 2}]}}
            with self.subTest(path=path), self.assertRaises(v.ContractError):
                v.validate_action(bad, self.state)
        for args in ({"locations": [{"file": "a.py", "start": 0, "end": 2}]}, {"locations": [{"file": "a.py", "start": 1, "end": 3}]}, {"locations": [], "env": {} }):
            with self.subTest(args=args), self.assertRaises(v.ContractError):
                v.validate_action({**raw, "typed_args": args}, self.state)
        with self.assertRaises(v.ContractError):
            v.validate_action({**raw, "action_type": "shell"}, self.state)

    def test_proposal_citations_and_snapshot_binding(self):
        proposal = {"cited_evidence_ids": ["e1"], "hypotheses": [{"hypothesis_id": "h1", "claim": "cause", "support_ids": ["e1"]}]}
        self.assertEqual(v.validate_proposal(proposal, self.state).cited_evidence_ids, ["e1"])
        with self.assertRaises(v.ContractError):
            v.validate_proposal({"cited_evidence_ids": ["missing"]}, self.state)
        self.state.evidence["e1"].snapshot_id = "other"
        with self.assertRaises(v.ContractError):
            v.validate_proposal(proposal, self.state)

    def test_state_transitions_and_location_validation(self):
        v.validate_transition("investigating", "verifying")
        v.validate_transition("verifying", "investigating")
        for before, after in (("received", "completed"), ("completed", "investigating"), ("received", "bogus")):
            with self.assertRaises(v.ContractError):
                v.validate_transition(before, after)
        with self.assertRaises(v.ContractError):
            v.decode_record(r.Location, {"file": "a.py", "start": 2, "end": 1})


if __name__ == "__main__":
    unittest.main()
