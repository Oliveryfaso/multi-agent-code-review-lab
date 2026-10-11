"""Real diagnostic retrieval, solver, host gating and persistence; zero native calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.agents.orchestrator import Orchestrator
from macr.evals.diagnostic_compact import restore_scope
from macr.memory.trace_store import TraceStore
from macr.providers.base import ModelBudget, ModelResponse, ProviderCapabilities, ProviderError
from macr.providers.local_http import LocalHTTPProvider
from macr.schemas import Evidence, Plan, PlanStep
from test_local_http_provider import completion, fixture_profile


BUDGET = ModelBudget(1536, 512, 300)
SOURCE = "def authorize(staff, paid):\n    return staff or paid\n"
QUESTION = "Does authorize enforce the stated access policy?"
SCENARIO = "Access requires both staff and paid. Both inputs are booleans."


def candidate(request, *, verdict="supported_defect", refs=None, unknowns=None):
    payload = json.loads(request.messages[-1]["content"])
    unknown = verdict == "unknown"
    return {"verdict": verdict, "cause": None if unknown else "OR permits access when only one condition holds.",
        "when": None if unknown else "staff=True and paid=False", "scope": payload["scope"],
        "refs": ([] if unknown else [payload["source"][0]["id"]]) if refs is None else refs,
        "unknowns": (["The dependency access contract is absent."] if unknown else []) if unknowns is None else unknowns}


def verification(request, *, changes=None, unknowns=None):
    payload = json.loads(request.messages[-1]["content"])
    checks = {"requirement": "established", "trigger": "reachable", "behavior": "violates",
              "protection": "absent", "counterevidence": "not_found"}
    checks.update(changes or {})
    refs = {name: [payload["source"][0]["id"]] for name in ("behavior", "trigger", "protection", "counterevidence")
        if (name == "behavior" and checks[name] != "unknown"
            or name == "trigger" and checks[name] == "excluded"
            or name == "protection" and checks[name] in ("effective", "ineffective")
            or name == "counterevidence" and checks[name] == "refutes")}
    return {"scope": payload["scope"], "checks": checks, "refs": refs,
            "reason": "These explicit checks concern the stated domain.", "unknowns": unknowns or []}


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

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
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
        return json.loads((self.store.root / f"{trace.task_id}.json").read_text())

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
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])
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
        self.assertEqual(stored["diagnosis"]["final"], trace.diagnosis["candidate"])
        self.assertEqual(len(stored["llm_calls"]), 2)
        self.assertTrue(any(row["kind"] == "final" for row in stored["board"]["diagnostic"]))
        graph = stored["board"]["workflow"][0]["payload"]
        names = {node["name"] for node in graph["nodes"]}
        self.assertEqual(graph["mode"], "diagnostic")
        self.assertTrue({"diagnostic_candidate", "diagnostic_verifier", "diagnostic_final"} <= names)
        self.assertFalse({"solver", "final_review", "patch", "verification"} & names)
        self.assertFalse(next(node for node in graph["nodes"] if node["name"] == "planner")["uses_llm"])
        policy = stored["board"]["policy"][0]["payload"]
        self.assertFalse({"solver", "llm_solver", "final_review"} & {row["step"] for row in policy["executed_steps"]})

    def test_prepare_and_continue_persist_each_stage_without_regeneration(self):
        provider = FakeProvider([candidate, verification])
        agent = self.orchestrator(provider)
        trace = agent.prepare_diagnostic(self.repo, QUESTION, scenario=SCENARIO, assumptions=[], budget=BUDGET)
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.persisted(trace)["diagnosis"]["status"], "prepared")
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
        self.assertIsNotNone(stored["diagnosis"]["candidate"])
        self.assertIsNone(stored["diagnosis"]["final"])
        self.assertEqual(stored["llm_calls"][-1]["usage"], failed.usage)
        self.assertEqual(stored["llm_calls"][-1]["finish_reason"], "length")
        self.assertEqual(stored["llm_calls"][-1]["failure_kind"], "incomplete")
        attempts = [row["payload"] for row in stored["board"]["diagnostic"] if row["kind"] == "attempt"]
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

    def production(self, actions, *, bound=100):
        transport = ProductionFakeTransport(actions)
        profile = fixture_profile(context_limit=2048, output_limit=512, timeout_seconds=300,
            capabilities=ProviderCapabilities(json_mode=True, usage=True))
        provider = LocalHTTPProvider(profile, transport=transport, token_counter=lambda request, profile: bound)
        return provider, transport

    def test_actual_production_parser_success_through_full_entry(self):
        provider, transport = self.production([candidate, verification])
        trace = self.run_agent(provider)
        self.assertEqual(trace.diagnosis["status"], "accepted")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual([row["max_tokens"] for row in transport.calls], [512, 512])
        self.assertEqual(trace.llm_calls[0]["provider"], "local_http")
        self.assertEqual(trace.llm_calls[0]["usage_kind"], "measured")
        self.assertEqual(restore_scope(trace.diagnosis["scope"]).source[0].body, SOURCE)

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
                raw = [row["payload"]["raw_output"] for row in stored["board"]["diagnostic"] if row["kind"] == "attempt"]
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
