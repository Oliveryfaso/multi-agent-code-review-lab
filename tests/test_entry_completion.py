"""Owned-child completion contracts using fake wait/clock boundaries only."""
import copy
import errno
import importlib.util
import math
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if importlib.util.find_spec('macr.investigation.entry_completion'):
    from macr.investigation import entry_completion as completion
else:
    completion = None


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(completion, 'entry_completion implementation is missing')
        self.now = 1.0
        self.calls = []
        self.sleeps = []
        self.events = []
        self.action = lambda: (71, 0, self.usage())
        clock = SimpleNamespace(monotonic=self.clock, sleep=self.sleep,
                                get_clock_info=lambda name: SimpleNamespace(
                                    implementation='fake shared monotonic', monotonic=True,
                                    adjustable=False))
        adapter = SimpleNamespace(WNOHANG=os.WNOHANG, wait4=self.wait,
                                  waitstatus_to_exitcode=os.waitstatus_to_exitcode)
        self.enterContext(patch.object(completion, 'time', clock))
        self.enterContext(patch.object(completion, 'os', adapter))

    def usage(self, user=.2, system=.1, rss=4096):
        return SimpleNamespace(ru_utime=user, ru_stime=system, ru_maxrss=rss)

    def clock(self):
        self.events.append('clock')
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def wait(self, pid, flags):
        self.calls.append((pid, flags))
        self.events.append('wait')
        return self.action()

    def observe(self, **kwargs):
        return completion.observe_owned_child(71, invocation_id='new-entry',
                                              deadline=kwargs.pop('deadline', 2.0), **kwargs)

    def qualify(self, record, **kwargs):
        options = dict(expected_pid=71, invocation_id='new-entry',
                       expected_clock=record['clock'], finalization_deadline=2.0)
        options.update(kwargs)
        return completion.qualify_completion(record, **options)

    def test_clock_captured_immediately_before_cpu_access(self):
        owner = self
        class Usage:
            @property
            def ru_utime(self):
                owner.events.append('cpu')
                return .2
            ru_stime = .1
            ru_maxrss = 4096
        self.action = lambda: (71, 0, Usage())
        record = self.observe()
        index = self.events.index('wait')
        self.assertEqual(self.events[index:index + 3], ['wait', 'clock', 'cpu'])
        self.assertEqual(record['wait_returned_monotonic_upper'], 1.0)
        self.assertEqual(record['cpu']['user_seconds'], .2)
        self.assertEqual(self.qualify(record)['status'], 'qualified')

    def test_specific_pid_nonblocking_poll_and_reap(self):
        count = 0
        samples = []
        def action():
            nonlocal count
            count += 1
            return (0, 0, None) if count == 1 else (71, 0, self.usage())
        self.action = action
        record = self.observe(on_poll=lambda pid: samples.append(pid))
        self.assertEqual(self.calls, [(71, os.WNOHANG)] * 2)
        self.assertEqual(self.sleeps, [.05])
        self.assertEqual(record['polls'], 2)
        self.assertTrue(record['reaped'])
        self.assertEqual(samples, [71])  # never sample after the positive/reaping wait

    def test_exact_deadline_and_one_float_step_late(self):
        for upper, expected in ((2.0, 'qualified'), (math.nextafter(2.0, math.inf), 'evidence_incomplete')):
            with self.subTest(upper=upper):
                self.now = 1.0
                def action():
                    self.now = upper
                    return 71, 0, self.usage()
                self.action = action
                record = self.observe()
                self.assertTrue(record['reaped'])
                self.assertEqual(record['wait_returned_monotonic_upper'], upper)
                self.assertEqual(self.qualify(record)['status'], expected)

    def test_nonzero_exit_is_observed_but_not_qualified(self):
        self.action = lambda: (71, 4 << 8, self.usage())
        record = self.observe()
        self.assertEqual(record['status'], 'observed')
        self.assertEqual(record['returncode'], 4)
        self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_signalled_status_decoded_without_sending_signal(self):
        self.action = lambda: (71, 15, self.usage())
        record = self.observe()
        self.assertEqual(record['returncode'], -15)
        self.assertFalse(record['signal_authority'])
        self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_permission_failure_stops_without_fallback_or_retry(self):
        def denied():
            raise PermissionError(errno.EPERM, 'denied')
        self.action = denied
        record = self.observe()
        self.assertFalse(record['reaped'])
        self.assertIsNone(record['cpu'])
        self.assertIsNone(record['wait_returned_monotonic_upper'])
        self.assertEqual(record['errors'][0]['errno'], errno.EPERM)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_echild_has_no_invented_completion(self):
        def missing():
            raise ChildProcessError(errno.ECHILD, 'not owned')
        self.action = missing
        record = self.observe()
        self.assertFalse(record['reaped'])
        self.assertIsNone(record['returncode'])
        self.assertEqual(len(self.calls), 1)

    def test_malformed_wait_or_wrong_pid_fails_closed(self):
        for value in (None, (71, 0), (72, 0, self.usage()), (True, 0, self.usage())):
            with self.subTest(value=value):
                self.action = lambda: value
                record = self.observe()
                self.assertEqual(record['status'], 'evidence_incomplete')
                self.assertFalse(record['reaped'])

    def test_missing_or_invalid_cpu_retains_reaped_facts(self):
        values = [None, self.usage(user=-1), self.usage(user=math.nan),
                  self.usage(system=math.inf), self.usage(user=True),
                  self.usage(user=10 ** 1000), self.usage(rss=-1), self.usage(rss=True)]
        for usage in values:
            with self.subTest(usage=usage):
                self.action = lambda: (71, 0, usage)
                record = self.observe()
                self.assertTrue(record['reaped'])
                self.assertEqual(record['raw_wait_status'], 0)
                self.assertEqual(record['wait_returned_monotonic_upper'], 1.0)
                self.assertIsNone(record['cpu'])
                self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_real_zero_usage_is_preserved_with_explicit_scope(self):
        self.action = lambda: (71, 0, self.usage(0, 0, 0))
        record = self.observe()
        self.assertEqual(record['cpu']['user_seconds'], 0)
        self.assertEqual(record['cpu']['system_seconds'], 0)
        self.assertEqual(record['cpu']['max_rss_platform_units'], 0)
        self.assertEqual(record['cpu']['scope'], 'wait4_owned_child_accounting')
        self.assertEqual(self.qualify(record)['status'], 'qualified')

    def test_unavailable_clock_descriptor_prevents_wait(self):
        completion.time.get_clock_info = lambda name: (_ for _ in ()).throw(RuntimeError('no clock'))
        record = self.observe()
        self.assertEqual(record['status'], 'evidence_incomplete')
        self.assertEqual(self.calls, [])

    def test_expired_deadline_prevents_wait(self):
        record = self.observe(deadline=1.0)
        self.assertFalse(record['reaped'])
        self.assertEqual(self.calls, [])

    def test_pre_cancel_prevents_wait(self):
        record = self.observe(cancelled=lambda: True)
        self.assertFalse(record['reaped'])
        self.assertEqual(self.calls, [])

    def test_backwards_clock_after_wait_preserves_exit(self):
        def action():
            self.now = .9
            return 71, 0, self.usage()
        self.action = action
        record = self.observe()
        self.assertTrue(record['reaped'])
        self.assertEqual(record['returncode'], 0)
        self.assertIsNone(record['wait_returned_monotonic_upper'])
        self.assertEqual(record['status'], 'evidence_incomplete')

    def test_clock_failure_after_wait_preserves_exit(self):
        def action():
            completion.time.monotonic = lambda: (_ for _ in ()).throw(RuntimeError('clock failed'))
            return 71, 0, self.usage()
        self.action = action
        record = self.observe()
        self.assertTrue(record['reaped'])
        self.assertEqual(record['returncode'], 0)
        self.assertIsNone(record['wait_returned_monotonic_upper'])
        self.assertIsNotNone(record['cpu'])

    def test_poll_cap_returns_unreaped_ownership(self):
        self.action = lambda: (0, 0, None)
        record = self.observe(max_polls=2)
        self.assertEqual(record['polls'], 2)
        self.assertFalse(record['reaped'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_deadline_callback_delay_prevents_wait(self):
        def deadline():
            self.now = 2.0
            return 2.0
        record = self.observe(deadline=deadline)
        self.assertFalse(record['reaped'])
        self.assertEqual(self.calls, [])

    def test_deadline_may_tighten(self):
        self.action = lambda: (0, 0, None)
        deadlines = iter((2.0, 1.04))
        record = self.observe(deadline=lambda: next(deadlines))
        self.assertEqual(record['polls'], 1)
        self.assertFalse(record['reaped'])

    def test_larger_callback_deadline_does_not_extend_budget(self):
        self.action = lambda: (0, 0, None)
        deadlines = iter((1.05, 10.0))
        record = self.observe(deadline=lambda: next(deadlines))
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(record['reaped'])

    def test_final_wait_rereads_tightened_deadline_without_losing_exit(self):
        for next_limit, expected in ((1.25, 'evidence_incomplete'), (3.0, 'qualified')):
            with self.subTest(next_limit=next_limit):
                self.now = 1.0
                deadlines = iter((2.0, next_limit))
                def action():
                    self.now = 1.5
                    return 71, 0, self.usage()
                self.action = action
                record = self.observe(deadline=lambda: next(deadlines))
                self.assertTrue(record['reaped'])
                self.assertEqual(record['wait_returned_monotonic_upper'], 1.5)
                self.assertIsNotNone(record['cpu'])
                self.assertEqual(self.qualify(record)['status'], expected)
                self.assertEqual(record['observation_deadline'], min(2.0, next_limit))

    def test_final_deadline_failure_or_invalid_value_preserves_exit(self):
        for value in (None, math.nan, RuntimeError('deadline unavailable')):
            with self.subTest(value=value):
                reads = 0
                def deadline():
                    nonlocal reads
                    reads += 1
                    if reads == 1:
                        return 2.0
                    if isinstance(value, Exception):
                        raise value
                    return value
                record = self.observe(deadline=deadline)
                self.assertTrue(record['reaped'])
                self.assertEqual(record['returncode'], 0)
                self.assertEqual(record['wait_returned_monotonic_upper'], 1.0)
                self.assertIsNotNone(record['cpu'])
                self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_final_deadline_callback_latency_consumes_same_budget(self):
        reads = 0
        def deadline():
            nonlocal reads
            reads += 1
            if reads == 2:
                self.now = 3.0
            return 2.0
        record = self.observe(deadline=deadline)
        self.assertTrue(record['reaped'])
        self.assertEqual(record['wait_returned_monotonic_upper'], 1.0)
        self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_pause_overrun_prevents_second_wait(self):
        self.action = lambda: (0, 0, None)
        completion.time.sleep = lambda seconds: setattr(self, 'now', 3.0)
        record = self.observe()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(record['reaped'])

    def test_post_wait_cancel_keeps_facts_but_refuses_qualification(self):
        record = self.observe(cancelled=lambda: bool(self.calls))
        self.assertTrue(record['reaped'])
        self.assertIsNotNone(record['cpu'])
        self.assertEqual(self.qualify(record)['status'], 'evidence_incomplete')

    def test_callback_or_pause_errors_do_not_escape(self):
        def fail(*args):
            raise RuntimeError('stop')
        for kwargs in ({'deadline': fail}, {'cancelled': fail}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.observe(**kwargs)['status'], 'evidence_incomplete')
        self.action = lambda: (0, 0, None)
        record = self.observe(on_poll=fail)
        self.assertFalse(record['reaped'])
        self.assertEqual(record['status'], 'evidence_incomplete')
        completion.time.sleep = fail
        self.assertEqual(self.observe()['status'], 'evidence_incomplete')

    def test_invalid_arguments_rejected_before_wait(self):
        for pid in (0, -1, True, '71'):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                completion.observe_owned_child(pid, invocation_id='id', deadline=2)
        for kwargs in ({'poll_interval': 0}, {'poll_interval': math.nan},
                       {'max_polls': True}, {'max_polls': 0}, {'on_poll': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.observe(**kwargs)
        self.assertEqual(self.calls, [])

    def test_nonterminal_wait_status_does_not_claim_reaped(self):
        self.action = lambda: (71, (19 << 8) | 0x7f, self.usage())
        record = self.observe()
        self.assertFalse(record['reaped'])
        self.assertEqual(record['raw_wait_status'], (19 << 8) | 0x7f)
        self.assertIsNone(record['returncode'])
        self.assertEqual(record['status'], 'evidence_incomplete')

    def test_qualifier_rejects_identity_clock_and_deadline_tampering(self):
        record = self.observe()
        for kwargs in ({'expected_pid': 72}, {'invocation_id': 'old-entry'},
                       {'expected_clock': dict(record['clock'], implementation='different')},
                       {'finalization_deadline': math.nextafter(1.0, -math.inf)}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.qualify(record, **kwargs)['status'], 'evidence_incomplete')

    def test_qualifier_missing_or_invalid_data_never_qualifies(self):
        record = self.observe()
        variants = [None, {}, dict(record, cpu=None), dict(record, reaped=False),
                    dict(record, wait_returned_monotonic_upper=math.nan),
                    dict(record, wait_returned_monotonic_upper=True),
                    dict(record, wait_returned_monotonic_upper=10 ** 1000),
                    dict(record, raw_wait_status=4 << 8), dict(record, scope='unrelated'),
                    dict(record, signal_authority=True)]
        bad_cpu = copy.deepcopy(record)
        bad_cpu['cpu']['system_seconds'] = -1
        variants.append(bad_cpu)
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(completion.qualify_completion(
                    value, expected_pid=71, invocation_id='new-entry',
                    expected_clock=record['clock'], finalization_deadline=2.0)['status'],
                    'evidence_incomplete')


if __name__ == '__main__':
    unittest.main()
