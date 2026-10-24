"""Synthetic persistence failures; no retained trace or checkpoint is opened."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.investigation.validation import ContractError
from macr.memory.trace_store import TraceStore
from macr.schemas import Trace


class TraceSummaryTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.store = TraceStore(self.root / 'traces')

    def sensitive_trace(self):
        from macr.schemas import AgentAnswer, Evidence, PatchArtifact, Plan, PlanStep, RunState
        # Arbitrary non-keyword text; every value is authored for this fixture.
        self.canary = 'cormorant_82_copper_veil'
        secret = self.canary
        trace = Trace(secret, '/synthetic/private/' + secret)
        trace.plan = Plan(secret, secret, [PlanStep(secret, secret, secret)])
        trace.answer = AgentAnswer(secret, [Evidence('/synthetic/' + secret, 1, 2, secret, secret)],
                                   0.0, [secret])
        trace.patch = PatchArtifact(secret, secret, ['/synthetic/' + secret],
                                    verification={'message': secret})
        trace.state_timeline = [RunState('completed', 'completed', secret)]
        trace.tool_calls = [{'tool': 'search_text', 'ok': False, 'latency_ms': 7,
            'data': {'nested': [[secret]]}, 'message': secret, 'summary': secret,
            'router': {'inputs': {'query': secret}, 'reason': secret}}]
        trace.llm_calls = [{'role': 'diagnostic_candidate', 'provider': secret,
            'model': secret, 'usage_kind': 'measured',
            'usage': {'input_tokens': 100, 'output_tokens': 40, secret: secret},
            'latency_ms': 3, 'finish_reason': 'stop', 'raw_output': secret}]
        trace.diagnosis = {'status': 'accepted', 'candidate': {'cause': secret},
            'final': {'cause': secret}, 'candidate_parsed': True,
            'verification_parsed': True, 'semantic_proof': False, 'unknown': secret}
        trace.diagnosis['scope'] = {'attempt_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
                                     'source': [[secret]]}
        trace.board = {'diagnostic': [[secret]]}
        trace.metrics = {'unknown': secret, 'final_review': {'ok': False,
            'human_review_required': True, 'issues': [secret]}}
        return trace

    def assert_safe(self, path):
        data = path.read_bytes()
        self.assertNotIn(self.canary.encode(), data)
        value = json.loads(data)
        self.assertEqual((value['schema'], value['version']), ('macr.trace.summary', 1))
        self.assertNotIn('query', value)
        self.assertNotIn('repo_path', value)
        self.assertNotIn('board', value)
        return value

    def test_default_task_and_latest_are_versioned_safe_summaries(self):
        trace = self.sensitive_trace()
        path = self.store.save(trace)
        value = self.assert_safe(path)
        self.assertEqual(path.read_bytes(), (self.store.root / 'latest.json').read_bytes())
        self.assertEqual(value['task_id'], trace.task_id)
        self.assertEqual(value['counts']['tool_calls'], 1)
        self.assertEqual(value['tool_calls'][0]['latency_ms'], 7)
        self.assertEqual(value['llm_calls'][0]['usage'], {'input_tokens': 100, 'output_tokens': 40})
        self.assertEqual(value['llm_calls'][0]['usage_kind'], 'measured')
        self.assertTrue(value['diagnosis']['candidate_present'])
        self.assertEqual(value['diagnosis']['attempt_id'], 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.assertTrue(value['metrics']['final_review']['human_review_required'])
        self.assertNotIn('issues', value['metrics']['final_review'])
        self.assertEqual(trace.query, self.canary)

    def test_fsync_failure_tmp_contains_only_safe_summary(self):
        trace = self.sensitive_trace()
        with patch('os.fsync', side_effect=OSError('synthetic flush failure')):
            with self.assertRaises(OSError):
                self.store.save(trace)
        files = list(self.store.root.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, '.tmp')
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assert_safe(files[0])

    def test_latest_failure_retains_old_latest_and_safe_new_task_and_tmp(self):
        trace = self.sensitive_trace()
        previous = Trace('old synthetic text', 'synthetic-source')
        self.store.save(previous)
        before = (self.store.root / 'latest.json').read_bytes()
        import os
        replace = os.replace
        def fail_latest(source, target):
            if Path(target).name == 'latest.json':
                raise OSError('synthetic replace failure')
            return replace(source, target)
        with patch('os.replace', side_effect=fail_latest), self.assertRaises(OSError):
            self.store.save(trace)
        self.assertEqual((self.store.root / 'latest.json').read_bytes(), before)
        self.assert_safe(self.store.root / (trace.task_id + '.json'))
        temporary = list(self.store.root.glob('*.tmp'))
        self.assertEqual(len(temporary), 1)
        self.assert_safe(temporary[0])

    def test_omitted_content_is_not_traversed_or_copied(self):
        class Opaque:
            def __deepcopy__(self, memo):
                raise AssertionError('raw content must never be copied')
        trace = self.sensitive_trace()
        cyclic = [Opaque(), self.canary, 'x' * (2 * 1024 * 1024)]
        cyclic.append(cyclic)
        trace.tool_calls[0]['data'] = cyclic
        trace.metrics['unknown'] = cyclic
        self.assert_safe(self.store.save(trace))

    def test_selected_rows_are_capped_and_truncation_is_explicit(self):
        trace = self.sensitive_trace()
        trace.tool_calls *= 1000
        value = self.assert_safe(self.store.save(trace))
        self.assertEqual(value['counts']['tool_calls'], 1000)
        self.assertEqual(len(value['tool_calls']), 64)
        self.assertTrue(value['truncation']['applied'])
        self.assertEqual(value['truncation']['tool_calls_omitted'], 936)
        self.assertLess(len(json.dumps(value).encode()), 1048576)

    def test_unknown_control_text_is_replaced_not_partially_retained(self):
        trace = self.sensitive_trace()
        trace.tool_calls[0].update(tool=self.canary, error_type=self.canary)
        trace.diagnosis.update(status=self.canary, protocol=self.canary)
        value = self.assert_safe(self.store.save(trace))
        self.assertEqual(value['tool_calls'][0]['tool'], 'unknown')
        self.assertEqual(value['diagnosis']['status'], 'unknown')

    def test_existing_patcher_role_is_preserved(self):
        trace = self.sensitive_trace()
        trace.llm_calls[0]['role'] = 'patcher'
        value = self.assert_safe(self.store.save(trace))
        self.assertEqual(value['llm_calls'][0]['role'], 'patcher')

    def test_summary_readers_never_grant_verification_or_recovery(self):
        from macr.memory.run_store import RunStore
        trace = self.sensitive_trace()
        path = self.store.save(trace)
        typed = RunStore(self.root / 'runs')
        self.addCleanup(typed.close)
        before = {p.name: p.read_bytes() for p in typed.root.iterdir()}
        for reader in (self.store, typed):
            with self.subTest(reader=type(reader).__name__):
                value = reader.read_legacy(path)
                self.assertEqual(set(value), {'payload', 'validation_status'})
                self.assertEqual(value['validation_status'], 'legacy_unverified')
                self.assertEqual(value['payload']['schema'], 'macr.trace.summary')
                self.assertEqual(value['payload']['task_id'], trace.task_id)
        self.assertEqual({p.name: p.read_bytes() for p in typed.root.iterdir()}, before)

    def test_second_fsync_failure_preserves_latest_and_leaves_safe_task_and_tmp(self):
        trace = self.sensitive_trace()
        self.store.save(Trace('old synthetic record', 'synthetic-source'))
        before = (self.store.root / 'latest.json').read_bytes()
        import os
        fsync = os.fsync
        calls = []
        def fail_second(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError('synthetic second fsync failure')
            return fsync(descriptor)
        with patch('os.fsync', side_effect=fail_second), self.assertRaises(OSError):
            self.store.save(trace)
        self.assertEqual(len(calls), 2)
        self.assertEqual((self.store.root / 'latest.json').read_bytes(), before)
        task = self.store.root / (trace.task_id + '.json')
        self.assert_safe(task)
        temporary = list(self.store.root.glob('*.tmp'))
        self.assertEqual(len(temporary), 1)
        self.assertEqual(task.read_bytes(), temporary[0].read_bytes())
        self.assertEqual(temporary[0].stat().st_mode & 0o777, 0o600)
        self.assert_safe(temporary[0])

    def test_historical_full_trace_wrappers_remain_unverified(self):
        from macr.memory.run_store import RunStore
        trace = self.sensitive_trace()
        path = self.root / 'historical.json'
        historical = trace.to_dict()
        path.write_text(json.dumps(historical))
        typed = RunStore(self.root / 'runs')
        self.addCleanup(typed.close)
        for reader in (self.store, typed):
            with self.subTest(reader=type(reader).__name__):
                value = reader.read_legacy(path)
                self.assertEqual(set(value), {'payload', 'validation_status'})
                self.assertEqual(value['validation_status'], 'legacy_unverified')
                self.assertEqual(value['payload'], historical)

    def test_selected_nonfinite_latency_fails_before_any_write(self):
        trace = self.sensitive_trace()
        trace.tool_calls[0]['latency_ms'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'trace_value_nonfinite'):
            self.store.save(trace)
        self.assertFalse(list(self.store.root.iterdir()))

    def test_summary_viewer_never_reads_ancillary_raw_content(self):
        from macr.viewer import TraceViewer
        trace = self.sensitive_trace()
        path = self.store.save(trace)
        viewer = TraceViewer(self.root, trace_path=path)
        with patch.object(viewer, '_read_text', side_effect=AssertionError('raw fallback forbidden')):
            page = viewer.render()
        self.assertIn('Content omitted', page)
        self.assertIn('Human review required', page)
        self.assertIn('action="/web-review"', page)
        self.assertIn('data-review-mode', page)
        self.assertIn("const syncMode", page)
        self.assertNotIn(self.canary, page)

    def test_summary_viewer_keeps_current_job_status_without_loading_raw_files(self):
        from macr.viewer import TraceViewer, WebReviewJob
        trace = self.sensitive_trace()
        viewer = TraceViewer(self.root, trace_path=self.store.save(trace))
        job = WebReviewJob('synthetic-job', 'ask', 'running', 'Synthetic job running', 0, 0)
        with patch.object(viewer, '_read_text', side_effect=AssertionError('raw fallback forbidden')):
            page = viewer.render(status='Synthetic queued status', job=job)
        self.assertIn('Synthetic queued status', page)
        self.assertIn('Synthetic job running', page)
        self.assertIn("data-job-id='synthetic-job'", page)
        self.assertIn('fetch(`/jobs/${jobId}.json`', page)

    def test_unknown_summary_version_has_no_legacy_fallback(self):
        from macr.viewer import TraceViewer
        path = self.root / 'future.json'
        path.write_text(json.dumps({'schema': 'macr.trace.summary', 'version': 99,
                                   'query': 'synthetic future raw text'}))
        viewer = TraceViewer(self.root, trace_path=path)
        with patch.object(viewer, '_read_text', side_effect=AssertionError('raw fallback forbidden')):
            page = viewer.render()
        self.assertIn('Unsupported trace summary version', page)
        self.assertNotIn('synthetic future raw text', page)


class TraceStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root() / 'traces'
        self.store = TraceStore(self.root)

    def trace(self, query):
        return Trace(query, 'synthetic-source')

    def test_saved_trace_and_latest_share_safe_bytes_and_private_permissions(self):
        trace = self.trace('synthetic question')
        path = self.store.save(trace)
        self.assertEqual(path, self.root / (trace.task_id + '.json'))
        self.assertEqual(path.read_bytes(), (self.root / 'latest.json').read_bytes())
        saved = json.loads(path.read_text())
        self.assertEqual((saved['schema'], saved['version']), ('macr.trace.summary', 1))
        self.assertEqual(saved['task_id'], trace.task_id)
        self.assertNotIn('query', saved)
        for target in (path, self.root / 'latest.json'):
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_failed_latest_replace_keeps_previous_complete_latest(self):
        previous = self.trace('previous synthetic question')
        self.store.save(previous)
        before = (self.root / 'latest.json').read_bytes()
        next_trace = self.trace('next synthetic question')
        import os
        replace = os.replace

        def fail_latest(source, target):
            if Path(target).name == 'latest.json':
                raise OSError('synthetic latest replacement failure')
            return replace(source, target)

        with patch('os.replace', side_effect=fail_latest):
            with self.assertRaises(OSError):
                self.store.save(next_trace)
        self.assertEqual((self.root / 'latest.json').read_bytes(), before)
        self.assertEqual(json.loads((self.root / (next_trace.task_id + '.json')).read_text())['task_id'],
                         next_trace.task_id)
        self.assertTrue(list(self.root.glob('*.tmp')), 'failed write evidence must remain')

    def test_failed_flush_keeps_existing_trace_and_latest(self):
        trace = self.trace('original synthetic question')
        path = self.store.save(trace)
        before = path.read_bytes()
        trace.query = 'replacement synthetic question'
        with patch('os.fsync', side_effect=OSError('synthetic flush failure')):
            with self.assertRaises(OSError):
                self.store.save(trace)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((self.root / 'latest.json').read_bytes(), before)

    def test_invalid_identifier_and_nonfinite_json_fail_before_write(self):
        trace = self.trace('synthetic question')
        trace.task_id = '../escape'
        with self.assertRaises(ContractError):
            self.store.save(trace)
        trace.task_id = 'valid-id'
        trace.tool_calls = [{'latency_ms': float('nan')}]
        with self.assertRaises(ValueError):
            self.store.save(trace)
        self.assertFalse(list(self.root.iterdir()))
        self.assertFalse((self.root.parent / 'escape.json').exists())

    def test_large_tool_value_is_omitted_before_copying(self):
        trace = self.trace('synthetic bounded record')
        trace.tool_calls = [{'data': 'X' * (2 * 1024 * 1024)}]
        saved = json.loads(self.store.save(trace).read_text())
        self.assertNotIn('data', saved['tool_calls'][0])

    def test_encoded_byte_limit_preserves_previous_complete_files(self):
        previous = self.trace('previous synthetic record')
        path = self.store.save(previous)
        before = path.read_bytes()
        trace = self.trace('synthetic escaping expansion')
        # Fault-inject the projection boundary to independently exercise the
        # actual encoder budget; no alternative raw persistence path is added.
        with patch('macr.memory.trace_encoding.project_trace_summary',
                   return_value={'text': chr(0) * 200000}):
            with self.assertRaisesRegex(ValueError, 'trace_limit_bytes'):
                self.store.save(trace)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((self.root / 'latest.json').read_bytes(), before)
        self.assertFalse((self.root / (trace.task_id + '.json')).exists())
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_encoder_excessive_nesting_is_rejected_without_recursion_failure(self):
        from macr.memory.trace_encoding import _encode_payload
        payload = {}
        nested = payload
        for unused in range(80):
            child = {}; nested['next'] = child; nested = child
        with self.assertRaises(ValueError):
            _encode_payload(payload)

    def test_broad_collection_is_rejected_before_writes(self):
        from macr.memory.trace_encoding import _encode_payload
        with self.assertRaises(ValueError):
            _encode_payload({'items': [0] * 40000})
        self.assertFalse(list(self.root.iterdir()))

    def test_cycle_has_a_controlled_failure_and_does_not_mutate_input(self):
        from macr.memory.trace_encoding import _encode_payload
        payload = {}
        payload['cycle'] = payload
        with self.assertRaises(ValueError):
            _encode_payload(payload)
        self.assertIs(payload['cycle'], payload)
        self.assertFalse(list(self.root.iterdir()))

    def test_unsupported_value_is_rejected_before_deepcopy_hook(self):
        class OpaqueValue:
            def __deepcopy__(self, memo):
                raise AssertionError('must not execute arbitrary deepcopy hooks')
        from macr.memory.trace_encoding import _encode_payload
        with self.assertRaises(ValueError):
            _encode_payload({'object': OpaqueValue()})
        self.assertFalse(list(self.root.iterdir()))

    def test_encoder_shared_noncyclic_data_counts_expanded_json_values(self):
        from macr.memory.trace_encoding import _encode_payload
        shared = {'numbers': (1, 2, 3)}
        self.assertEqual(json.loads(_encode_payload({'first': shared, 'second': shared})),
                         {'first': {'numbers': [1, 2, 3]}, 'second': {'numbers': [1, 2, 3]}})

    def test_repeated_aliases_count_each_serialized_occurrence(self):
        from macr.memory.trace_encoding import _encode_payload
        shared = [0] * 1024
        with self.assertRaises(ValueError):
            _encode_payload({'repeated': [shared] * 40})
        self.assertFalse(list(self.root.iterdir()))

    def test_extreme_integer_is_rejected_before_writes(self):
        from macr.memory.trace_encoding import _encode_payload
        with self.assertRaises(ValueError):
            _encode_payload({'integer': 1 << 5000})
        self.assertFalse(list(self.root.iterdir()))

    def test_one_mib_encoded_boundary_is_inclusive(self):
        from macr.memory.trace_encoding import _encode_payload
        payload = {'padding': ''}
        overhead = len(json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'))
        payload['padding'] = 'a' * (1048576 - overhead)
        self.assertEqual(len(_encode_payload(payload)), 1048576)
        payload['padding'] += 'b'
        with self.assertRaises(ValueError):
            _encode_payload(payload)

    def test_unknown_metrics_and_primitive_mapping_keys_are_discarded(self):
        trace = self.trace('synthetic mapping keys')
        trace.metrics['keys'] = {2: 'two', False: 'false', None: 'none', 1.5: 'float'}
        saved = json.loads(self.store.save(trace).read_text())
        self.assertNotIn('keys', saved['metrics'])

    def test_old_synthetic_full_trace_is_still_readable_by_viewer(self):
        from macr.viewer import TraceViewer
        trace = self.trace('synthetic historical question')
        path = self.root / 'historical.json'
        path.write_text(json.dumps(trace.to_dict()))
        self.assertIn('synthetic historical question', TraceViewer(self.root, trace_path=path).render())
