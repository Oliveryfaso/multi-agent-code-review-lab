"""Real diagnostic retrieval, solver, host gating and persistence; zero native calls."""
from copy import deepcopy
from dataclasses import asdict, replace
import io
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from debugger_fixtures import fixture_root, write_files
from macr.agents.orchestrator import Orchestrator
from macr.evals.diagnostic_compact import (
    VERSION, LEGACY_VERSION, REQUIREMENT_PRESERVING_PROMPT, protocol_schema, restore_scope)
from macr.memory.trace_store import TraceStore
from macr.providers.base import ModelBudget, ModelResponse, ProviderCapabilities, ProviderError
from macr.providers.local_http import LocalHTTPProvider
from macr.schemas import Evidence, Plan, PlanStep
from test_local_http_provider import completion, fixture_profile


BUDGET = ModelBudget(1536, 512, 300)
SOURCE = "def authorize(staff, paid):\n    return staff or paid\n"
QUESTION = "Does authorize enforce the stated access policy?"
SCENARIO = "Access requires both staff and paid. Both inputs are booleans."

# Sanitized development output, preserving the exact 456-byte model content.
# This is an offline replay witness, not a new native response or acceptance case.
RETAINED_DEV01_RAW = '''{
  "verdict": "supported_defect",
  "cause": "The function `allowed` incorrectly returns `staff or paid` instead of requiring both `staff` and `paid` to be `True`, violating the stated domain that access requires both staff and paid to be True.",
  "when": "The input (True, False) leads to access being granted, which contradicts the requirement that both staff and paid must be True.",
  "refs": ["e1"],
  "unknowns": [],
  "scope": "93df631e2006a56c"
}'''
CALLER_SOURCE = ('def allowed(staff, paid):\n'
    '    """Permit access only when staff and paid are both true."""\n'
    '    return staff or paid\n\n'
    'def access_status(staff, paid):\n'
    '    return "granted" if allowed(staff, paid) else "denied"\n\n'
    'def mixed_access():\n    return access_status(True, False)\n')
CALLER_QUESTION = ('allowed staff paid access_status: investigate possible access bug for boolean inputs; '
    'access requires both staff and paid True. Explain the supplied caller path and cite only retrieved evidence.')
CALLER_SCENARIO = 'Assess the supplied functions, their caller path, and the input domain stated in the question.'


def candidate(request, *, verdict="supported_defect", refs=None, unknowns=None):
    payload = json.loads(request.messages[-1]["content"])
    unknown = verdict == "unknown"
    return {"verdict": verdict, "cause": None if unknown else "OR permits access when only one condition holds.",
        "when": None if unknown else "staff=True and paid=False", "scope": payload["scope"],
        "refs": ([] if unknown else [payload["source"][0]["id"]]) if refs is None else refs,
        "unknowns": (["The dependency access contract is absent."] if unknown else []) if unknowns is None else unknowns}


def verification(request, *, changes=None, unknowns=None, coverage="complete", version=VERSION):
    payload = json.loads(request.messages[-1]["content"])
    checks = {"requirement": "established", "trigger": "reachable", "behavior": "violates",
              "protection": "absent", "counterevidence": "not_found"}
    checks.update(changes or {})
    refs = {name: [payload["source"][0]["id"]] for name in ("behavior", "trigger", "protection", "counterevidence")
        if (name == "behavior" and checks[name] != "unknown"
            or name == "trigger" and checks[name] == "excluded"
            or name == "protection" and checks[name] in ("effective", "ineffective")
            or name == "counterevidence" and checks[name] == "refutes")}
    result = {"scope": payload["scope"], "coverage": coverage,
              "reason": "These explicit checks concern the stated domain.", "unknowns": unknowns or []}
    if version == LEGACY_VERSION:
        return result | {"checks": checks, "refs": refs}
    return result | {"checks": {name: {"state": state, "refs": refs.get(name, [])}
                               for name, state in checks.items()}}


class FakeProvider:
    name, model = "fixture_offline", "fixture-model"

    def __init__(self, actions):
        self.actions, self.requests = list(actions), []

    def complete(self, request):
        self.requests.append(deepcopy(request))
        if not self.actions:
            raise AssertionError("diagnostic entry exceeded the frozen calls")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        value = action(request)
        return value if isinstance(value, ModelResponse) else ModelResponse(json.dumps(value),
            provider=self.name, model=self.model, usage={"input_tokens": 100, "output_tokens": 40}, usage_kind="measured")


class ProductionFakeTransport:
    def __init__(self, actions):
        self.actions, self.calls = list(actions), []
        self.bodies = []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.bodies.append(body)
        request = json.loads(body)
        self.calls.append(request)
        if not self.actions:
            raise AssertionError("unexpected production dispatch")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            # Actual production wire contains the builder's messages.
            value = action(type("WireRequest", (), {"messages": request["messages"]})())
            return completion(json.dumps(value), usage={"prompt_tokens": 100, "completion_tokens": 40})
        return deepcopy(action)


class DiagnosticOrchestratorTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("diagnostic integration is offline"))
            guard.start()
            self.addCleanup(guard.stop)
        self.repo = write_files(fixture_root(), {"access.py": SOURCE})
        self.store = TraceStore(fixture_root() / "traces")
        self.plan = Plan("bug_investigation", "high", [PlanStep("source", "text_search"),
            PlanStep("whole functions", "ast"), PlanStep("callers", "symbol_graph")], search_terms=["authorize"])

    def orchestrator(self, provider=None, *, use_llm_planner=False):
        agent = Orchestrator(trace_store=self.store, provider=provider, use_llm_planner=use_llm_planner)
        guard = patch.object(agent.rule_planner, "plan", return_value=self.plan)
        guard.start()
        self.addCleanup(guard.stop)
        return agent

    def run_agent(self, provider):
        return self.orchestrator(provider).run_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
            assumptions=[], budget=BUDGET)

    def persisted(self, trace):
        # This helper always reads actual disk bytes. Business/source binding
        # assertions below use the in-memory Trace and captured wire requests.
        path = self.store.root / f"{trace.task_id}.json"
        raw = path.read_text()
        stored = json.loads(raw)
        self.assertEqual((stored['schema'], stored['version']), ('macr.trace.summary', 1))
        self.assertEqual(stored['task_id'], trace.task_id)
        self.assertEqual(stored['diagnosis']['status'], trace.diagnosis['status'])
        self.assertEqual(stored['diagnosis']['candidate_present'], trace.diagnosis['candidate'] is not None)
        self.assertEqual(stored['diagnosis']['final_present'], trace.diagnosis['final'] is not None)
        self.assertEqual(stored['counts']['llm_calls'], len(trace.llm_calls))
        for key in ('ok', 'human_review_required'):
            self.assertEqual(stored['metrics']['final_review'][key], trace.metrics['final_review'][key])
        for key in ('query', 'repo_path', 'board'):
            self.assertNotIn(key, stored)
        for key in ('candidate', 'final', 'scope', 'candidate_request', 'verification_request', 'observations'):
            self.assertNotIn(key, stored['diagnosis'])
        self.assertNotIn(str(self.repo), raw)
        self.assertNotIn(QUESTION, raw)
        self.assertEqual(path.read_bytes(), (self.store.root / 'latest.json').read_bytes())
        return stored

    def test_one_full_source_completes_without_count_confidence_planner_or_patch_gates(self):
        provider = FakeProvider([candidate, verification])
        agent = self.orchestrator(provider, use_llm_planner=True)
        with patch.object(agent.planner, "plan", side_effect=AssertionError("diagnostic LLM planner forbidden")), \
                patch.object(agent.final_reviewer, "review", side_effect=AssertionError("legacy confidence gate forbidden")), \
                patch.object(agent.patcher, "propose", side_effect=AssertionError("diagnostic patches forbidden")):
            trace = agent.run_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.diagnosis["status"], "accepted")
        self.assertEqual(len(trace.answer.evidence), 1)
        self.assertEqual(trace.answer.evidence[0].snippet, SOURCE)
        self.assertEqual(trace.answer.confidence, 0.0)
        self.assertFalse(trace.diagnosis["semantic_proof"])
        self.assertTrue(trace.diagnosis["candidate_parsed"])
        self.assertTrue(trace.diagnosis["verification_parsed"])
        self.assertEqual(trace.diagnosis["verification"]["coverage"], "complete")
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertFalse(trace.metrics["final_review"]["ok"])
        self.assertTrue(trace.metrics["final_review"]["human_review_required"])
        self.assertEqual(trace.metrics["diagnostic"]["automatic_retries"], 0)
        payloads = [json.loads(request.messages[-1]["content"]) for request in provider.requests]
        self.assertEqual(payloads[0]["source"], payloads[1]["source"])
        self.assertEqual(len(payloads[0]["source"]), 1)
        self.assertEqual(payloads[0]["source"][0]["body"], SOURCE)
        self.assertEqual(payloads[0]["source"][0]["end"], 2)
        for request in provider.requests:
            self.assertEqual((request.max_input_tokens, request.max_output_tokens, request.timeout_seconds), (1536, 512, 300))
            self.assertEqual(request.tools, [])
        stored = self.persisted(trace)
        self.assertEqual(trace.diagnosis["final"], trace.diagnosis["candidate"])
        self.assertEqual(stored["metrics"]["final_review"]['issue_count'], len(trace.metrics["final_review"]['issues']))
        self.assertEqual(len(stored["llm_calls"]), 2)
        self.assertTrue(any(row["kind"] == "final" for row in trace.board["diagnostic"]))
        graph = trace.board["workflow"][0]["payload"]
        names = {node["name"] for node in graph["nodes"]}
        self.assertEqual(graph["mode"], "diagnostic")
        self.assertTrue({"diagnostic_candidate", "diagnostic_verifier", "diagnostic_final"} <= names)
        self.assertFalse({"solver", "final_review", "patch", "verification"} & names)
        self.assertFalse(next(node for node in graph["nodes"] if node["name"] == "planner")["uses_llm"])
        policy = trace.board["policy"][0]["payload"]
        self.assertFalse({"solver", "llm_solver", "final_review"} & {row["step"] for row in policy["executed_steps"]})

    def test_prepare_and_continue_persist_each_stage_without_regeneration(self):
        provider = FakeProvider([candidate, verification])
        agent = self.orchestrator(provider)
        trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.persisted(trace)["diagnosis"]["status"], "prepared")
        self.assertFalse(trace.diagnosis["candidate_parsed"])
        self.assertFalse(trace.diagnosis["verification_parsed"])
        agent.diagnostic_candidate(trace)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(self.persisted(trace)["diagnosis"]["status"], "candidate_completed")
        agent.verify_diagnostic(trace)
        self.assertEqual(len(provider.requests), 2)
        for method in (agent.diagnostic_candidate, agent.verify_diagnostic):
            with self.assertRaises(ProviderError) as error:
                method(trace)
            self.assertEqual(error.exception.code, "diagnostic_stage_invalid")
        self.assertEqual(len(provider.requests), 2)

    def test_three_irrelevant_locations_do_not_override_explicit_missing_checks(self):
        self.repo = write_files(fixture_root(), {"access.py": "".join(
            f"def authorize_{number}():\n    return {number}\n" for number in range(3))})
        def unrelated(request):
            payload = json.loads(request.messages[-1]["content"])
            self.assertEqual(len(payload["source"]), 3)
            return candidate(request, refs=[row["id"] for row in payload["source"][:2]])
        provider = FakeProvider([unrelated, lambda request: verification(request,
            changes={"requirement": "missing", "behavior": "unknown"},
            unknowns=["The source does not establish the access requirement."])])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "rejected")
        self.assertIsNotNone(trace.diagnosis["candidate"])
        self.assertIsNone(trace.diagnosis["final"])
        self.assertIsNone(trace.answer)
        self.assertFalse(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertEqual(trace.metrics["final_review"]["issues"], [trace.diagnosis["verification"]["error_code"]])
        self.assertTrue(self.persisted(trace)["metrics"]["final_review"]["human_review_required"])
        # Rejection follows explicit contradictory checks, not an NLP relevance proof.
        self.assertFalse(trace.diagnosis["verification"]["semantic_proof"])

    def test_false_trigger_and_invented_healthy_requirement_are_rejected_without_verdict_flip(self):
        for verdict, changes in (("supported_defect", {"trigger": "excluded"}),
                ("no_defect_supported", {"requirement": "missing", "behavior": "satisfies"})):
            with self.subTest(verdict=verdict):
                provider = FakeProvider([lambda request: candidate(request, verdict=verdict),
                    lambda request: verification(request, changes=changes)])
                trace = self.run_agent(provider)
                self.assertEqual(trace.diagnosis["status"], "rejected")
                self.assertEqual(trace.diagnosis["candidate"]["verdict"], verdict)
                self.assertIsNone(trace.diagnosis["final"])
                self.assertEqual(len(provider.requests), 2)

    def test_complete_specific_essential_gap_can_finish_unknown_with_empty_refs(self):
        provider = FakeProvider([lambda request: candidate(request, verdict="unknown"),
            lambda request: verification(request, changes={"behavior": "unknown"},
                unknowns=["The dependency access contract is absent."])])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "accepted")
        self.assertEqual(trace.diagnosis["final"]["verdict"], "unknown")
        self.assertEqual(trace.answer.evidence, [])
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])

    def test_unknown_without_essential_gap_is_rejected(self):
        provider = FakeProvider([lambda request: candidate(request, verdict="unknown"),
            lambda request: verification(request, changes={"behavior": "satisfies", "protection": "unknown"},
                unknowns=["A nonessential optimization is not documented."])])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "rejected")
        self.assertEqual(trace.diagnosis["verification"]["error_code"], "verification_essential_gap_missing")
        self.assertIsNone(trace.diagnosis["final"])

    def test_verifier_technical_failure_retains_candidate_and_known_failed_response(self):
        raw = '{"checks":'
        failed = ModelResponse(raw, finish_reason="length", provider="fixture_offline", model="fixture-model",
            usage={"input_tokens": 100, "output_tokens": 512}, usage_kind="measured")
        provider = FakeProvider([candidate, ProviderError("model_output_incomplete", response=failed)])
        trace = self.run_agent(provider)
        stored = self.persisted(trace)
        self.assertEqual(trace.diagnosis["status"], "failed")
        self.assertTrue(stored["diagnosis"]["candidate_present"])
        self.assertFalse(stored["diagnosis"]["final_present"])
        self.assertTrue(stored["diagnosis"]["candidate_parsed"])
        self.assertFalse(stored["diagnosis"]["verification_parsed"])
        self.assertEqual(stored["llm_calls"][-1]["usage"], failed.usage)
        self.assertEqual(stored["llm_calls"][-1]["finish_reason"], "length")
        self.assertEqual(stored["llm_calls"][-1]["failure_kind"], "incomplete")
        attempts = [row["payload"] for row in trace.board["diagnostic"] if row["kind"] == "attempt"]
        self.assertEqual(attempts[-1]["raw_output"], raw)
        self.assertEqual(len(provider.requests), 2)

    def test_source_or_attempt_drift_is_rejected_before_continuation_dispatch(self):
        for mutation in (lambda scope: scope["source"][0].update(body="different source\n"),
                         lambda scope: scope.update(attempt_id="previous-attempt")):
            with self.subTest(mutation=mutation):
                provider = FakeProvider([candidate])
                agent = self.orchestrator(provider)
                trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
                mutation(trace.diagnosis["scope"])
                with self.assertRaises(ProviderError):
                    agent.diagnostic_candidate(trace)
                self.assertEqual(provider.requests, [])

    def test_foreign_reference_or_previous_scope_output_fails_without_verifier_dispatch(self):
        for alteration, code in (({"refs": ["outside-id"]}, "compact_contract_invalid"),
                ({"scope": "previous-attempt"}, "compact_scope_mismatch")):
            with self.subTest(alteration=alteration):
                provider = FakeProvider([lambda request: candidate(request) | alteration])
                trace = self.run_agent(provider)
                self.assertEqual(trace.diagnosis["status"], "failed")
                self.assertEqual(trace.diagnosis["error_type"], code)
                self.assertIsNone(trace.diagnosis["final"])
                self.assertIsNone(trace.answer)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(trace.llm_calls[0]["usage_kind"], "measured")

    def test_source_read_is_bounded_cached_and_forbidden_paths_are_not_citable(self):
        agent = self.orchestrator()
        from macr.investigation.scope import read_safe
        evidence = [Evidence("access.py", 1, 2, "function", source_tool="parse_ast"),
                    Evidence("access.py", 2, 2, "line", source_tool="search_text")]
        with patch("macr.investigation.scope.read_safe", wraps=read_safe) as reader:
            selected = agent._diagnostic_sources(self.repo, evidence)
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].snippet, SOURCE)
        for path in ("../outside.py", "oracle/answer.py", "link.py"):
            with self.subTest(path=path):
                if path == "link.py":
                    (self.repo / path).symlink_to(self.repo / "access.py")
                try:
                    with self.assertRaises(ProviderError):
                        agent._diagnostic_sources(self.repo, [Evidence(path, 1, 1, "locator")])
                finally:
                    if path == "link.py":
                        (self.repo / path).unlink()

    def test_whole_module_redundancy_ignores_blank_lines_but_preserves_new_top_level_code(self):
        source = ("def lookup(key, backend):\n    value = backend.read(key)\n    return value\n\n"
            "def display(value):\n    return str(value)\n\n"
            "def show(key, backend):\n    return display(lookup(key, backend))\n")
        agent = self.orchestrator()
        functions = [Evidence("lookup.py", start, end, "function", source_tool="parse_ast")
                     for start, end in ((1, 3), (5, 6), (8, 9))]
        for addition in ("", "LIMIT = 4\n"):
            with self.subTest(new_top_level=bool(addition)):
                repo = write_files(fixture_root(), {"lookup.py": source + addition})
                evidence = functions + [Evidence("lookup.py", 1, 10 if addition else 9,
                    "module", source_tool="code_graph")]
                selected = agent._diagnostic_sources(repo, evidence)
                self.assertEqual(len(selected), 4 if addition else 3)
                self.assertEqual([row.source_tool for row in selected[:3]], ["parse_ast"] * 3)
                if addition:
                    self.assertEqual(selected[-1].snippet, source + addition)
                    self.assertEqual(selected[-1].line_end, 10)
                else:
                    self.assertFalse(any(row.source_tool == "code_graph" for row in selected))

    def production(self, actions, *, bound=100, diagnostic_schema_decoding=False):
        transport = ProductionFakeTransport(actions)
        profile = fixture_profile(context_limit=2048, output_limit=512, timeout_seconds=300,
            capabilities=ProviderCapabilities(json_mode=True, usage=True))
        if diagnostic_schema_decoding:
            profile = replace(profile, runtime="llama.cpp", runtime_version="b11521")
        provider = LocalHTTPProvider(profile, transport=transport, token_counter=lambda request, profile: bound,
                                     diagnostic_schema_decoding=diagnostic_schema_decoding)
        return provider, transport

    def test_prompt_variant_only_changes_candidate_wire_and_keeps_actual_request_in_memory(self):
        prepared = self.orchestrator().prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
            assumptions=[], budget=BUDGET)
        wires = []
        for variant in ('baseline', 'requirement_preserving'):
            provider, transport = self.production([candidate, verification], diagnostic_schema_decoding=True)
            requests = []
            def count(request, profile):
                requests.append(deepcopy(request))
                return 100
            provider.token_counter = count
            agent, trace = self.orchestrator(provider), deepcopy(prepared)
            agent.diagnostic_candidate(trace, prompt_variant=variant)
            self.persisted(trace)
            self.assertEqual(trace.diagnosis['candidate_request'], asdict(requests[0]))
            self.assertEqual(requests[0].messages, transport.calls[0]['messages'])
            agent.verify_diagnostic(trace)
            self.persisted(trace)
            self.assertEqual(trace.diagnosis['verification_request'], asdict(requests[1]))
            for body, actual_request in zip(transport.bodies, requests):
                self.assertIn(b'"schema": ' + json.dumps(actual_request.response_schema).encode(), body)
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(trace.diagnosis['final'], trace.diagnosis['candidate'])
            self.assertFalse(trace.diagnosis['semantic_proof'])
            self.assertTrue(trace.metrics['final_review']['human_review_required'])
            wires.append(deepcopy(transport.calls))
        self.assertEqual(wires[1][0]['messages'][0]['content'],
                         wires[0][0]['messages'][0]['content'] + '\n\n' + REQUIREMENT_PRESERVING_PROMPT)
        wires[1][0]['messages'][0]['content'] = wires[0][0]['messages'][0]['content']
        self.assertEqual(wires[0], wires[1])
        provider, transport = self.production([candidate, verification], diagnostic_schema_decoding=True)
        trace = self.orchestrator(provider).run_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
            assumptions=[], budget=BUDGET, prompt_variant='requirement_preserving')
        self.persisted(trace)
        recorded_request = trace.diagnosis['candidate_request']
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(recorded_request['messages'], transport.calls[0]['messages'])
        self.assertEqual(recorded_request['response_schema'], transport.calls[0]['response_format']['json_schema']['schema'])
        self.assertEqual(recorded_request['metadata']['diagnostic_prompt_variant'], 'requirement_preserving')
        self.assertTrue(recorded_request['messages'][0]['content'].endswith('\n\n' + REQUIREMENT_PRESERVING_PROMPT))
        self.assertEqual(transport.calls[1]['messages'][0]['content'], wires[0][1]['messages'][0]['content'])

    def test_invalid_prompt_variant_has_zero_dispatch_and_keeps_prepared_state(self):
        class StringSubclass(str):
            pass
        provider, transport = self.production([], diagnostic_schema_decoding=True)
        agent = self.orchestrator(provider)
        trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
        original = deepcopy(trace.diagnosis)
        original_summary = self.persisted(trace)
        scope = restore_scope(trace.diagnosis['scope'])
        for variant in ('unknown', '', None, True, [], {}, StringSubclass('baseline')):
            with self.subTest(variant=variant):
                with patch.object(agent, 'prepare_diagnostic', side_effect=AssertionError('invalid variant retrieved')):
                    with self.assertRaises(ProviderError) as error:
                        agent.run_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[],
                                             budget=BUDGET, prompt_variant=variant)
                self.assertEqual(error.exception.code, 'diagnostic_prompt_variant_invalid')
                self.assertEqual(error.exception.failure_kind, 'invalid_contract')
                with self.assertRaisesRegex(ProviderError, 'diagnostic_prompt_variant_invalid'):
                    agent.diagnostic_candidate(trace, prompt_variant=variant)
                with self.assertRaisesRegex(ProviderError, 'diagnostic_prompt_variant_invalid'):
                    agent.solver.diagnostic_candidate(scope, question=QUESTION, scenario=SCENARIO,
                        assumptions=[], model_budget=BUDGET, prompt_variant=variant)
                self.assertEqual(trace.diagnosis, original)
                self.assertEqual(self.persisted(trace), original_summary)
                self.assertEqual(transport.calls, [])

    def run_semantic_development(self, files, question, terms, candidate_action, verifier_action,
                                 *, diagnostic_schema_decoding=False):
        repo = write_files(fixture_root(), files)
        provider, transport = self.production([candidate_action, verifier_action], bound=700,
                                              diagnostic_schema_decoding=diagnostic_schema_decoding)
        trace = self.orchestrator(provider).run_diagnostic(repo, question, scenario=CALLER_SCENARIO,
            assumptions=[], budget=BUDGET, search_terms=terms)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(trace.diagnosis["protocol"], VERSION)
        self.assertEqual(trace.diagnosis["candidate_request"]["metadata"]["diagnostic_protocol"], VERSION)
        self.assertTrue(trace.diagnosis["candidate_parsed"])
        self.assertTrue(trace.diagnosis["verification_parsed"])
        self.assertFalse(trace.diagnosis["semantic_proof"])
        payloads = [json.loads(request["messages"][-1]["content"]) for request in transport.calls]
        self.assertEqual(payloads[0]["source"], payloads[1]["source"])
        if diagnostic_schema_decoding:
            scope = restore_scope(trace.diagnosis["scope"])
            schemas = [trace.diagnosis["candidate_request"]["response_schema"], protocol_schema(scope, verifier=True)]
            self.assertEqual(schemas[0], protocol_schema(scope))
            for request, schema in zip(transport.calls, schemas):
                self.assertEqual(request["response_format"], {"type": "json_schema", "json_schema": {"schema": schema}})
        self.assertEqual(len(payloads[0]["source"]), 3)
        self.assertTrue(all(row["complete"] and row["citable"] for row in payloads[0]["source"]))
        self.assertEqual(len(trace.diagnosis["candidate"]["refs"]), 3)
        self.assertEqual(payloads[1]["candidate"]["cause"], trace.diagnosis["candidate"]["cause"])
        self.assertEqual(payloads[1]["candidate"]["when"], trace.diagnosis["candidate"]["when"])
        self.assertNotIn("candidate_quality_warnings", payloads[1]["candidate"])
        self.assertEqual(trace.diagnosis["final"], trace.diagnosis["candidate"])
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertFalse(trace.metrics["final_review"]["ok"])
        self.assertTrue(self.persisted(trace)["metrics"]["final_review"]["human_review_required"])
        return trace

    def test_three_layer_correct_boolean_caller_with_three_refs_completes(self):
        def complete_caller(request):
            payload = json.loads(request.messages[-1]["content"])
            return {"verdict": "supported_defect", "scope": payload["scope"], "refs": ["e1", "e2", "e3"],
                "cause": "allowed uses staff OR paid although access requires both boolean inputs true.",
                "when": "mixed_access calls access_status(True,False), which calls allowed(True,False)=True and returns granted; the required result is denied.",
                "unknowns": []}
        def check_caller(request):
            result = verification(request)
            result.update(reason="OR grants the mixed caller; AND requires denial. The full supplied path is explained.")
            result["checks"]["behavior"]["refs"] = ["e1"]
            result["checks"]["trigger"]["refs"] = ["e2", "e3"]
            return result
        trace = self.run_semantic_development({"access.py": CALLER_SOURCE}, CALLER_QUESTION,
            ["allowed", "staff", "paid", "access_status"], complete_caller, check_caller)
        self.assertEqual([row.symbol for row in trace.answer.evidence], ["allowed", "access_status", "mixed_access"])
        self.assertIn("allowed(True,False)=True", trace.answer.answer)
        self.assertIn("required result is denied", trace.answer.answer)

    def test_effective_and_ineffective_guard_citations_follow_real_retrieval(self):
        for operator, state, verdict, value in (("or", "ineffective", "supported_defect", True),
                                               ("and", "effective", "no_defect_supported", False)):
            with self.subTest(protection=state):
                raw = []
                source = CALLER_SOURCE.replace("return staff or paid", f"return staff {operator} paid")
                def report(request):
                    result = candidate(request, verdict=verdict, refs=["e1", "e2", "e3"])
                    result.update(cause=f"The {operator.upper()} mechanism {'violates' if value else 'satisfies'} the both-True policy.",
                        when=f"mixed_access -> access_status(True,False) -> allowed(True,False)={value}; "
                             f"returns {'granted' if value else 'denied'}, expected denied.")
                    raw.append(json.dumps(result))
                    return result
                def check(request):
                    result = verification(request, changes={"behavior": "violates" if value else "satisfies",
                        "protection": state, "counterevidence": "does_not_refute" if value else "refutes"})
                    result.update(reason=f"{operator.upper()} returns {value} for the mixed caller; the both-True policy requires denial.")
                    raw.append(json.dumps(result))
                    return result
                trace = self.run_semantic_development({"access.py": source}, CALLER_QUESTION,
                    ["allowed", "staff", "paid", "access_status"], report, check, diagnostic_schema_decoding=True)
                checked = trace.diagnosis["verification"]
                bound = restore_scope(trace.diagnosis["scope"]).source[0]
                self.assertIn(f"return staff {operator} paid", bound.body)
                self.assertEqual(checked["refs"]["behavior"], [bound.citation()])
                self.assertEqual(checked["refs"]["protection"], checked["refs"]["behavior"])
                self.assertEqual(trace.diagnosis["final"]["verdict"], verdict)
                self.assertEqual(trace.answer.confidence, 0.0)
                self.assertEqual(len(trace.answer.evidence), 3)
                stored = self.persisted(trace)
                self.assertEqual([row["payload"]["raw_output"] for row in trace.board["diagnostic"]
                                  if row["kind"] == "attempt"], raw)
                self.assertEqual([row["usage"] for row in stored["llm_calls"]],
                                 [{"input_tokens": 100, "output_tokens": 40}] * 2)

    def test_protection_citations_missing_empty_or_foreign_fail_after_one_verifier(self):
        cases = [(state, mutation, "no_defect_supported" if state == "effective" else "supported_defect")
                 for state in ("effective", "ineffective") for mutation in ("missing", "empty", "foreign")]
        cases.append(("effective", "refutes_defect", "supported_defect"))
        for state, mutation, verdict in cases:
            with self.subTest(protection=state, mutation=mutation):
                operator = "and" if state == "effective" else "or"
                source = CALLER_SOURCE.replace("return staff or paid", f"return staff {operator} paid")
                raw = []
                def report(request):
                    result = candidate(request, verdict=verdict, refs=["e1", "e2", "e3"])
                    result.update(cause=f"The {operator.upper()} mechanism is reported to "
                        f"{'satisfy' if verdict == 'no_defect_supported' else 'violate'} the both-True policy.",
                        when=f"mixed_access -> access_status(True,False) -> allowed(True,False)={state == 'ineffective'}; "
                             f"returns {'denied' if state == 'effective' else 'granted'}, expected denied.")
                    raw.append(json.dumps(result))
                    return result
                def check(request):
                    result = verification(request, changes={"behavior": "satisfies" if state == "effective" else "violates",
                        "protection": state, "counterevidence": "refutes" if state == "effective" else "does_not_refute"})
                    if mutation == "missing":
                        result["checks"]["protection"].pop("refs")
                    elif mutation == "empty":
                        result["checks"]["protection"]["refs"] = []
                    elif mutation == "foreign":
                        result["checks"]["protection"]["refs"] = ["unretrieved"]
                    raw.append(json.dumps(result))
                    return result
                repo = write_files(fixture_root(), {"access.py": source})
                provider, transport = self.production([report, check], bound=700, diagnostic_schema_decoding=True)
                trace = self.orchestrator(provider).run_diagnostic(repo, CALLER_QUESTION, scenario=CALLER_SCENARIO,
                    assumptions=[], budget=BUDGET, search_terms=["allowed", "staff", "paid", "access_status"])
                self.assertEqual(len(transport.calls), 2)
                scope = restore_scope(trace.diagnosis["scope"])
                schemas = [trace.diagnosis["candidate_request"]["response_schema"], protocol_schema(scope, verifier=True)]
                self.assertEqual(schemas[0], protocol_schema(scope))
                for request, schema in zip(transport.calls, schemas):
                    self.assertEqual(request["response_format"], {"type": "json_schema", "json_schema": {"schema": schema}})
                payloads = [json.loads(request["messages"][-1]["content"]) for request in transport.calls]
                self.assertEqual(payloads[0]["source"], payloads[1]["source"])
                self.assertEqual(len(payloads[0]["source"]), 3)
                self.assertTrue(all(row["complete"] and row["citable"] for row in payloads[0]["source"]))
                self.assertEqual(trace.diagnosis["candidate"]["verdict"], verdict)
                self.assertIsNone(trace.diagnosis["final"])
                self.assertIsNone(trace.answer)
                if mutation == "refutes_defect":
                    self.assertEqual(trace.diagnosis["status"], "rejected")
                    self.assertEqual(trace.diagnosis["rejection_code"], "verification_cross_field_conflict")
                else:
                    self.assertEqual(trace.diagnosis["status"], "failed")
                    self.assertEqual(trace.diagnosis["error_type"], "compact_contract_invalid")
                    self.assertEqual(trace.diagnosis["failure_kind"], "invalid_contract")
                stored = self.persisted(trace)
                self.assertEqual([row["payload"]["raw_output"] for row in trace.board["diagnostic"]
                                  if row["kind"] == "attempt"], raw)
                self.assertEqual([row["usage"] for row in stored["llm_calls"]],
                                 [{"input_tokens": 100, "output_tokens": 40}] * 2)
                self.assertEqual([row["finish_reason"] for row in stored["llm_calls"]], ["stop", "stop"])

    def test_healthy_nonnegative_domain_and_done_above_total_caller_completes(self):
        source = ('def pending(done, total):\n'
            '    """Remaining jobs; completion at or above total returns zero."""\n'
            '    return max(0, total - done)\n\n'
            'def job_status(done, total):\n    return pending(done, total)\n\n'
            'def completed_count(total):\n    return job_status(total + 1, total)\n')
        question = ('pending job_status done total: investigate possible count bug; for nonnegative integers '
            'remaining count must be max(0, total-done), including done above total. Explain guards and caller path with retrieved citations.')
        def healthy_caller(request):
            payload = json.loads(request.messages[-1]["content"])
            return {"verdict": "no_defect_supported", "scope": payload["scope"], "refs": ["e1", "e2", "e3"],
                "cause": "pending clamps total-done at zero, so nonnegative integer inputs return the required remaining count, including done above total.",
                "when": "completed_count(total) calls job_status(total+1,total), then pending(total+1,total); max(0,-1)=0, matching the requirement.",
                "unknowns": []}
        def check_domain(request):
            result = verification(request, changes={"behavior": "satisfies"})
            result.update(reason="Clamp covers nonnegative inputs and the above-total caller; its result is correctly zero.")
            result["checks"]["behavior"]["refs"] = ["e1"]
            result["checks"]["trigger"]["refs"] = ["e2", "e3"]
            return result
        trace = self.run_semantic_development({"jobs.py": source}, question,
            ["pending", "job_status", "done", "total"], healthy_caller, check_domain)
        self.assertEqual(trace.diagnosis["final"]["verdict"], "no_defect_supported")
        self.assertIn("nonnegative integer", trace.answer.answer)
        self.assertIn("max(0,-1)=0", trace.answer.answer)

    def test_unknown_covers_known_caller_and_necessary_missing_dependency_without_invention(self):
        source = ('def lookup(key, registry):\n'
            '    """Delegate a present string key to an external registry."""\n'
            '    return registry.resolve(key)\n\n'
            'def display_lookup(key, registry):\n    return lookup(key, registry)\n\n'
            'def show_present(registry):\n    return display_lookup("present", registry)\n')
        question = ('lookup display_lookup registry resolve: investigate whether a present key is guaranteed '
            'to return without exception; the external registry.resolve implementation and contract are unavailable. '
            'Cite supplied path and name missing evidence without inventing guarantees.')
        gap = "show_present calls display_lookup('present',registry), then lookup and registry.resolve('present'); its absent contract/implementation prevents proving exception freedom."
        def unknown_caller(request):
            payload = json.loads(request.messages[-1]["content"])
            return {"verdict": "unknown", "scope": payload["scope"], "refs": ["e1", "e2", "e3"],
                    "cause": None, "when": None, "unknowns": [gap]}
        def check_gap(request):
            result = verification(request, changes={"trigger": "unknown", "behavior": "unknown",
                                                    "protection": "unknown"}, unknowns=[gap])
            result.update(reason="The supplied path is covered, but the absent external resolve contract prevents a guarantee.")
            return result
        trace = self.run_semantic_development({"lookup.py": source}, question,
            ["lookup", "display_lookup", "registry", "resolve"], unknown_caller, check_gap,
            diagnostic_schema_decoding=True)
        self.assertNotIn("protection", trace.diagnosis["verification"]["refs"])
        self.assertEqual(trace.diagnosis["final"]["verdict"], "unknown")
        self.assertEqual(trace.diagnosis["final"]["unknowns"], [gap])
        self.assertEqual(trace.diagnosis["candidate_quality_warnings"], [
            {"field": "unknowns[0]", "actual_chars": len(gap), "preferred_chars": 80}])
        self.assertEqual(trace.answer.next_steps, [gap])

    def test_exact_retained_dev01_raw_enters_one_verifier_with_warnings_but_incomplete_has_no_final(self):
        self.assertEqual(len(RETAINED_DEV01_RAW.encode()), 456)
        original = json.loads(RETAINED_DEV01_RAW)
        self.assertEqual((len(original["cause"]), len(original["when"])), (198, 127))
        raw_envelope = completion(RETAINED_DEV01_RAW, usage={"prompt_tokens": 658, "completion_tokens": 128})
        def incomplete(request):
            payload = json.loads(request.messages[-1]["content"])
            self.assertEqual(payload["candidate"]["cause"], original["cause"])
            self.assertEqual(payload["candidate"]["when"], original["when"])
            result = verification(request, coverage="incomplete", version=LEGACY_VERSION)
            result.update(reason="The candidate omits mixed_access and access_status argument propagation and result.")
            return result
        provider, transport = self.production([raw_envelope, incomplete], bound=700, diagnostic_schema_decoding=True)
        repo = write_files(fixture_root(), {"access.py": CALLER_SOURCE})
        agent = self.orchestrator(provider)
        with patch("macr.schemas.uuid4", return_value=UUID("438d09d6-8af2-4ae1-b875-9b757f831aa5")), \
                patch("macr.agents.orchestrator.uuid4", return_value=UUID("41814965-0eb5-435f-a872-2182bb85341f")):
            trace = agent.prepare_diagnostic(repo, CALLER_QUESTION, scenario=CALLER_SCENARIO,
                assumptions=[], budget=BUDGET, search_terms=["allowed", "staff", "paid", "access_status"])
            trace.diagnosis["protocol"] = LEGACY_VERSION
            agent.diagnostic_candidate(trace)
            agent.verify_diagnostic(trace)
        self.assertEqual(restore_scope(trace.diagnosis["scope"]).binding, original["scope"])
        self.assertEqual(trace.diagnosis["status"], "rejected")
        self.assertEqual(trace.diagnosis["rejection_code"], "verification_task_incomplete")
        self.assertEqual(trace.diagnosis["candidate"]["cause"], original["cause"])
        self.assertEqual(trace.diagnosis["candidate"]["when"], original["when"])
        self.assertEqual(set(trace.diagnosis["candidate"]), {"verdict", "cause", "when", "refs", "unknowns"})
        self.assertEqual(trace.diagnosis["candidate_quality_warnings"], [
            {"field": "cause", "actual_chars": 198, "preferred_chars": 160},
            {"field": "when", "actual_chars": 127, "preferred_chars": 80}])
        self.assertEqual(trace.diagnosis["verification"]["coverage"], "incomplete")
        self.assertTrue(trace.diagnosis["verification_parsed"])
        self.assertIsNone(trace.diagnosis["final"])
        self.assertIsNone(trace.answer)
        self.assertFalse(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertFalse(trace.diagnosis["semantic_proof"])
        self.assertEqual(len(transport.calls), 2)
        stored = self.persisted(trace)
        self.assertEqual(stored["diagnosis"]["protocol"], LEGACY_VERSION)
        self.assertEqual(trace.diagnosis["candidate_request"]["metadata"]["diagnostic_protocol"], LEGACY_VERSION)
        self.assertEqual(trace.diagnosis["verification_request"]["metadata"]["diagnostic_protocol"], LEGACY_VERSION)
        scope = restore_scope(trace.diagnosis["scope"])
        for body, verifier in zip(transport.bodies, (False, True)):
            schema = protocol_schema(scope, verifier=verifier, version=LEGACY_VERSION)
            self.assertIn(b'"schema": ' + json.dumps(schema).encode(), body)
        attempts = [row["payload"] for row in trace.board["diagnostic"] if row["kind"] == "attempt"]
        self.assertEqual(attempts[0]["raw_output"], RETAINED_DEV01_RAW)
        self.assertEqual(stored["llm_calls"][0]["usage"], {"input_tokens": 658, "output_tokens": 128})
        self.assertEqual(stored["llm_calls"][0]["finish_reason"], "stop")
        self.assertEqual(stored["llm_calls"][1]["usage"], {"input_tokens": 100, "output_tokens": 40})
        self.assertEqual(trace.metrics["final_review"]["issues"], ["verification_task_incomplete"])
        self.assertEqual(stored["metrics"]["final_review"]["issue_count"], 1)

    def test_missing_coverage_is_invalid_contract_and_preserves_candidate_and_verifier_usage(self):
        def missing_coverage(request):
            result = verification(request)
            del result["coverage"]
            return result
        provider, transport = self.production([candidate, missing_coverage])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "failed")
        self.assertEqual(trace.diagnosis["error_type"], "compact_contract_invalid")
        self.assertEqual(trace.diagnosis["failure_kind"], "invalid_contract")
        self.assertTrue(trace.diagnosis["candidate_parsed"])
        self.assertFalse(trace.diagnosis["verification_parsed"])
        self.assertIsNotNone(trace.diagnosis["candidate"])
        self.assertIsNone(trace.diagnosis["final"])
        self.assertEqual(len(transport.calls), 2)
        stored = self.persisted(trace)
        self.assertEqual(stored["llm_calls"][-1]["usage"], {"input_tokens": 100, "output_tokens": 40})
        self.assertEqual(stored["llm_calls"][-1]["finish_reason"], "stop")

    def test_unsupported_or_mismatched_protocol_cannot_silently_resume(self):
        provider = FakeProvider([candidate])
        agent = self.orchestrator(provider)
        trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
        trace.diagnosis["protocol"] = "diagnostic-compact-v1"
        with self.assertRaises(ProviderError) as error:
            agent.diagnostic_candidate(trace)
        self.assertEqual(error.exception.code, "diagnostic_protocol_mismatch")
        self.assertEqual(provider.requests, [])
        trace.diagnosis["protocol"] = VERSION
        agent.diagnostic_candidate(trace)
        trace.diagnosis["protocol"] = LEGACY_VERSION
        with self.assertRaisesRegex(ProviderError, 'diagnostic_protocol_mismatch'):
            agent.verify_diagnostic(trace)
        self.assertEqual(trace.diagnosis["status"], "candidate_completed")
        self.assertEqual(len(provider.requests), 1)
        scope = restore_scope(trace.diagnosis["scope"])
        for version in (None, {}, 'diagnostic-compact-v1'):
            with self.subTest(version=version), self.assertRaisesRegex(ProviderError, 'compact_protocol_mismatch'):
                agent.solver.diagnostic_candidate(scope, question=QUESTION, scenario=SCENARIO,
                    assumptions=[], model_budget=BUDGET, version=version)
            self.assertEqual(len(provider.requests), 1)
        trace.diagnosis["protocol"] = VERSION
        trace.diagnosis["candidate_request"]["metadata"]["diagnostic_protocol"] = "diagnostic-compact-v1"
        with self.assertRaises(ProviderError) as error:
            agent.verify_diagnostic(trace)
        self.assertEqual(error.exception.code, "diagnostic_protocol_mismatch")
        self.assertEqual(trace.diagnosis["status"], "candidate_completed")
        self.assertEqual(len(provider.requests), 1)

    def test_actual_production_parser_success_through_full_entry(self):
        from macr.cli import _print_trace
        from macr.viewer import TraceViewer
        provider, transport = self.production([candidate, verification])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "accepted")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual([row["max_tokens"] for row in transport.calls], [512, 512])
        self.assertEqual(trace.llm_calls[0]["provider"], "local_http")
        self.assertEqual(trace.llm_calls[0]["usage_kind"], "measured")
        self.assertEqual(restore_scope(trace.diagnosis["scope"]).source[0].body, SOURCE)
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertFalse(trace.metrics["final_review"]["ok"])
        self.assertTrue(trace.metrics["final_review"]["human_review_required"])
        self.assertFalse(trace.diagnosis["semantic_proof"])
        stored = self.persisted(trace)
        self.assertEqual(stored["metrics"]["final_review"]["human_review_required"], True)
        review_issue = "contract/source checks passed; semantic/task coverage needs review"
        self.assertEqual(trace.metrics["final_review"]["issues"], [review_issue])
        self.assertEqual(stored["metrics"]["final_review"]["issue_count"], 1)
        output = io.StringIO()
        with patch("sys.stdout", output):
            _print_trace(trace, as_json=False)
        self.assertIn("Human Review Required", output.getvalue())
        self.assertIn(review_issue, output.getvalue())
        self.assertNotIn("Rerun with --clarification", output.getvalue())
        rendered = TraceViewer(root=fixture_root(), trace_path=self.store.root / f"{trace.task_id}.json").render()
        self.assertIn("Human review required", rendered)
        self.assertIn("Content omitted", rendered)
        self.assertNotIn(review_issue, rendered)
        generic = deepcopy(trace)
        generic.metrics["final_review"] = {"ok": False, "human_review_required": True,
                                          "questions": ["Which retrieved source needs clarification?"]}
        output = io.StringIO()
        with patch("sys.stdout", output):
            _print_trace(generic, as_json=False)
        self.assertIn("Rerun with --clarification", output.getvalue())
        self.assertIn(generic.metrics["final_review"]["questions"][0], output.getvalue())

    def test_production_length_invalid_json_shape_and_timeout_have_no_repair_or_fallback(self):
        for content, finish, code, kind in (("{\"verdict\":", "length", "model_output_incomplete", "incomplete"),
                ("not JSON", "stop", "model_output_invalid", "invalid_json"),
                ("{}", "stop", "compact_scope_mismatch", "invalid_contract")):
            with self.subTest(content=content):
                envelope = completion(content, usage={"prompt_tokens": 100, "completion_tokens": 40})
                envelope["choices"][0]["finish_reason"] = finish
                provider, transport = self.production([envelope])
                trace = self.run_agent(provider)
                stored = self.persisted(trace)
                self.assertEqual(trace.diagnosis["status"], "failed")
                self.assertEqual(trace.diagnosis["error_type"], code)
                self.assertEqual(trace.diagnosis["failure_kind"], kind)
                self.assertIsNone(trace.answer)
                self.assertIsNone(trace.diagnosis["candidate"])
                self.assertIsNone(trace.diagnosis["final"])
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(stored["llm_calls"][0]["usage"], {"input_tokens": 100, "output_tokens": 40})
                self.assertEqual(stored["llm_calls"][0]["finish_reason"], finish)
                raw = [row["payload"]["raw_output"] for row in trace.board["diagnostic"] if row["kind"] == "attempt"]
                self.assertEqual(raw, [content])
        provider, transport = self.production([TimeoutError()])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["failure_kind"], "transport")
        self.assertEqual(trace.llm_calls[0]["usage_kind"], "unknown")
        self.assertEqual(trace.llm_calls[0]["usage"], {})
        self.assertEqual(len(transport.calls), 1)

    def test_trusted_token_overflow_refuses_zero_transport_dispatch(self):
        provider, transport = self.production([], bound=1537)
        trace = self.run_agent(provider)
        self.assertEqual(transport.calls, [])
        self.assertEqual(trace.diagnosis["status"], "failed")
        self.assertEqual(trace.diagnosis["error_type"], "model_context_overflow")
        self.assertIsNone(trace.diagnosis["candidate"])
        self.assertFalse(trace.metrics["run_outcome"]["end_to_end_completed"])

    def test_reference_cannot_enter_explicit_diagnostic_scope(self):
        agent = self.orchestrator(FakeProvider([]))
        with self.assertRaises(TypeError):
            agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET,
                reference="private evaluator answer")
        with self.assertRaises(ProviderError) as error:
            agent.prepare_diagnostic(self.repo, "\ud800", scenario=SCENARIO, assumptions=[], budget=BUDGET)
        self.assertEqual(error.exception.code, "diagnostic_input_invalid")

    def test_fact_checks_reach_both_production_requests_and_keep_exact_packet_in_memory(self):
        provider, transport = self.production([candidate, verification], diagnostic_schema_decoding=True)
        agent = self.orchestrator(provider)
        trace = agent.run_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[],
            budget=BUDGET, fact_checks=True, prompt_variant='requirement_preserving')
        self.assertEqual(len(transport.calls), 2)
        payloads = [json.loads(call['messages'][-1]['content']) for call in transport.calls]
        self.assertEqual(payloads[0]['observations'], payloads[1]['observations'])
        self.assertEqual(trace.diagnosis['observations'], payloads[0]['observations'])
        self.assertEqual(payloads[0]['question'], QUESTION)
        self.assertEqual(payloads[0]['scenario'], SCENARIO)
        self.assertEqual(payloads[0]['observations']['rows'][0]['return'], 'staff or paid')
        self.assertEqual(trace.diagnosis['final'], trace.diagnosis['candidate'])
        self.assertFalse(trace.diagnosis['semantic_proof'])
        self.assertTrue(trace.metrics['final_review']['human_review_required'])
        self.persisted(trace)
        recorded_diagnosis = trace.diagnosis
        for name in ('candidate_request', 'verification_request'):
            self.assertEqual(recorded_diagnosis[name]['metadata']['diagnostic_observation_version'], 'visible_syntax_v1')
            self.assertEqual((recorded_diagnosis[name]['max_input_tokens'], recorded_diagnosis[name]['max_output_tokens']), (1536, 512))
        self.assertEqual(recorded_diagnosis['candidate_request']['metadata']['diagnostic_observation_sha256'],
                         recorded_diagnosis['verification_request']['metadata']['diagnostic_observation_sha256'])
        scope = restore_scope(trace.diagnosis['scope'])
        for call, verifier in zip(transport.calls, (False, True)):
            self.assertEqual(call['response_format']['json_schema']['schema'], protocol_schema(scope, verifier=verifier))

    def test_enlarged_budget_reaches_both_actual_requests_and_keeps_length_incomplete(self):
        budget = ModelBudget(3072, 1024, 600)
        for truncated_role in (None, 'candidate', 'verifier'):
            with self.subTest(truncated_role=truncated_role):
                provider, transport = self.production([candidate, verification], diagnostic_schema_decoding=True)
                provider.profile = replace(provider.profile, context_limit=4096, output_limit=1024,
                                           timeout_seconds=600)
                provider.token_counter = lambda request, profile: 2500
                original_post = transport.post
                timeouts = []
                def post(endpoint, body, timeout_seconds, max_response_bytes):
                    timeouts.append(timeout_seconds)
                    envelope = original_post(endpoint, body, timeout_seconds, max_response_bytes)
                    role = 'candidate' if len(transport.calls) == 1 else 'verifier'
                    if role == truncated_role:
                        envelope['choices'][0]['finish_reason'] = 'length'
                        envelope['usage'] = {'prompt_tokens': 100, 'completion_tokens': 1024,
                                             'total_tokens': 1124}
                    return envelope
                transport.post = post
                agent = self.orchestrator(provider)
                trace = agent.run_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[],
                    budget=budget, fact_checks=True)
                expected_calls = 1 if truncated_role == 'candidate' else 2
                self.assertEqual(len(transport.calls), expected_calls)
                self.assertEqual(timeouts, [600] * expected_calls)
                self.assertEqual([row['max_tokens'] for row in transport.calls], [1024] * expected_calls)
                stored = self.persisted(trace)
                for name in ('candidate_request', 'verification_request')[:expected_calls]:
                    self.assertEqual({key: trace.diagnosis[name][key] for key in asdict(budget)},
                                     asdict(budget))
                if truncated_role is None:
                    self.assertEqual(trace.diagnosis['status'], 'accepted')
                    self.assertEqual(trace.diagnosis['final'], trace.diagnosis['candidate'])
                else:
                    self.assertIsNone(trace.diagnosis['final'])
                    self.assertEqual(trace.diagnosis['error_type'], 'model_output_incomplete')
                    self.assertEqual(stored['llm_calls'][-1]['finish_reason'], 'length')
                    self.assertEqual(stored['llm_calls'][-1]['usage']['output_tokens'], 1024)
                    attempts = [row['payload'] for row in trace.board['diagnostic']
                                if row['kind'] == 'attempt']
                    self.assertIsInstance(json.loads(attempts[-1]['raw_output']), dict)

    def test_fact_aids_allow_verifier_refusal_of_false_requirement_or_guard_without_flip(self):
        scenarios = [
            ('def gate(approved, dependency):\n    return approved and dependency()\n',
             'The fixed dependency returns bool; only approved determines the required result.', 'no_defect_supported'),
            ('def gate(stale, pinned):\n    if pinned:\n        return "keep"\n    return helper(pinned, stale)\n',
             'Pinned records must be kept; inspect the known guard before claiming a helper call.', 'supported_defect')]
        for source, scenario, verdict in scenarios:
            with self.subTest(verdict=verdict):
                repo = write_files(fixture_root(), {'gate.py': source})
                def false_report(request):
                    report = candidate(request, verdict=verdict)
                    report.update(cause='The implementation condition is the complete requirement.',
                                  when='The protected branch calls the helper and proves its result.')
                    return report
                def refuse(request):
                    payload = json.loads(request.messages[-1]['content'])
                    self.assertEqual(payload['scenario'], scenario)
                    self.assertTrue(payload['observations']['rows'])
                    checked = verification(request, changes={'behavior': 'satisfies'} if verdict == 'no_defect_supported' else {})
                    checked.update(coverage='incomplete', reason='The original requirement/guard claim is unsupported; do not repair this candidate.')
                    return checked
                provider, transport = self.production([false_report, refuse], diagnostic_schema_decoding=True)
                trace = self.orchestrator(provider).run_diagnostic(repo, 'Check the gate caller and requirement.',
                    scenario=scenario, assumptions=[], budget=BUDGET, search_terms=['gate'], fact_checks=True)
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(trace.diagnosis['candidate']['verdict'], verdict)
                self.assertEqual(trace.diagnosis['status'], 'rejected')
                self.assertEqual(trace.diagnosis['rejection_code'], 'verification_task_incomplete')
                self.assertIsNone(trace.diagnosis['final'])
                self.assertTrue(all(call['usage_kind'] == 'measured' for call in trace.llm_calls))

    def test_fact_packet_mutation_or_downgrade_has_zero_new_dispatch_and_keeps_stage(self):
        for phase, mutation in (('candidate', 'packet'), ('candidate', 'mode'),
                                ('verifier', 'packet'), ('verifier', 'digest'), ('verifier', 'downgrade')):
            with self.subTest(phase=phase, mutation=mutation):
                provider = FakeProvider([candidate, verification])
                agent = self.orchestrator(provider)
                trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
                                                assumptions=[], budget=BUDGET, fact_checks=True)
                if phase == 'verifier':
                    agent.diagnostic_candidate(trace)
                if mutation == 'packet':
                    trace.diagnosis['observations']['rows'][0]['ref'] = 'foreign'
                elif mutation == 'mode':
                    trace.diagnosis['fact_checks'] = None
                elif mutation == 'digest':
                    trace.diagnosis['candidate_request']['metadata']['diagnostic_observation_sha256'] = '0' * 64
                else:
                    request = trace.diagnosis['candidate_request']
                    payload = json.loads(request['messages'][-1]['content'])
                    del payload['observations']
                    request['messages'][-1]['content'] = json.dumps(payload)
                    for key in ('diagnostic_observation_version', 'diagnostic_observation_sha256'):
                        del request['metadata'][key]
                before, calls = deepcopy(trace.diagnosis), len(provider.requests)
                with self.assertRaises(ProviderError):
                    agent.diagnostic_candidate(trace) if phase == 'candidate' else agent.verify_diagnostic(trace)
                self.assertEqual(trace.diagnosis, before)
                self.assertEqual(len(provider.requests), calls)
        with self.assertRaisesRegex(ProviderError, 'diagnostic_input_invalid'):
            self.orchestrator().prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
                                                   assumptions=[], budget=BUDGET, fact_checks=1)

    def test_fact_checks_preserve_length_failure_raw_usage_and_zero_overflow_dispatch(self):
        failed = completion('{"verdict":', usage={'prompt_tokens': 100, 'completion_tokens': 40})
        failed['choices'][0]['finish_reason'] = 'length'
        provider, transport = self.production([failed], diagnostic_schema_decoding=True)
        trace = self.orchestrator(provider).run_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
            assumptions=[], budget=BUDGET, fact_checks=True)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(trace.diagnosis['error_type'], 'model_output_incomplete')
        self.assertIsNone(trace.diagnosis['candidate'])
        self.assertIsNone(trace.diagnosis['final'])
        self.assertEqual(trace.llm_calls[0]['usage'], {'input_tokens': 100, 'output_tokens': 40})
        self.assertEqual(trace.llm_calls[0]['finish_reason'], 'length')
        self.assertIn('observations', json.loads(trace.diagnosis['candidate_request']['messages'][-1]['content']))
        provider, transport = self.production([], bound=1537, diagnostic_schema_decoding=True)
        trace = self.orchestrator(provider).run_diagnostic(self.repo, QUESTION, scenario=SCENARIO,
            assumptions=[], budget=BUDGET, fact_checks=True)
        self.assertEqual(transport.calls, [])
        self.assertEqual(trace.diagnosis['error_type'], 'model_context_overflow')
        self.assertIsNone(trace.diagnosis['final'])

    def test_fact_checks_still_allow_faithful_healthy_domain_and_specific_missing_guarantee(self):
        examples = [
            ('def authorize(left, right):\n    return left and right\n',
             'For boolean inputs, grant exactly when both inputs are true.', 'no_defect_supported'),
            ('def authorize(approved, dependency):\n    return approved and dependency()\n',
             'Require exactly approved; deployed dependency returns bool, with no return-value guarantee.', 'unknown')]
        for source, scenario, verdict in examples:
            with self.subTest(verdict=verdict):
                repo = write_files(fixture_root(), {'access.py': source})
                gap = 'For approved=True, the fixed dependency may return True or False; its missing deployed return guarantee prevents proving the approved-only requirement.'
                def report(request):
                    result = candidate(request, verdict=verdict, unknowns=[gap] if verdict == 'unknown' else [])
                    if verdict == 'no_defect_supported':
                        result.update(cause='AND grants both-True and denies every other boolean pair.',
                                      when='authorize receives the two boolean inputs and returns exactly their conjunction, matching the whole required domain.')
                    return result
                def check(request):
                    result = verification(request, changes={'behavior': 'satisfies'} if verdict == 'no_defect_supported'
                        else {'trigger': 'unknown', 'behavior': 'unknown', 'protection': 'unknown'},
                        unknowns=[gap] if verdict == 'unknown' else [])
                    result['reason'] = 'The original requirement and supplied expression support this report without inventing the dependency value.'
                    return result
                provider, transport = self.production([report, check], diagnostic_schema_decoding=True)
                trace = self.orchestrator(provider).run_diagnostic(repo, 'Review authorize against this requirement.',
                    scenario=scenario, assumptions=[], budget=BUDGET, fact_checks=True)
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(trace.diagnosis['final']['verdict'], verdict)
                self.assertFalse(trace.diagnosis['semantic_proof'])
                self.assertTrue(trace.metrics['final_review']['human_review_required'])
