"""Offline evidence and timing contracts for the policy runner."""
import json
from hashlib import sha256
from time import sleep
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals import deepseek_policy_runner as runner
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_tools import StaticEvidenceTools
from macr.providers.base import ModelResponse, ProviderError
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply


class SlowTransport(FakeTransport):
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        sleep(0.02)
        return super().post(endpoint, body, timeout_seconds, max_response_bytes)


class PolicyObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        source = self.root / "snapshots" / "o01"
        source.mkdir(parents=True)
        (source / "entry.py").write_text("def gate(value):\n    return value > 0\n", encoding="utf-8")
        case = {"id": "o01", "snapshot_root": "snapshots/o01",
                "question": "Does gate satisfy this case?", "scenario": "Call gate(0).",
                "assumptions": [], "authorized_files": ["entry.py"],
                "initial_evidence_files": ["entry.py"], "stratum": "full_context",
                "evidence_parity": "equal", "entrypoint": {"file": "entry.py", "symbol": "gate"}}
        self.manifest_dir = self.root / "manifest"
        prepare_policy_manifest(case, dataset_root=self.root, out=self.manifest_dir, model=MODEL)

    def run_arm(self, arm, transport, name):
        return runner.run_policy_arm(self.manifest_dir, dataset_root=self.root, arm=arm,
            provider=DeepSeekProvider(model=MODEL["model_id"], transport=transport),
            out=self.root / name)

    def test_incomplete_response_keeps_bounded_full_evidence_in_failure_trace(self):
        partial = reply("partial", finish="length")
        partial["usage"]["prompt_cache_hit_tokens"] = 3
        partial["usage"]["prompt_cache_miss_tokens"] = 9
        transport = FakeTransport(partial)
        row = self.run_arm("A", transport, "incomplete")
        self.assertEqual((row["status"], row["error_code"]),
                         ("failed", "model_output_incomplete"))
        evidence = row["requests"][0]["response"]
        self.assertEqual(evidence["assistant_message"]["content"], "partial")
        self.assertEqual(evidence["raw_usage"]["prompt_cache_hit_tokens"], 3)
        self.assertEqual(evidence["raw_usage"]["completion_tokens_details"]["reasoning_tokens"], 2)
        self.assertEqual(evidence["model"], MODEL["model_id"])
        self.assertEqual(evidence["system_fingerprint"], "fixture-fingerprint")
        self.assertEqual(evidence["finish_reason"], "length")
        self.assertIsInstance(evidence["latency_ms"], int)
        durable = json.loads((self.root / "incomplete" / "decision-01.response.json").read_text())
        self.assertEqual(durable, evidence)
        outcome = json.loads((self.root / "incomplete" / "decision-01.outcome.json").read_text())
        self.assertTrue(outcome["response_evidence_complete"])

    def test_transport_without_response_records_unknown_evidence_and_stops(self):
        row = self.run_arm("A", FakeTransport(TimeoutError()), "unknown")
        self.assertEqual(row["status"], "remote_outcome_unknown")
        evidence = row["requests"][0]["response"]
        self.assertEqual(evidence["state"], "unknown")
        self.assertIsNone(evidence["model"])
        self.assertIsNone(evidence["raw_usage"])
        durable = json.loads((self.root / "unknown" / "decision-01.response.json").read_text())
        self.assertEqual(durable, evidence)

    def test_partial_failure_envelope_marks_absent_fields_unknown(self):
        partial = ModelResponse(content="partial", usage={"input_tokens": 7},
            latency_ms=9, model=MODEL["model_id"], provider="deepseek",
            finish_reason="length", usage_kind="unknown")
        row = self.run_arm("A", FakeTransport(ProviderError(
            "model_output_incomplete", response=partial)), "base-envelope")
        self.assertEqual(row["error_code"], "model_output_incomplete")
        evidence = row["requests"][0]["response"]
        self.assertEqual(evidence["state"], "received")
        self.assertIsNone(evidence["assistant_message"])
        self.assertIsNone(evidence["raw_usage"])
        self.assertIsNone(evidence["system_fingerprint"])
        self.assertEqual(evidence["model"], MODEL["model_id"])

    def test_total_timing_includes_preparation_and_provider(self):
        original = runner.load_policy_manifest

        def slow_load(*args, **kwargs):
            sleep(0.02)
            return original(*args, **kwargs)

        with patch.object(runner, "load_policy_manifest", side_effect=slow_load):
            row = self.run_arm("A", SlowTransport(reply(json.dumps(UNKNOWN))), "timing-a")
        timing = row["timing_seconds"]
        self.assertGreaterEqual(timing["preparation"], 0.015)
        self.assertGreaterEqual(timing["provider"], 0.015)
        self.assertEqual(timing["tool"], 0)
        self.assertGreaterEqual(row["elapsed_seconds"], 0.035)

    def test_tool_timing_is_distinct_from_provider_and_preparation(self):
        original = StaticEvidenceTools.execute

        def slow_tool(instance, name, args):
            sleep(0.02)
            return original(instance, name, args)

        answers = [reply(None, calls=[call("search-1", "search", {"query": "gate"})]),
                   reply(None, calls=[call("finish-2", "finish", {"diagnosis": UNKNOWN})])]
        with patch.object(StaticEvidenceTools, "execute", slow_tool):
            row = self.run_arm("D", SlowTransport(*answers), "timing-d")
        self.assertEqual(row["status"], "semantic_pending")
        timing = row["timing_seconds"]
        self.assertGreaterEqual(timing["tool"], 0.015)
        self.assertGreaterEqual(timing["provider"], 0.035)
        self.assertGreaterEqual(row["elapsed_seconds"], 0.055)

    def test_missing_fingerprint_stops_before_second_decision(self):
        first = reply(json.dumps(UNKNOWN))
        first["system_fingerprint"] = None
        transport = FakeTransport(first, reply(json.dumps({"status": "accept",
            "coverage": "complete", "reason": "fixture"})))
        row = self.run_arm("C", transport, "missing-fingerprint")
        self.assertEqual(row["error_code"], "system_fingerprint_unknown")
        self.assertEqual(row["requests_attempted"], 1)
        self.assertEqual(len(transport.posts), 1)

    def test_fingerprint_change_stops_d_before_third_decision(self):
        first = reply(None, calls=[call("read-1", "search", {"query": "gate"})])
        second = reply(None, calls=[call("read-2", "search", {"query": "return"})])
        second["system_fingerprint"] = "changed-backend"
        final = reply(None, calls=[call("finish-3", "finish", {"diagnosis": UNKNOWN})])
        transport = FakeTransport(first, second, final)
        row = self.run_arm("D", transport, "changed-fingerprint")
        self.assertEqual(row["error_code"], "system_fingerprint_drift")
        self.assertEqual(row["requests_attempted"], 2)
        self.assertEqual(len(transport.posts), 2)
        self.assertEqual(row["requests"][1]["response"]["system_fingerprint"],
                         "changed-backend")

    def test_request_receipt_binds_exact_wire_and_persisted_request_object(self):
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        row = self.run_arm("A", transport, "request-binding")
        body = transport.raw_bodies[0]
        canonical = json.dumps(json.loads(body), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        entry = row["requests"][0]
        receipt = json.loads((self.root / "request-binding" /
                              "decision-01.reservation.json").read_text())
        self.assertEqual(entry.get("request_sha256"), sha256(body).hexdigest())
        self.assertEqual(receipt["request_sha256"], sha256(body).hexdigest())
        self.assertEqual(entry.get("request_canonical_sha256"), sha256(canonical).hexdigest())
        self.assertEqual(receipt.get("request_canonical_sha256"), sha256(canonical).hexdigest())


if __name__ == "__main__":
    unittest.main()
