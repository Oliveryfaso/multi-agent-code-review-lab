"""Small boundary checks for the fixed mock check; no native process dispatch."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from debugger_fixtures import fixture_root

import check_mock_recovery as check


class MockRecoveryCheckTests(unittest.TestCase):
    def envelope(self, calls=1):
        return {'mock_only': True, 'production_resume': False,
                'dispatch_permitted': False, 'state_authority': 'langgraph_sqlite',
                'model_calls': 0, 'tool_calls': 0, 'mock_provider_calls_this_call': calls,
                'status': 'completed',
                'executed_mock_steps_this_call': {'evidence': 1, 'report': 1}}

    def test_exhausted_budget_never_starts_a_child(self):
        with patch.object(check.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'wall_seconds_limit'):
                check.run_child(['synthetic'], 0, ())
        spawn.assert_not_called()

    def test_timeout_kills_only_directly_created_child(self):
        process = Mock(returncode=-9)
        process.poll.side_effect = [None, -9]
        clock = iter((0, 31, 31, 31))
        with patch.object(check.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(check.selectors, 'DefaultSelector') as selector, \
                patch.object(check.time, 'monotonic', side_effect=lambda: next(clock, 31)):
            selector.return_value.get_map.return_value = {'pipe': True}
            result = check.run_child(['synthetic'], 120, ())
        self.assertEqual(result['bound_error'], 'child_timeout')
        process.kill.assert_called_once_with()
        self.assertEqual(result['output_bytes'], 0)
        self.assertEqual(spawn.call_args.kwargs['env'], check.ENVIRONMENT)
        self.assertIs(spawn.call_args.kwargs['stdin'], check.subprocess.DEVNULL)
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_streaming_output_limit_clips_and_stops_own_child(self):
        process = Mock(returncode=-9)
        process.poll.side_effect = [None, -9]
        key = SimpleNamespace(fileobj=process.stdout, data='stdout')
        with patch.object(check, 'OUTPUT_BYTES', 4), \
                patch.object(check.subprocess, 'Popen', return_value=process), \
                patch.object(check.selectors, 'DefaultSelector') as selector, \
                patch.object(check.os, 'read', return_value=b'abcdefgh'), \
                patch.object(check.time, 'monotonic', return_value=0):
            selector.return_value.get_map.return_value = {'pipe': True}
            selector.return_value.select.return_value = [(key, 1)]
            result = check.run_child(['synthetic'], 120, ())
        self.assertEqual(result['bound_error'], 'child_output_limit')
        self.assertEqual(result['stdout'], 'abcd')
        self.assertEqual(result['output_bytes'], 4)
        process.kill.assert_called_once_with()

    def test_allocated_limit_refuses_dispatch(self):
        base = fixture_root()
        (base / 'data').write_bytes(b'synthetic')
        with patch.object(check, 'FIXTURE_BYTES', 0), \
                patch.object(check.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'allocated_bytes_limit'):
                check.run_child(['synthetic'], 120, (base,))
        spawn.assert_not_called()

    def test_selector_initialization_failure_reclaims_owned_child(self):
        process = Mock(returncode=-9)
        process.poll.return_value = None
        with patch.object(check.subprocess, 'Popen', return_value=process), \
                patch.object(check.selectors, 'DefaultSelector',
                             side_effect=RuntimeError('selector_failed')):
            result = check.run_child(['synthetic'], 120, ())
        self.assertEqual(result['bound_error'], 'selector_failed')
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1)
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_fixture_links_count_only_own_allocation_without_following_target(self):
        base = fixture_root()
        measured = base / 'measured'
        measured.mkdir()
        target = base / 'target'
        target.mkdir()
        (target / 'data.bin').write_bytes(b'x' * 65536)
        link = measured / 'link'
        link.symlink_to(target, target_is_directory=True)
        try:
            expected = (measured.stat().st_blocks + link.lstat().st_blocks) * 512
            self.assertEqual(check.fixture_bytes((measured,)), expected)
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_root'):
                check.fixture_bytes((link,))
        finally:
            link.unlink()

    def test_failed_child_stops_batch_without_retry_or_later_cases(self):
        failure = {'stdout': '', 'stderr': '', 'exit_code': -9,
                   'bound_error': 'child_timeout', 'allocated_peak_bytes': 0}
        report = {'cases': [], 'allocated_peak_bytes': 0}
        with patch.object(check, 'run_child', return_value=failure) as child:
            with self.assertRaisesRegex(RuntimeError, 'child_timeout'):
                check.check_cases(report, {}, Path('synthetic'), check.time.monotonic())
        child.assert_called_once()
        self.assertEqual(len(report['cases']), 1)

    def test_wrong_counts_or_real_dispatch_claims_fail_contract(self):
        check.check_mock_case(self.envelope(), check.CASES[0])
        for field, value in (('model_calls', 1), ('tool_calls', 1),
                             ('production_resume', True), ('mock_provider_calls_this_call', 0),
                             ('mock_provider_calls_this_call', True)):
            with self.subTest(field=field, value=value):
                output = self.envelope()
                output[field] = value
                with self.assertRaisesRegex(RuntimeError, 'mock_boundary_or_call_count_mismatch'):
                    check.check_mock_case(output, check.CASES[0])
        output = self.envelope()
        output['executed_mock_steps_this_call'] = {'evidence': 0, 'report': 1}
        with self.assertRaisesRegex(RuntimeError, 'actual_mock_execution_mismatch'):
            check.check_mock_case(output, check.CASES[0])

    def test_unknown_requires_explanation_and_cannot_claim_provider_replay(self):
        output = self.envelope(0) | {'status': 'rejected', 'error': 'outcome_unknown',
                                    'explanation': check.UNKNOWN_EXPLANATION}
        check.check_mock_case(output, check.CASES[-1])
        output['explanation'] = ''
        with self.assertRaisesRegex(RuntimeError, 'unknown_explanation_missing'):
            check.check_mock_case(output, check.CASES[-1])


if __name__ == '__main__':
    unittest.main()
