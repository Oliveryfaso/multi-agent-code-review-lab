"""Supervisor timing regressions with no worker, target IO or native metrics."""
import copy
from dataclasses import replace
from subprocess import TimeoutExpired
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from macr.investigation import evidence_budget, evidence_supervisor as supervisor
from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_report import render_json


class Clock:
    def __init__(self):
        self.now = 100.0
        self.interrupt_once = False

    def __call__(self):
        if self.interrupt_once:
            self.interrupt_once = False
            raise KeyboardInterrupt()
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Process:
    def __init__(self, clock, unknown_wait):
        self.pid, self.returncode = 402, None
        self.clock, self.unknown_wait = clock, unknown_wait
        self.stdin = self.stdout = self.stderr = None

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout):
        self.clock.advance(.0001)
        if self.unknown_wait:
            raise TimeoutExpired('synthetic-process', timeout)
        self.returncode = -15
        return self.returncode


def run_supervisor(*, spawn_error=False, workspace_error=False, unknown_wait=False,
                   confirmed_wait=False, metadata_error=False, tiny_report=False,
                   interrupt_after_popen=False):
    clock = Clock()
    process = Process(clock, unknown_wait)
    launches, observations, payloads = [], [], []
    profile = replace(EvidenceProfile(), max_worker_rss_bytes=1,
                      **({'max_report_bytes': 1} if tiny_report else {}))
    request = EvidenceRequest('/synthetic-timing-target', '/synthetic-timing-output',
                              'Show the declaration.', entries=['x.py:f'], profile=profile)

    def workspace(*args, **kwargs):
        if workspace_error:
            raise OSError('synthetic workspace failure')
        return SimpleNamespace(token=str(uuid4()))

    def popen(argv, **kwargs):
        launches.append(kwargs)
        if spawn_error:
            raise OSError('synthetic spawn failure')
        clock.advance(.001)
        begin = clock()
        clock.advance(.0001)
        observations.append((begin, clock()))
        clock.advance(.001)
        clock.interrupt_once = interrupt_after_popen
        return process

    def pressure(monitor):
        monitor['pressure'] = {'available': True, 'raw': 1, 'state': 'normal'}
        monitor['pressure_sample_count'] += 1

    def communicate(owned, data, budget, monitor, storage):
        payloads.append(supervisor.load_json(data.decode(), max_bytes=profile.max_input_bytes,
                                             max_depth=64))
        pressure(monitor)
        clock.advance(.001)
        monitor.update(rss_sample_count=1, rss_peak_bytes=196608)
        if confirmed_wait:
            owned.wait(timeout=1)
            return b''
        budget.observe('max_worker_rss_bytes', 196608, 'worker_memory')
        raise AssertionError('resource guard did not fire')

    query = Mock(return_value=SimpleNamespace(implementation='synthetic_monotonic()',
                                             monotonic=True, adjustable=False),
                 side_effect=OSError('synthetic metadata failure') if metadata_error else None)
    clock_api = SimpleNamespace(monotonic=clock, get_clock_info=query)
    process_api = SimpleNamespace(Popen=popen, PIPE=-1, TimeoutExpired=TimeoutExpired)
    with patch.object(supervisor, 'time', clock_api), \
         patch.object(evidence_budget, 'time', clock_api), \
         patch.object(supervisor, 'os', SimpleNamespace(getpid=lambda: 401)), \
         patch.object(supervisor, 'subprocess', process_api), \
         patch.object(supervisor, 'EvidenceWorkspace', workspace), \
         patch.object(supervisor, '_observe_pressure', pressure), \
         patch.object(supervisor, '_communicate', communicate):
        report = supervisor.collect_evidence(request)
    return SimpleNamespace(report=report, final=report.to_dict(), profile=profile,
                           launches=launches, observations=observations, payloads=payloads,
                           metadata_calls=query.call_count)


class WorkerTimingTests(unittest.TestCase):
    def test_pre_call_bound_contains_observation_before_popen_returns(self):
        result = run_supervisor()
        closure = result.final['run']['closure']
        begin, end = result.observations[0]
        self.assertLessEqual(closure['worker_spawn_call_before_monotonic'], begin)
        self.assertLess(end, closure['worker_started_monotonic'])
        self.assertLessEqual(closure['worker_started_monotonic'], closure['worker_closed_monotonic'])
        timing = closure['timing']
        self.assertEqual(timing['version'], 2)
        self.assertEqual(timing['invocation'], {'supervisor_pid': 401,
                                              'out': result.final['request']['out']})
        self.assertEqual(timing['events'], {
            'worker_spawn_call_before_monotonic': 'before_Popen',
            'worker_started_monotonic': 'parent_after_Popen_return',
            'worker_closed_monotonic': 'parent_after_confirmed_wait'})
        self.assertEqual(timing['worker_business_start'], 'not_observed')
        self.assertEqual(timing['OS_creation'], 'bounded_by_Popen_call')

    def test_clock_metadata_and_serialization_use_bounded_existing_report(self):
        result = run_supervisor()
        timing = result.final['run']['closure']['timing']
        self.assertEqual(result.metadata_calls, 1)
        self.assertEqual(timing['clock'], {
            'api': 'time.monotonic', 'implementation': 'synthetic_monotonic()',
            'unit': 'seconds', 'monotonic': True, 'adjustable': False,
            'python_implementation': sys.implementation.name,
            'python_version': list(sys.version_info[:3]), 'platform': sys.platform})
        projection = copy.deepcopy(result.report)
        projection.run['closure'].pop('timing')
        projection.run['closure'].pop('worker_spawn_call_before_monotonic')
        encoded = render_json(result.report).encode()
        self.assertGreater(len(encoded), len(render_json(projection).encode()))
        self.assertLess(len(encoded), result.profile.max_report_bytes)

    def test_analysis_budget_launch_flags_and_rss_failure_are_preserved(self):
        result = run_supervisor()
        closure = result.final['run']['closure']
        self.assertEqual(closure['timing']['analysis_budget_started_monotonic'], 100.0)
        self.assertEqual(result.payloads[0]['started'], 100.0)
        self.assertEqual(result.payloads[0]['deadline'], 220.0)
        self.assertEqual((result.profile.active_seconds, result.profile.cleanup_seconds), (120.0, 5.0))
        self.assertEqual(result.final['errors'][0]['stage'], 'worker_memory')
        self.assertEqual(closure['steps'], ['term', 'wait'])
        self.assertTrue(closure['exit_confirmed'])
        self.assertEqual(closure['returncode'], -15)
        self.assertEqual((closure['starts'], closure['restarts']), (1, 0))
        self.assertEqual(len(result.launches), 1)
        self.assertEqual(result.launches[0]['env'], {})
        self.assertFalse(result.launches[0]['shell'])
        self.assertTrue(result.launches[0]['close_fds'])
        self.assertEqual(result.final['usage']['model_calls'], 0)
        self.assertFalse(result.final['capabilities']['repository_code_executed'])

    def test_spawn_failure_has_only_attempt_lower_bound(self):
        closure = run_supervisor(spawn_error=True).final['run']['closure']
        self.assertIsNotNone(closure['worker_spawn_call_before_monotonic'])
        self.assertIsNone(closure['worker_started_monotonic'])
        self.assertIsNone(closure['worker_closed_monotonic'])
        self.assertEqual(closure['starts'], 0)

    def test_pre_spawn_failure_has_no_creation_bound(self):
        result = run_supervisor(workspace_error=True)
        self.assertIsNone(result.final['run']['closure']['worker_spawn_call_before_monotonic'])
        self.assertIsNone(result.final['run']['closure']['worker_started_monotonic'])
        self.assertEqual(result.launches, [])

    def test_unknown_wait_does_not_invent_confirmed_upper_bound(self):
        closure = run_supervisor(unknown_wait=True).final['run']['closure']
        self.assertFalse(closure['exit_confirmed'])
        self.assertIsNone(closure['worker_closed_monotonic'])
        self.assertIn('unknown', closure['steps'])
        self.assertEqual(closure['timing']['version'], 2)

    def test_confirmed_wait_path_retains_timing(self):
        closure = run_supervisor(confirmed_wait=True).final['run']['closure']
        self.assertTrue(closure['exit_confirmed'])
        self.assertEqual(closure['steps'], ['wait'])
        self.assertLessEqual(closure['worker_spawn_call_before_monotonic'], closure['worker_started_monotonic'])
        self.assertLessEqual(closure['worker_started_monotonic'], closure['worker_closed_monotonic'])

    def test_metadata_failure_preserves_resource_guard(self):
        result = run_supervisor(metadata_error=True)
        self.assertIsNone(result.final['run']['closure']['timing']['clock'])
        self.assertEqual(result.final['errors'][0]['code'], 'scope_limit')
        self.assertEqual(result.final['errors'][0]['stage'], 'worker_memory')
        self.assertTrue(result.final['run']['closure']['exit_confirmed'])

    def test_interrupt_after_popen_still_closes_owned_process(self):
        result = run_supervisor(interrupt_after_popen=True)
        closure = result.final['run']['closure']
        self.assertTrue(closure['cleanup_attempted'])
        self.assertTrue(closure['exit_confirmed'])
        self.assertEqual(closure['steps'], ['term', 'wait'])
        self.assertIsNone(closure['worker_started_monotonic'])
        self.assertEqual(result.final['status'], 'cancelled')

    def test_tiny_report_cap_keeps_typed_failure_without_enlarging_cap(self):
        result = run_supervisor(tiny_report=True)
        self.assertIn('output_limit_unrepresentable', [e['code'] for e in result.final['errors']])
        self.assertIsNone(result.final['request'])
        self.assertEqual(result.profile.max_report_bytes, 1)
        self.assertEqual(result.final['run']['publication'], 'uncommitted')


if __name__ == '__main__':
    unittest.main()
