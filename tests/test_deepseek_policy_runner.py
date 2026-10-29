"""Offline policy runner integration using only scripted wire responses."""
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from macr.evals.diagnostic_compare import OUTPUT_SCHEMA
from macr.evals.deepseek_policy_manifest import DEFAULT_BUDGET, prepare_policy_manifest
from macr.evals.deepseek_policy_runner import PolicyRunError, run_policy_arm
from macr.providers.deepseek import DeepSeekProvider


MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}
UNKNOWN = {"verdict": "unknown", "cause": None, "when": None, "refs": [],
           "unknowns": ["The deployed flag value is outside the authorized source snapshot."]}


def reply(content, *, calls=None, finish=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message.update(reasoning_content="I need static evidence.", tool_calls=calls)
    return {"model": MODEL["model_id"], "system_fingerprint": "fixture-fingerprint",
        "choices": [{"message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8,
                  "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 12,
                  "completion_tokens_details": {"reasoning_tokens": 2}}}


def call(identifier, name, arguments):
    return {"id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


class FakeTransport:
    offline_fixture = True

    def __init__(self, *answers):
        self.answers = list(answers)
        self.posts = []
        self.raw_bodies = []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.raw_bodies.append(body)
        self.posts.append(json.loads(body))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class PolicyRunnerTests(unittest.TestCase):
    def test_verifier_serialized_request_declares_and_preserves_reason_boundaries(self):
        cases = [('r' * 256, 'semantic_pending'), ('r' * 257, 'failed'),
                 ('界' * 256, 'semantic_pending'), ('界' * 257, 'failed'),
                 (' ' + 'r' * 256 + ' ', 'semantic_pending')]
        for number, (reason, expected) in enumerate(cases):
            with self.subTest(number=number):
                transport = FakeTransport(reply(json.dumps(UNKNOWN)), reply(json.dumps({
                    'status': 'accept', 'coverage': 'complete', 'reason': reason})))
                result = self.run_arm('C', transport, name='verifier-boundary-' + str(number))
                wire = json.loads(transport.raw_bodies[1])
                prompt = wire['messages'][0]['content']
                marker = 'Verification JSON Schema:\n'
                self.assertIn(marker, prompt)
                schema, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
                self.assertEqual(schema['required'], ['status', 'coverage', 'reason'])
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(schema['properties']['reason'],
                    {'type': 'string', 'minLength': 1, 'maxLength': 256})
                self.assertEqual(schema['properties']['status']['enum'], ['accept', 'reject'])
                self.assertEqual(schema['properties']['coverage']['enum'], ['complete', 'incomplete'])
                self.assertEqual(wire['response_format'], {'type': 'json_object'})
                self.assertEqual(result['status'], expected)
                if expected == 'failed':
                    self.assertEqual(result['error_code'], 'verification_contract_invalid')
                    self.assertIsNone(result['answer'])
                else:
                    self.assertEqual(result['verification']['reason'], reason)

    def test_ask_user_wire_declares_character_limit_without_changing_strip_semantics(self):
        for number, (gap, expected) in enumerate((('界' * 200, 'ask_user_terminated'),
                ('界' * 201, 'failed'), (' ' + 'g' * 200 + ' ', 'ask_user_terminated'))):
            transport = FakeTransport(reply(None, calls=[call('ask-1', 'ask_user', {'gap': gap})]))
            result = self.run_arm('D', transport, name='ask-boundary-' + str(number))
            wire = json.loads(transport.raw_bodies[0])
            ask = next(tool['function'] for tool in wire['tools']
                       if tool['function']['name'] == 'ask_user')
            self.assertEqual(ask['parameters']['properties']['gap'],
                {'type': 'string', 'minLength': 1, 'maxLength': 200})
            self.assertEqual(result['status'], expected)
            if expected == 'failed':
                self.assertEqual(result['error_code'], 'ask_user_contract_invalid')
            else:
                self.assertEqual(result['gap'], gap)

    def test_d_read_parameter_wire_communicates_real_byte_and_scope_limits(self):
        from macr.evals.deepseek_policy_manifest import load_policy_manifest
        from macr.evals.deepseek_policy_tools import StaticEvidenceTools
        transport = FakeTransport(reply(None, calls=[call('finish-1', 'finish', {'diagnosis': UNKNOWN})]))
        self.run_arm('D', transport)
        wire = json.loads(transport.raw_bodies[0])
        tools = {tool['function']['name']: tool['function'] for tool in wire['tools']}
        for name, field in (('search', 'query'), ('read_symbol', 'symbol'), ('read_caller', 'symbol')):
            parameter = tools[name]['parameters']['properties'][field]
            self.assertEqual((parameter['minLength'], parameter['maxLength']), (1, 128))
            self.assertIn('128 UTF-8 bytes', parameter['description'])
            self.assertIn('nonblank', parameter['description'])
            if field == 'symbol':
                self.assertIn('Python identifiers', parameter['description'])
        self.assertIn('512 UTF-8 bytes', tools['read_symbol']['parameters']['properties']['file']['description'])
        self.assertIn('authorized', tools['read_symbol']['parameters']['properties']['file']['description'])
        self.assertIn('8192 UTF-8 bytes', tools['search']['description'])
        self.assertIn('Never repeat', wire['messages'][0]['content'])
        self.assertIn('within or across decisions', wire['messages'][0]['content'])
        self.assertIn('4 read tools in total', wire['messages'][0]['content'])
        manifest = load_policy_manifest(self.manifest_dir, dataset_root=self.root)
        reader = StaticEvidenceTools(manifest, self.root)
        for term in ('x' * 128, '界' * 42 + 'xx'):
            self.assertEqual(reader.execute('search', {'query': term})['status'], 'ok')
        for term in ('x' * 129, '界' * 43):
            self.assertEqual(reader.execute('search', {'query': term})['status'], 'error')
        limited = self.root / 'limited-manifest'
        prepare_policy_manifest(self.case, dataset_root=self.root, out=limited,
            model=MODEL, budget=DEFAULT_BUDGET | {'max_tool_reads': 1})
        self.manifest_dir = limited
        limited_wire = FakeTransport(reply(None, calls=[call('finish-2', 'finish', {'diagnosis': UNKNOWN})]))
        self.run_arm('D', limited_wire, name='limited-d')
        self.assertIn('1 read tools in total', json.loads(limited_wire.raw_bodies[0])['messages'][0]['content'])

    def test_object_byte_boundary_is_preserved_without_truncation(self):
        from macr.evals.deepseek_policy_runner import _object
        prefix, suffix = '{"value":"', '"}'
        body = prefix + 'x' * (8192 - len(prefix + suffix)) + suffix
        self.assertEqual(len(body.encode('utf-8')), 8192)
        self.assertEqual(len(_object(body)['value']), 8192 - len(prefix + suffix))
        with self.assertRaisesRegex(PolicyRunError, '^diagnosis_json_invalid$'):
            _object(body + ' ')

    def test_legacy_prompt_is_readable_but_cannot_dispatch_new_instructions(self):
        from macr.evals.deepseek_policy_manifest import PolicyManifestError, _digest, load_policy_manifest
        path = self.manifest_dir / 'manifest.json'
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest['prompt_version'], 'deepseek-policy-contracts-v3')
        manifest['prompt_version'] = 'deepseek-policy-refs-v2'
        manifest['manifest_sha256'] = _digest({k:v for k,v in manifest.items() if k != 'manifest_sha256'})
        path.write_text(json.dumps(manifest))
        self.assertEqual(load_policy_manifest(self.manifest_dir, dataset_root=self.root), manifest)
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        with self.assertRaisesRegex(PolicyRunError, '^policy_prompt_version_invalid$'):
            self.run_arm('A', transport)
        self.assertEqual(transport.posts, [])
        self.assertFalse((self.root / 'run-A').exists())
        with self.assertRaises(PolicyManifestError):
            prepare_policy_manifest(self.case, dataset_root=self.root,
                out=self.root / 'legacy-preparation', model=MODEL,
                prompt_version='deepseek-policy-refs-v2')
        self.assertFalse((self.root / 'legacy-preparation').exists())
        manifest['prompt_version'] = 'unknown-contracts-v4'
        manifest['manifest_sha256'] = _digest({k:v for k,v in manifest.items() if k != 'manifest_sha256'})
        path.write_text(json.dumps(manifest))
        with self.assertRaises(PolicyManifestError):
            load_policy_manifest(self.manifest_dir, dataset_root=self.root)

    def test_diagnosis_character_boundaries_remain_strict_in_every_arm(self):
        definite = {'verdict': 'supported_defect', 'cause': 'c' * 600, 'when': '界' * 300,
            'refs': [{'file': 'entry.py', 'start': 1, 'end': 4}], 'unknowns': []}
        cases = [(definite, 'semantic_pending'), (definite | {'when': '界' * 301}, 'failed'),
                 (definite | {'cause': 'c' * 601}, 'failed'),
                 (UNKNOWN | {'unknowns': ['界' * 200]}, 'semantic_pending'),
                 (UNKNOWN | {'unknowns': ['界' * 201]}, 'failed')]
        for arm in 'ABCD':
            for number, (answer, expected) in enumerate(cases):
                with self.subTest(arm=arm, boundary=number):
                    answers = [reply(json.dumps(answer))]
                    if arm == 'C':
                        answers.append(reply('{"status":"accept","coverage":"complete","reason":"Fixture"}'))
                    elif arm == 'D':
                        answers = [reply(None, calls=[call('finish-1', 'finish', {'diagnosis': answer})])]
                    result = self.run_arm(arm, FakeTransport(*answers),
                        name=f'diagnosis-boundary-{arm}-{number}')
                    self.assertEqual(result['status'], expected)
                    if expected == 'failed':
                        self.assertEqual(result['error_code'], 'eval_contract_invalid')
                        self.assertIsNone(result['answer'])
                    else:
                        self.assertEqual(result['answer'], answer)

    def test_tool_argument_json_and_policy_schema_fail_at_their_own_boundaries(self):
        cases = [("{unfinished", "model_response_invalid", "unknown"),
                 (json.dumps({"diagnosis": {"verdict": "bogus"}}),
                  "eval_contract_invalid", "received")]
        for number, (arguments, expected, state) in enumerate(cases):
            invocation = call("finish-1", "finish", {})
            invocation["function"]["arguments"] = arguments
            result = self.run_arm("D", FakeTransport(reply(None, calls=[invocation])),
                                  name="caller-owned-" + str(number))
            self.assertEqual(result["error_code"], expected)
            self.assertEqual(result["tool_reads"], 0)
            response = result["requests"][0]["response"]
            self.assertEqual(response["state"], state)
            if state == "unknown":
                self.assertEqual(response["failure_diagnostic"]["validation_error"], "tool_arguments_json")
            else:
                self.assertNotIn("failure_diagnostic", response)

    def test_invalid_tool_envelope_records_safe_failure_stage_without_body(self):
        bad = reply(None, calls=[call("bad-1", "finish", {"diagnosis": UNKNOWN})])
        del bad["choices"][0]["message"]["content"]
        bad["private-sentinel-key"] = "SECRET_SENTINEL_NEVER_RECORD"
        result = self.run_arm("D", FakeTransport(bad))
        self.assertEqual((result["status"], result["error_code"], result["tool_reads"]),
                         ("failed", "model_response_invalid", 0))
        response = result["requests"][0]["response"]
        self.assertEqual(response["state"], "unknown")
        self.assertEqual(response["failure_diagnostic"]["stage"], "assistant_message")
        self.assertNotIn("SECRET_SENTINEL", json.dumps(response))
        self.assertNotIn("private-sentinel-key", json.dumps(response))

    def setUp(self):
        self.root = fixture_root()
        source = self.root / "snapshots" / "p01"
        source.mkdir(parents=True)
        (source / "entry.py").write_text("from helper import gate\n\ndef check(x):\n    return gate(x)\n",
                                          encoding="utf-8")
        (source / "helper.py").write_text("def gate(x):\n    return x > 0\n", encoding="utf-8")
        (self.root / "gold.json").write_text('{"secret":"never enter model"}', encoding="utf-8")
        self.case = {"id": "p01", "snapshot_root": "snapshots/p01",
            "question": "Does check return true for all integers?", "scenario": "Call check(0).",
            "assumptions": [], "authorized_files": ["entry.py", "helper.py"],
            "initial_evidence_files": ["entry.py"], "stratum": "retrieval_required",
            "evidence_parity": "unequal_by_design",
            "entrypoint": {"file": "entry.py", "symbol": "check"}}
        self.manifest_dir = self.root / "manifest"
        prepare_policy_manifest(self.case, dataset_root=self.root, out=self.manifest_dir,
                                model=MODEL)

    def run_arm(self, arm, transport, *, name=None):
        provider = DeepSeekProvider(model=MODEL["model_id"], transport=transport)
        return run_policy_arm(self.manifest_dir, dataset_root=self.root, arm=arm,
                              provider=provider, out=self.root / (name or "run-" + arm))

    def test_generation_wire_carries_canonical_refs_schema_in_all_four_arms(self):
        schema_marker = "Diagnosis JSON Schema:\n"
        expected_refs = {"type": "object", "additionalProperties": False,
            "required": ["file", "start", "end"], "properties": {
                "file": {"type": "string"},
                "start": {"type": "integer", "minimum": 1},
                "end": {"type": "integer", "minimum": 1}}}
        for arm in ("A", "B", "C", "D"):
            with self.subTest(arm=arm):
                answers = [reply(json.dumps(UNKNOWN))]
                if arm == "C":
                    answers.append(reply('{"status":"accept","coverage":"complete","reason":"Fixture only"}'))
                elif arm == "D":
                    answers = [reply(None, calls=[call("finish-1", "finish",
                        {"diagnosis": UNKNOWN})])]
                transport = FakeTransport(*answers)
                result = self.run_arm(arm, transport, name="wire-schema-" + arm)
                self.assertEqual(result["status"], "semantic_pending")
                wire = json.loads(transport.raw_bodies[0])
                prompt = wire["messages"][0]["content"]
                self.assertIn(schema_marker, prompt)
                sent_schema, _ = json.JSONDecoder().raw_decode(prompt.split(schema_marker, 1)[1])
                self.assertEqual(sent_schema, OUTPUT_SCHEMA)
                self.assertEqual(sent_schema["properties"]["refs"]["items"], expected_refs)
                manifest = json.loads((self.manifest_dir / "manifest.json").read_text())
                self.assertEqual(manifest["prompt_version"], "deepseek-policy-contracts-v3")
                properties = sent_schema['properties']
                self.assertEqual(properties['cause']['maxLength'], 600)
                self.assertEqual(properties['when']['maxLength'], 300)
                self.assertEqual(properties['unknowns']['items']['maxLength'], 200)
                self.assertIn('8192 UTF-8 bytes', prompt)
                self.assertIn('refs must be unique', prompt)
                if arm == "D":
                    finish = next(tool["function"] for tool in wire["tools"]
                                  if tool["function"]["name"] == "finish")
                    self.assertEqual(finish["parameters"]["properties"]["diagnosis"], sent_schema)
                else:
                    self.assertEqual(wire["response_format"], {"type": "json_object"})
                if arm == "C":
                    verifier = json.loads(transport.raw_bodies[1])["messages"][0]["content"]
                    self.assertNotIn(schema_marker, verifier)
                    for field in ("status", "coverage", "reason"):
                        self.assertIn(field, verifier)

    def test_refs_schema_fix_keeps_noncanonical_refs_rejected_without_normalization(self):
        diagnosis = {"verdict": "supported_defect", "cause": "The call delegates to gate.",
            "when": "Call check(0).", "refs": [{"file": "entry.py", "lines": "3-4",
                "quote": "def check(x):"}], "unknowns": []}
        for arm in ("A", "B", "C", "D"):
            with self.subTest(arm=arm):
                response = (reply(None, calls=[call("finish-1", "finish",
                    {"diagnosis": diagnosis})]) if arm == "D"
                    else reply(json.dumps(diagnosis)))
                transport = FakeTransport(response)
                result = self.run_arm(arm, transport, name="wrong-refs-" + arm)
                self.assertEqual((result["status"], result["error_code"]),
                                 ("failed", "eval_contract_invalid"))
                self.assertEqual((result["answer"], result["requests_attempted"]), (None, 1))
                self.assertFalse(result["semantic_proof"])

    def test_finish_tool_wire_declares_canonical_diagnosis_fields(self):
        transport = FakeTransport(reply(None, calls=[call("finish-1", "finish",
                                                         {"diagnosis": UNKNOWN})]))
        self.run_arm("D", transport, name="finish-schema")
        wire = json.loads(transport.raw_bodies[0])
        finish = next(tool["function"] for tool in wire["tools"]
                      if tool["function"]["name"] == "finish")
        self.assertEqual(finish["parameters"]["properties"]["diagnosis"], OUTPUT_SCHEMA)

    def test_canonical_refs_remain_pending_without_semantic_pass(self):
        diagnosis = {"verdict": "supported_defect", "cause": "The call delegates to gate.",
            "when": "Call check(0).", "refs": [{"file": "entry.py", "start": 3,
                "end": 4}], "unknowns": []}
        for arm in ("A", "B", "C", "D"):
            with self.subTest(arm=arm):
                answers = [reply(json.dumps(diagnosis))]
                if arm == "C":
                    answers.append(reply('{"status":"accept","coverage":"complete","reason":"Fixture only"}'))
                elif arm == "D":
                    answers = [reply(None, calls=[call("finish-1", "finish",
                        {"diagnosis": diagnosis})])]
                result = self.run_arm(arm, FakeTransport(*answers), name="canonical-refs-" + arm)
                self.assertEqual((result["status"], result["review_status"]),
                                 ("semantic_pending", "pending"))
                self.assertEqual(result["answer"]["refs"], diagnosis["refs"])
                self.assertFalse(result["semantic_proof"])

    def test_four_arms_are_pending_not_accuracy_claims_and_d_replays_transcript(self):
        observed_source = {}
        observed_payload = {}
        for arm in ("A", "B"):
            transport = FakeTransport(reply(json.dumps(UNKNOWN)))
            row = self.run_arm(arm, transport)
            self.assertEqual((row["status"], row["review_status"], row["requests_attempted"]),
                             ("semantic_pending", "pending", 1))
            self.assertEqual(row["model_calls_live"], 0)
            self.assertNotIn("never enter model", json.dumps(transport.posts))
            observed_payload[arm] = json.loads(transport.posts[0]["messages"][-1]["content"])
            observed_source[arm] = {s["file"] for s in observed_payload[arm]["source"]}
            self.assertEqual(transport.posts[0]["thinking"], {"type": "enabled"})
            self.assertEqual(transport.posts[0]["reasoning_effort"], "high")
        self.assertEqual(observed_source, {"A": {"entry.py", "helper.py"},
                                           "B": {"entry.py"}})
        self.assertNotIn("static_evidence", observed_payload["A"])
        self.assertEqual(observed_payload["B"]["static_evidence"]["basis"],
                         "ast_only_from_initial_source")
        self.assertTrue(observed_payload["B"]["static_evidence"]["calls"])
        c_transport = FakeTransport(reply(json.dumps(UNKNOWN)),
            reply('{"status":"accept","coverage":"complete","reason":"Fixture review only"}'))
        c = self.run_arm("C", c_transport)
        self.assertEqual((c["status"], c["requests_attempted"]), ("semantic_pending", 2))
        self.assertEqual({s["file"] for s in json.loads(
            c_transport.posts[0]["messages"][-1]["content"])["source"]}, observed_source["B"])
        self.assertEqual(json.loads(c_transport.posts[0]["messages"][-1]["content"]),
                         observed_payload["B"])
        self.assertIn("Review the provided candidate", c_transport.posts[1]["messages"][0]["content"])
        d_transport = FakeTransport(
            reply(None, calls=[call("read-1", "search", {"query": "gate"})]),
            reply(None, calls=[call("read-2", "read_caller", {"symbol": "gate"})]),
            reply(None, calls=[call("end-3", "finish", {"diagnosis": UNKNOWN})]))
        d = self.run_arm("D", d_transport)
        self.assertEqual((d["status"], d["requests_attempted"], d["tool_reads"]),
                         ("semantic_pending", 3, 2))
        second = d_transport.posts[1]["messages"]
        self.assertEqual(second[-2]["tool_calls"][0]["id"], "read-1")
        self.assertEqual(second[-2]["reasoning_content"], "I need static evidence.")
        self.assertEqual(second[-1]["tool_call_id"], "read-1")
        self.assertEqual(d_transport.posts[2]["messages"][-1]["tool_call_id"], "read-2")
        self.assertEqual([len(item["request"]["messages"]) for item in d["requests"]], [2, 4, 6])
        persisted = json.loads((self.root / "run-D" / "result.json").read_text())
        self.assertEqual([len(item["request"]["messages"]) for item in persisted["requests"]],
                         [2, 4, 6])
        self.assertEqual([item["request"] for item in d["requests"]], d_transport.posts)
        self.assertTrue((self.root / "run-D" / "claim.json").is_file())
        self.assertTrue((self.root / "run-D" / "result.json").is_file())
        for decision in (1, 2, 3):
            self.assertTrue((self.root / "run-D" / f"decision-{decision:02d}.reservation.json").is_file())
            self.assertTrue((self.root / "run-D" / f"decision-{decision:02d}.outcome.json").is_file())

    def test_wrong_cause_with_valid_label_never_becomes_automatic_success(self):
        wrong = {"verdict": "supported_defect", "cause": "The moon changed x.",
                 "when": "Call check(0).", "refs": [{"file": "entry.py", "start": 3, "end": 4}],
                 "unknowns": []}
        row = self.run_arm("A", FakeTransport(reply(json.dumps(wrong))))
        self.assertEqual(row["answer"]["verdict"], "supported_defect")
        self.assertEqual(row["status"], "semantic_pending")
        self.assertFalse(row["semantic_proof"])
        self.assertNotIn("diagnostic_passed", row)

    def test_ask_user_gap_is_unresolved_with_independent_abstention_review(self):
        gap = "The deployed flag value is outside the authorized source snapshot."
        transport = FakeTransport(reply(None, calls=[call("ask-1", "ask_user", {"gap": gap})]))
        row = self.run_arm("D", transport, name="run-ask-user")
        self.assertEqual((row["status"], row["review_status"], row.get("task_resolution"),
                          row.get("abstention_review_status")),
                         ("ask_user_terminated", "pending", "unresolved", "pending"))
        self.assertEqual((row["gap"], row["answer"]), (gap, None))
        self.assertEqual((row["requests_attempted"], len(transport.posts)), (1, 1))
        self.assertEqual((row["tool_events"][-1]["name"], row["tool_events"][-1]["terminal"]),
                         ("ask_user", True))
        self.assertFalse(row["semantic_proof"])
        self.assertNotIn("diagnostic_passed", row)

    def test_finish_unknown_still_awaits_semantic_review(self):
        transport = FakeTransport(reply(None, calls=[call("finish-1", "finish",
                                                        {"diagnosis": UNKNOWN})]))
        row = self.run_arm("D", transport, name="run-finish-unknown")
        self.assertEqual((row["status"], row["review_status"], row["requests_attempted"]),
                         ("semantic_pending", "pending", 1))
        self.assertEqual(row["answer"]["verdict"], "unknown")
        self.assertFalse(row["semantic_proof"])

    def test_unknown_outcome_keeps_reservation_and_prevents_repeat(self):
        transport = FakeTransport(TimeoutError())
        row = self.run_arm("A", transport)
        self.assertEqual(row["status"], "remote_outcome_unknown")
        self.assertEqual(row["reservations"][0]["state"], "reserved_unknown_outcome")
        with self.assertRaises(PolicyRunError):
            self.run_arm("A", transport)
        self.assertEqual(len(transport.posts), 1)
        reservation = json.loads((self.root / "run-A" / "decision-01.reservation.json").read_text())
        self.assertEqual(reservation["state"], "reserved_unknown_outcome")

    def test_interruption_preserves_durable_reservation_without_redispatch(self):
        transport = FakeTransport(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_arm("A", transport)
        folder = self.root / "run-A"
        self.assertTrue((folder / "claim.json").is_file())
        self.assertTrue((folder / "decision-01.reservation.json").is_file())
        self.assertFalse((folder / "decision-01.outcome.json").exists())
        self.assertFalse((folder / "result.json").exists())
        with self.assertRaises(PolicyRunError):
            self.run_arm("A", transport)
        self.assertEqual(len(transport.posts), 1)

    def test_malformed_response_is_contract_failure_with_reservation_kept(self):
        transport = FakeTransport({"choices": []})
        row = self.run_arm("A", transport)
        self.assertEqual((row["status"], row["error_code"]), ("failed", "model_response_invalid"))
        self.assertEqual(row["reservations"][0]["state"], "reserved_unknown_outcome")
        self.assertTrue((self.root / "run-A" / "decision-01.outcome.json").is_file())

    def test_third_decision_read_is_failure_without_fourth_dispatch(self):
        transport = FakeTransport(
            reply(None, calls=[call("r1", "search", {"query": "gate"})]),
            reply(None, calls=[call("r2", "read_symbol", {"file": "entry.py", "symbol": "check"})]),
            reply(None, calls=[call("r3", "search", {"query": "helper"})]))
        row = self.run_arm("D", transport)
        self.assertEqual(row["error_code"], "third_decision_must_terminate")
        self.assertEqual(row["requests_attempted"], 3)
        self.assertEqual(len(transport.posts), 3)

    def test_two_read_calls_in_one_decision_replay_both_tool_ids(self):
        transport = FakeTransport(
            reply(None, calls=[call("r1", "search", {"query": "gate"}),
                               call("r2", "read_caller", {"symbol": "gate"})]),
            reply(None, calls=[call("finish", "finish", {"diagnosis": UNKNOWN})]))
        row = self.run_arm("D", transport)
        self.assertEqual((row["status"], row["tool_reads"], row["requests_attempted"]),
                         ("semantic_pending", 2, 2))
        messages = transport.posts[1]["messages"]
        self.assertEqual([item["tool_call_id"] for item in messages if item["role"] == "tool"],
                         ["r1", "r2"])

    def test_gold_tool_request_fails_without_reading_gold(self):
        transport = FakeTransport(reply(None, calls=[call("bad", "read_symbol",
            {"file": "../gold.json", "symbol": "secret"})]))
        row = self.run_arm("D", transport)
        self.assertEqual(row["error_code"], "read_tool_invalid")
        self.assertNotIn("never enter model", json.dumps(row))

    def test_escaped_lone_surrogate_tool_args_fail_with_durable_result(self):
        for name, arguments in (("ask_user", {"gap": "\ud800"}),
                                ("search", {"query": "\ud800"})):
            with self.subTest(name=name):
                output = "run-surrogate-" + name
                transport = FakeTransport(reply(None, calls=[call("bad", name, arguments)]))
                row = self.run_arm("D", transport, name=output)
                self.assertEqual(row["status"], "failed")
                self.assertTrue((self.root / output / "result.json").is_file())
                self.assertTrue((self.root / output / "decision-01.reservation.json").is_file())

    def test_offline_marker_required_before_claim_or_dispatch(self):
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        transport.offline_fixture = False
        with self.assertRaisesRegex(PolicyRunError, "live_dispatch_disabled"):
            self.run_arm("A", transport)
        self.assertEqual(transport.posts, [])
        self.assertFalse((self.root / "run-A").exists())

    def test_request_budget_blocks_before_fake_transport(self):
        limited = self.root / "manifest-limited"
        prepare_policy_manifest(self.case, dataset_root=self.root, out=limited,
            model=MODEL, budget=DEFAULT_BUDGET | {"max_request_bytes": 1024})
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        provider = DeepSeekProvider(model=MODEL["model_id"], transport=transport)
        row = run_policy_arm(limited, dataset_root=self.root, arm="D", provider=provider,
                             out=self.root / "run-limited")
        self.assertEqual(row["error_code"], "request_bytes_limit")
        self.assertEqual(row["requests_attempted"], 0)
        self.assertEqual(transport.posts, [])

    def test_exact_provider_body_bytes_are_admitted_before_dispatch(self):
        preview = FakeTransport(reply(None, calls=[call("end", "finish", {"diagnosis": UNKNOWN})]))
        self.run_arm("D", preview, name="run-preview")
        exact_bytes = len(preview.raw_bodies[0])
        self.assertGreater(exact_bytes, 1024)
        limited = self.root / "manifest-exact-limit"
        prepare_policy_manifest(self.case, dataset_root=self.root, out=limited,
            model=MODEL, budget=DEFAULT_BUDGET | {"max_request_bytes": exact_bytes - 1})
        transport = FakeTransport(reply(None, calls=[call("end", "finish", {"diagnosis": UNKNOWN})]))
        row = run_policy_arm(limited, dataset_root=self.root, arm="D",
            provider=DeepSeekProvider(model=MODEL["model_id"], transport=transport),
            out=self.root / "run-exact-limit")
        self.assertEqual(row["error_code"], "request_bytes_limit")
        self.assertEqual(transport.posts, [])

    def test_small_record_budget_blocks_before_dispatch(self):
        limited = self.root / "manifest-small-record"
        prepare_policy_manifest(self.case, dataset_root=self.root, out=limited,
            model=MODEL, budget=DEFAULT_BUDGET | {"max_record_bytes": 1024})
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        row = run_policy_arm(limited, dataset_root=self.root, arm="A",
            provider=DeepSeekProvider(model=MODEL["model_id"], transport=transport),
            out=self.root / "run-small-record")
        self.assertEqual(row["error_code"], "run_record_preflight_limit")
        self.assertEqual(transport.posts, [])
        self.assertTrue((self.root / "run-small-record" / "result.json").is_file())

    def test_large_response_keeps_bounded_fallback_and_decision_receipt(self):
        limited = self.root / "manifest-fallback"
        prepare_policy_manifest(self.case, dataset_root=self.root, out=limited,
            model=MODEL, budget=DEFAULT_BUDGET | {"max_record_bytes": 4096})
        transport = FakeTransport(reply("x" * 5000))
        row = run_policy_arm(limited, dataset_root=self.root, arm="A",
            provider=DeepSeekProvider(model=MODEL["model_id"], transport=transport),
            out=self.root / "run-fallback")
        self.assertEqual(row["status"], "record_limit_exceeded")
        self.assertEqual(row["error_code"], "run_record_limit")
        self.assertFalse(row["artifact_complete"])
        self.assertTrue((self.root / "run-fallback" / "decision-01.reservation.json").is_file())
        response_record = json.loads((self.root / "run-fallback" / "decision-01.response.json").read_text())
        self.assertEqual(response_record["state"], "omitted_record_limit")
        self.assertFalse(response_record["artifact_complete"])
        self.assertTrue((self.root / "run-fallback" / "decision-01.outcome.json").is_file())
        self.assertEqual(json.loads((self.root / "run-fallback" / "result.json").read_text()), row)


if __name__ == "__main__":
    unittest.main()
