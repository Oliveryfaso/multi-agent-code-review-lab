"""Scripted providers exercise the real solver and orchestrator without native dispatch."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.agents.final_reviewer import FinalReviewAgent
from macr.agents.orchestrator import Orchestrator
from macr.agents.solver import SolverAgent
from macr.memory.evidence_store import EvidenceStore
from macr.memory.trace_store import TraceStore
from macr.providers.base import ModelResponse, ProviderError
from macr.schemas import AgentAnswer, Evidence, Plan, PlanStep


FIELDS = ("file", "line_start", "line_end", "symbol", "source_tool")


class ScriptedProvider:
    name = "scripted_offline"
    model = "scripted_fixture"

    def __init__(self, actions):
        self.actions = list(actions)
        self.requests = []

    def complete(self, request):
        self.requests.append(deepcopy(request))
        if not self.actions:
            raise AssertionError("solver exceeded the frozen request count")
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action(request)


def response(request, *, citations=None, repeat=False):
    context = json.loads(request.messages[1]["content"])
    if citations is None:
        citations, seen = [], set()
        for item in context["evidence"]:
            position = (item["file"], item["line_start"], item["line_end"])
            if position not in seen:
                citations.append({field: item[field] for field in FIELDS})
                seen.add(position)
            if len(citations) == 3:
                break
    if repeat:
        citations = [item for item in citations for _ in range(3)]
    return ModelResponse(json.dumps({
        "answer": "Retrieved source supports the review; inspect the selected locations.",
        "evidence": citations, "next_steps": ["inspect the selected source"],
    }), provider="scripted_offline", model="scripted_fixture")


def foreign_response(request):
    return response(request, citations=[{
        "file": "foreign.py", "line_start": 1, "line_end": 2,
        "symbol": None, "source_tool": "parse_ast",
    }])


class AgentEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("agent integration must remain offline"))
            guard.start()
            self.addCleanup(guard.stop)
        self.plan = Plan("bug_investigation", "high", [
            PlanStep("find source", "text_search"),
            PlanStep("map source", "ast"),
            PlanStep("expand source", "symbol_graph"),
        ], search_terms=["process_payment"])
        self.evidence = [
            Evidence(f"source{index}.py", index + 1, index + 2, "authoritative source",
                     symbol=f"source{index}", source_tool="parse_ast", snippet="verified snippet")
            for index in range(3)
        ]

    def run_agent(self, actions, *, generate_patch=False, missing=False):
        files = {"backend/payments.py": (
            "def process_payment(payload):\n    return validate_payment(payload)\n"
            "def validate_payment(payload):\n    return bool(payload)\n"
            "def process_payment_handler(payload):\n    return process_payment(payload)\n"
        )} if not missing else {"unrelated.py": "VALUE = 1\n"}
        repo = write_files(fixture_root(), files)
        provider = ScriptedProvider(actions)
        orchestrator = Orchestrator(trace_store=TraceStore(fixture_root() / "traces"), provider=provider)
        with patch.object(orchestrator.planner, "plan", return_value=self.plan):
            trace = orchestrator.run_patch(repo, "process_payment") if generate_patch else orchestrator.run(repo, "process_payment")
        return trace, provider

    def test_real_entry_binds_only_explicit_citations_and_deduplicates(self):
        trace, provider = self.run_agent([lambda request: response(request, repeat=True)])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].response_schema, SolverAgent.OUTPUT_SCHEMA)
        self.assertEqual(len(trace.answer.evidence), 3)
        self.assertAlmostEqual(trace.answer.confidence, .6)
        authority = trace.board["evidence"][0]["payload"]["items"]
        self.assertGreater(len(authority), len(trace.answer.evidence))
        for item in trace.answer.evidence:
            self.assertTrue(any(all(getattr(item, field) == source[field] for field in FIELDS) for source in authority))
        self.assertTrue(trace.metrics["final_review"]["ok"])
        self.assertTrue(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertEqual(trace.metrics["solver_execution"]["attempted_requests"], 1)

    def test_foreign_reference_repair_succeeds_once_with_validation_feedback(self):
        trace, provider = self.run_agent([foreign_response, response])
        self.assertEqual(len(provider.requests), 2)
        feedback = json.loads(provider.requests[1].messages[1]["content"])["validation_feedback"]
        self.assertTrue(any("solver_citation_outside_context" in issue for issue in feedback))
        self.assertEqual(trace.metrics["solver_execution"]["attempts"][0]["status"], "failed")
        self.assertEqual(trace.board["recovery"][0]["payload"]["action"], "model_repair")
        self.assertTrue(trace.metrics["final_review"]["ok"])
        self.assertEqual(trace.state_timeline[-1].status, "completed")
        self.assertIn("foreign.py", trace.board["solver_attempts"][0]["payload"]["raw_output"])

    def test_foreign_reference_exhaustion_is_failed_without_fallback(self):
        trace, provider = self.run_agent([foreign_response, foreign_response])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(trace.llm_calls), 2)
        self.assertEqual(trace.answer.status, "failed")
        self.assertEqual(trace.answer.error_type, "solver_citation_outside_context")
        self.assertEqual(trace.answer.evidence, [])
        self.assertEqual(trace.answer.confidence, 0.0)
        self.assertEqual(trace.state_timeline[-1].status, "failed")
        self.assertFalse(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertTrue(trace.metrics["final_review"]["human_review_required"])

    def test_empty_explicit_citations_never_borrow_retrieved_evidence(self):
        empty = lambda request: response(request, citations=[])
        trace, provider = self.run_agent([empty, empty])
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(trace.board["evidence"][0]["payload"]["items"])
        self.assertEqual(trace.answer.evidence, [])
        self.assertIn("evidence too thin: 0", trace.metrics["final_review"]["issues"])
        self.assertEqual(trace.state_timeline[-1].status, "needs_review")

    def test_transient_provider_failure_retries_and_preserves_failed_attempt(self):
        trace, provider = self.run_agent([ProviderError("model_timeout"), response])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(trace.llm_calls), 2)
        self.assertEqual(trace.llm_calls[0]["error_type"], "model_timeout")
        self.assertEqual(trace.llm_calls[0]["usage_kind"], "unknown")
        self.assertIsNone(trace.llm_calls[0]["cost"]["estimated_cost_usd"])
        self.assertEqual(trace.board["recovery"][0]["payload"]["action"], "model_retry")
        self.assertTrue(trace.metrics["final_review"]["ok"])
        self.assertEqual(trace.metrics["solver_execution"]["attempted_requests"], 2)

    def test_transient_failure_exhaustion_remains_technical_failure(self):
        trace, provider = self.run_agent([ProviderError("model_busy"), ProviderError("model_busy")])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.answer.failure_kind, "transient")
        self.assertEqual(trace.state_timeline[-1].status, "failed")
        self.assertFalse(trace.metrics["final_review"]["retry_recommended"])

    def test_permanent_provider_failure_is_not_retried_or_disguised(self):
        trace, provider = self.run_agent([ProviderError("remote_disabled")])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(trace.answer.failure_kind, "provider_error")
        self.assertEqual(trace.metrics["solver_execution"]["retry_count"], 0)
        self.assertFalse(trace.metrics["final_review"]["ok"])
        self.assertTrue(trace.metrics["final_review"]["human_review_required"])
        self.assertEqual(trace.state_timeline[-1].status, "failed")

    def test_missing_context_uses_no_provider_requests(self):
        trace, provider = self.run_agent([], missing=True)
        self.assertEqual(provider.requests, [])
        self.assertEqual(trace.llm_calls, [])
        self.assertEqual(trace.answer.status, "incomplete")
        self.assertEqual(trace.answer.failure_kind, "missing_context")
        self.assertEqual(trace.metrics["solver_execution"]["attempted_requests"], 0)
        self.assertEqual(trace.state_timeline[-1].status, "needs_review")

    def test_failed_solver_prevents_patch_model_and_verification(self):
        trace, provider = self.run_agent([foreign_response, foreign_response], generate_patch=True)
        self.assertEqual(len(provider.requests), 2)
        self.assertIsNone(trace.patch)
        self.assertFalse(any(call["tool"] == "verify_patch" for call in trace.tool_calls))
        self.assertTrue(any(state.name == "patching" and state.status == "skipped" for state in trace.state_timeline))
        self.assertEqual(trace.state_timeline[-1].status, "failed")

    def test_three_tool_citations_at_one_position_do_not_increase_support(self):
        evidence = [replace(self.evidence[0], source_tool=tool) for tool in ("parse_ast", "code_graph", "symbol_graph")]
        provider = ScriptedProvider([lambda request: response(request, citations=[
            {field: getattr(item, field) for field in FIELDS} for item in evidence
        ])])
        answer, _ = SolverAgent(provider).solve("process_payment", self.plan, evidence)
        single, _ = SolverAgent().solve("process_payment", self.plan, evidence[:1])
        report = FinalReviewAgent().review("query", self.plan, answer, evidence, [])
        self.assertEqual(answer.confidence, single.confidence)
        self.assertEqual(len(answer.evidence), 3)
        self.assertIn("evidence too thin: 1", report.issues)

    def test_every_raw_reference_is_validated_before_deduplication(self):
        def invalid(request):
            citation = {field: getattr(self.evidence[0], field) for field in FIELDS}
            bad = {**citation, "line_end": citation["line_end"] + 1}
            return response(request, citations=[citation, citation, bad])
        answer, model_response = SolverAgent(ScriptedProvider([invalid])).solve("query", self.plan, self.evidence)
        self.assertEqual(answer.status, "failed")
        self.assertEqual(answer.error_type, "solver_citation_outside_context")
        self.assertIsNotNone(model_response)

    def test_compaction_keeps_distinct_line_end(self):
        compacted = SolverAgent()._compact_evidence([self.evidence[0], replace(self.evidence[0], line_end=9)])
        self.assertEqual(len(compacted), 2)

    def test_retrieved_but_unseen_reference_is_not_allowed(self):
        evidence = [replace(self.evidence[0], file=f"source{index:02}.py") for index in range(9)]
        unseen = {field: getattr(evidence[-1], field) for field in FIELDS}
        answer, _ = SolverAgent(ScriptedProvider([lambda request: response(request, citations=[unseen])])).solve("query", self.plan, evidence)
        self.assertEqual(answer.error_type, "solver_citation_outside_context")

    def test_empty_and_truncated_model_output_never_become_fallback_success(self):
        for model_response in (ModelResponse(""), ModelResponse("{}", finish_reason="length")):
            with self.subTest(content=model_response.content):
                answer, recorded = SolverAgent(ScriptedProvider([lambda request: model_response])).solve("query", self.plan, self.evidence)
                self.assertEqual(answer.status, "failed")
                self.assertEqual(answer.evidence, [])
                self.assertIs(recorded, model_response)

    def test_json_duplicate_keys_and_bool_line_numbers_are_invalid(self):
        duplicate = lambda request: ModelResponse('{"answer":"first","answer":"last","evidence":[],"next_steps":[]}')
        boolean = lambda request: response(request, citations=[{
            **{field: getattr(self.evidence[0], field) for field in FIELDS}, "line_start": True,
        }])
        for action, code in ((duplicate, "solver_duplicate_json_key"), (boolean, "solver_citation_invalid")):
            with self.subTest(code=code):
                answer, _ = SolverAgent(ScriptedProvider([action])).solve("query", self.plan, self.evidence)
                self.assertEqual(answer.error_type, code)

    def test_unexpected_provider_programming_error_surfaces(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected adapter bug"):
            SolverAgent(ScriptedProvider([RuntimeError("unexpected adapter bug")])).solve("query", self.plan, self.evidence)

    def test_invalid_unicode_is_repaired_once_and_rejected_output_is_saved(self):
        invalid = lambda request: ModelResponse(json.dumps({
            "answer": "\ud800", "evidence": [], "next_steps": [],
        }))
        trace, provider = self.run_agent([invalid, response])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.metrics["solver_execution"]["attempts"][0]["error_type"], "solver_output_invalid")
        self.assertTrue(trace.metrics["final_review"]["ok"])

    def test_raw_invalid_unicode_failure_is_persisted_as_reversible_json_escape(self):
        invalid = lambda request: ModelResponse("\ud800")
        trace, provider = self.run_agent([invalid, invalid])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.answer.status, "failed")
        attempt = trace.board["solver_attempts"][0]["payload"]
        self.assertEqual(attempt["raw_output_encoding"], "json_escaped")
        self.assertEqual(json.loads(attempt["raw_output"]), "\ud800")

    def test_invalid_unicode_in_next_steps_is_not_accepted(self):
        invalid = lambda request: ModelResponse(json.dumps({
            "answer": "source review", "evidence": [], "next_steps": ["\ud800"],
        }))
        answer, _ = SolverAgent(ScriptedProvider([invalid])).solve("query", self.plan, self.evidence)
        self.assertEqual(answer.error_type, "solver_output_invalid")

    def test_invalid_finish_reason_type_is_provider_contract_failure(self):
        invalid = lambda request: ModelResponse("{}", finish_reason=[])
        answer, _ = SolverAgent(ScriptedProvider([invalid])).solve("query", self.plan, self.evidence)
        self.assertEqual(answer.error_type, "provider_contract")
        self.assertFalse(answer.retryable)

    def test_excessive_json_nesting_is_model_invalid(self):
        invalid = lambda request: ModelResponse("[" * 1100 + "0" + "]" * 1100)
        answer, _ = SolverAgent(ScriptedProvider([invalid])).solve("query", self.plan, self.evidence)
        self.assertEqual(answer.error_type, "solver_output_invalid")
        self.assertTrue(answer.retryable)

    def test_null_and_empty_symbol_mismatches_are_rejected_in_both_directions(self):
        for authority_symbol, model_symbol in ((None, ""), ("", None)):
            with self.subTest(authority_symbol=authority_symbol):
                source = replace(self.evidence[0], symbol=authority_symbol)
                citation = {**{field: getattr(source, field) for field in FIELDS}, "symbol": model_symbol}
                provider = ScriptedProvider([lambda request: response(request, citations=[citation])])
                answer, _ = SolverAgent(provider).solve("query", self.plan, [source])
                self.assertEqual(answer.error_type, "solver_citation_outside_context")

    def test_matching_null_and_empty_symbols_allow_only_exact_duplicate_refs(self):
        for symbol in (None, ""):
            with self.subTest(symbol=symbol):
                source = replace(self.evidence[0], symbol=symbol)
                citation = {field: getattr(source, field) for field in FIELDS}
                provider = ScriptedProvider([lambda request: response(request, citations=[citation, citation])])
                answer, _ = SolverAgent(provider).solve("query", self.plan, [source])
                self.assertEqual(answer.status, "completed")
                self.assertEqual(answer.evidence, [source])
                self.assertIs(answer.evidence[0], source)

    def test_compaction_keeps_null_and_empty_symbol_distinct(self):
        sources = [replace(self.evidence[0], symbol=value) for value in (None, "")]
        self.assertEqual(SolverAgent()._compact_evidence(sources), sources)
        self.assertEqual(SolverAgent()._evidence_confidence(sources), SolverAgent()._evidence_confidence(sources[:1]))

    def test_store_keeps_null_and_empty_symbols_separate_but_merges_exact_refs(self):
        store = EvidenceStore()
        absent = replace(self.evidence[0], symbol=None, reason="absent symbol", confidence=.4)
        empty = replace(self.evidence[0], symbol="", reason="empty symbol", confidence=.5)
        self.assertTrue(store.add(absent))
        self.assertTrue(store.add(empty))
        self.assertEqual(len(store), 2)
        self.assertEqual([item.reason for item in store.items()], ["absent symbol", "empty symbol"])
        self.assertFalse(store.add(replace(absent, reason="same identity", confidence=.8)))
        self.assertEqual(store.items()[0].confidence, .8)
        self.assertEqual(store.items()[1].confidence, .5)
        self.assertNotIn("same identity", store.items()[1].reason)

    def test_final_review_rejects_three_null_empty_symbol_mismatches(self):
        for authority_symbol, model_symbol in ((None, ""), ("", None)):
            with self.subTest(authority_symbol=authority_symbol):
                sources = [replace(item, symbol=authority_symbol) for item in self.evidence]
                forged = [replace(item, symbol=model_symbol) for item in sources]
                report = FinalReviewAgent().review("query", self.plan,
                    AgentAnswer("review", forged, .8, []), sources, [])
                self.assertFalse(report.ok)
                self.assertIn("3 answer references outside authoritative evidence", report.issues)
                self.assertIn("evidence too thin: 0", report.issues)

    def test_real_entry_repairs_empty_symbol_that_does_not_match_visible_null(self):
        def mismatched(request):
            visible = json.loads(request.messages[1]["content"])["evidence"]
            absent = next(item for item in visible if item["symbol"] is None)
            return response(request, citations=[{**{field: absent[field] for field in FIELDS}, "symbol": ""}])
        trace, provider = self.run_agent([mismatched, response])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.metrics["solver_execution"]["attempts"][0]["error_type"], "solver_citation_outside_context")
        self.assertTrue(trace.metrics["final_review"]["ok"])

    def test_source_tool_null_is_invalid_without_fallback(self):
        citation = {**{field: getattr(self.evidence[0], field) for field in FIELDS}, "source_tool": None}
        provider = ScriptedProvider([lambda request: response(request, citations=[citation])])
        answer, _ = SolverAgent(provider).solve("query", self.plan, self.evidence)
        self.assertEqual(answer.error_type, "solver_citation_invalid")

    def test_provider_selects_snippet_before_clipping_and_deterministic_rank_is_preserved(self):
        locations = [Evidence(f"location{index:02}.py", 1, 5, "AST found relevant function",
                              symbol=f"location{index}", source_tool="parse_ast", confidence=.99)
                     for index in range(20)]
        source = Evidence("access.py", 2, 2, "literal source", source_tool="search_text",
                          snippet="return staff or paid", confidence=.65)
        evidence = [*locations, source]
        deterministic, _ = SolverAgent().solve("query", self.plan, evidence)
        self.assertEqual(deterministic.evidence, locations[:12])
        self.assertTrue(all(not item.snippet for item in deterministic.evidence))
        provider = ScriptedProvider([response])
        answer, _ = SolverAgent(provider).solve("query", self.plan, evidence)
        visible = json.loads(provider.requests[0].messages[1]["content"])["evidence"]
        self.assertEqual(len(visible), 8)
        self.assertEqual(visible[0]["snippet"], "return staff or paid")
        self.assertEqual([item["file"] for item in visible[1:]], [item.file for item in locations[:7]])
        self.assertIs(answer.evidence[0], source)
        self.assertAlmostEqual(answer.confidence, .6)

    def test_real_request_contains_source_return_despite_many_blank_location_facts(self):
        source_text = "".join(f"def process_payment_{index:02}(staff, paid):\n    return staff or paid\n" for index in range(16))
        repo = write_files(fixture_root(), {"access.py": source_text})
        provider = ScriptedProvider([response])
        orchestrator = Orchestrator(trace_store=TraceStore(fixture_root() / "traces"), provider=provider)
        plan = replace(self.plan, search_terms=["process_payment", "staff", "paid"])
        with patch.object(orchestrator.planner, "plan", return_value=plan), \
             patch.object(orchestrator.solver, "solve", wraps=orchestrator.solver.solve) as solve:
            trace = orchestrator.run(repo, "process_payment")
        retrieved = solve.call_args_list[0].args[2]
        self.assertGreaterEqual(sum(not item.snippet.strip() for item in retrieved), 12)
        self.assertTrue(any(item.source_tool == "search_text" and item.snippet == "return staff or paid" for item in retrieved))
        visible = json.loads(provider.requests[0].messages[1]["content"])["evidence"]
        self.assertTrue(any(item["snippet"] == "return staff or paid" for item in visible))
        self.assertEqual(repo.joinpath("access.py").read_text(), source_text)
        self.assertTrue(trace.metrics["final_review"]["ok"])

    def test_real_entry_length_replay_exhaustion_is_failed_even_with_valid_json(self):
        truncated = lambda request: replace(response(request), finish_reason="length")
        trace, provider = self.run_agent([truncated, truncated])
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(trace.answer.error_type, "model_output_incomplete")
        self.assertEqual(trace.answer.status, "failed")
        self.assertFalse(trace.metrics["run_outcome"]["end_to_end_completed"])
        self.assertEqual(trace.state_timeline[-1].status, "failed")
        self.assertEqual([call["finish_reason"] for call in trace.llm_calls], ["length", "length"])


if __name__ == "__main__":
    unittest.main()
