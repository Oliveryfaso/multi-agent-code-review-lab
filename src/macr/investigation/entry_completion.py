"""Independent completion observations for an already owned direct child.

The caller must be the sole waiter and retain cleanup ownership on incomplete
unreaped returns. This helper never launches or signals anything. Native use
still needs an approved, source-bound launcher and the original resource guards.
"""
import math
import os
import sys
import time


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_clock(clock):
    return (type(clock) is dict and clock.get('api') == 'time.monotonic'
            and type(clock.get('implementation')) is str and bool(clock['implementation'])
            and clock.get('unit') == 'seconds' and clock.get('monotonic') is True
            and clock.get('adjustable') is False
            and type(clock.get('python_implementation')) is str
            and bool(clock['python_implementation'])
            and type(clock.get('python_version')) is list and len(clock['python_version']) == 3
            and all(type(part) is int and part >= 0 for part in clock['python_version'])
            and type(clock.get('platform')) is str and bool(clock['platform']))


def _clock_descriptor():
    info = time.get_clock_info('monotonic')
    return {'api': 'time.monotonic', 'implementation': info.implementation,
            'unit': 'seconds', 'monotonic': info.monotonic, 'adjustable': info.adjustable,
            'python_implementation': sys.implementation.name,
            'python_version': list(sys.version_info[:3]), 'platform': sys.platform}


def _valid_cpu(cpu, platform):
    return (type(cpu) is dict and cpu.get('scope') == 'wait4_owned_child_accounting'
            and cpu.get('platform') == platform
            and all(_finite(cpu.get(name)) and cpu[name] >= 0
                    for name in ('user_seconds', 'system_seconds'))
            and type(cpu.get('max_rss_platform_units')) is int
            and cpu['max_rss_platform_units'] >= 0)


def _decode_status(status):
    if type(status) is not int or not 0 <= status <= 0xffff:
        raise ValueError('invalid_wait_status')
    return os.waitstatus_to_exitcode(status)


def observe_owned_child(pid, *, invocation_id, deadline, cancelled=None,
                        poll_interval=.05, max_polls=4096, on_poll=None):
    """Poll exactly ``pid`` with wait4/WNOHANG; never retry a failed OS call.

    ``deadline`` is the existing absolute monotonic deadline or a callback that
    may tighten it. A later larger value cannot extend the initial bound.
    A post-wait timestamp is an upper bound on exit, not the actual exit instant.
    CPU values are kernel wait accounting; max RSS retains platform-native units.
    ``on_poll(pid)`` may enforce live resource guards after a zero wait result;
    it is never called after a reaping wait. Errors stop without granting signals.
    """
    if (type(pid) is not int or pid <= 0 or type(invocation_id) is not str
            or not invocation_id or len(invocation_id) > 256
            or not _finite(poll_interval) or poll_interval <= 0
            or type(max_polls) is not int or max_polls <= 0
            or (cancelled is not None and not callable(cancelled))
            or (on_poll is not None and not callable(on_poll))):
        raise ValueError('invalid_completion_arguments')
    record = {'status': 'evidence_incomplete', 'scope': 'owned_direct_child_wait4',
              'pid': pid, 'invocation_id': invocation_id, 'clock': None,
              'reaped': False, 'raw_wait_status': None, 'returncode': None,
              'wait_returned_monotonic_upper': None, 'observation_deadline': None,
              'cpu': None, 'polls': 0, 'errors': [], 'signal_authority': False}

    def fail(code, error=None):
        item = {'code': code}
        if error is not None:
            item['exception_type'] = type(error).__name__
            number = getattr(error, 'errno', None)
            if type(number) is int:
                item['errno'] = number
        record['errors'].append(item)

    previous = None

    def clock_read():
        nonlocal previous
        value = time.monotonic()
        if not _finite(value) or value < 0 or (previous is not None and value < previous):
            raise ValueError('invalid_or_backwards_clock')
        previous = value
        return value

    def stopped():
        value = False if cancelled is None else cancelled()
        if type(value) is not bool:
            raise ValueError('invalid_cancellation_observation')
        return value

    def tighten_deadline():
        limit = deadline() if callable(deadline) else deadline
        if not _finite(limit):
            raise ValueError('invalid_deadline')
        old_limit = record['observation_deadline']
        record['observation_deadline'] = limit if old_limit is None else min(old_limit, limit)

    try:
        record['clock'] = _clock_descriptor()
        if not _valid_clock(record['clock']):
            raise ValueError('unsupported_clock')
    except Exception as error:
        fail('clock_metadata_unavailable', error)
        return record

    while record['polls'] < max_polls:
        try:
            tighten_deadline()
            stop = stopped()
            before = clock_read()  # deadline/cancellation callback latency counts
        except Exception as error:
            fail('pre_wait_observation_failed', error)
            return record
        if stop:
            fail('cancelled')
            return record
        if before >= record['observation_deadline']:
            fail('deadline_before_wait')
            return record
        record['polls'] += 1
        try:
            result = os.wait4(pid, os.WNOHANG)
        except Exception as error:
            fail('wait4_failed', error)
            return record
        # First operation after wait returns: no usage access or persistence first.
        after = None
        try:
            after = clock_read()
        except Exception as error:
            fail('post_wait_clock_failed', error)
        if type(result) is not tuple or len(result) != 3:
            fail('malformed_wait_result')
            return record
        waited_pid, status, usage = result
        if type(waited_pid) is not int or waited_pid not in (0, pid):
            fail('unexpected_wait_pid')
            return record
        if waited_pid == pid:
            record['raw_wait_status'] = status if type(status) is int else None
            record['wait_returned_monotonic_upper'] = after
            try:
                record['returncode'] = _decode_status(status)
                record['reaped'] = True
            except Exception as error:
                fail('nonterminal_or_invalid_wait_status', error)
            try:
                cpu = {'scope': 'wait4_owned_child_accounting', 'platform': sys.platform,
                       'user_seconds': usage.ru_utime, 'system_seconds': usage.ru_stime,
                       'max_rss_platform_units': usage.ru_maxrss}
                if not _valid_cpu(cpu, sys.platform):
                    raise ValueError('invalid_cpu_accounting')
                record['cpu'] = cpu
            except Exception as error:
                fail('cpu_accounting_unavailable', error)
            try:
                # Re-read after preserving wait facts: the bound may have shrunk
                # during the final wait. This never replaces the first upper.
                tighten_deadline()
                if stopped():
                    fail('cancelled')
                if clock_read() > record['observation_deadline']:
                    fail('completion_observation_after_deadline')
            except Exception as error:
                fail('post_wait_observation_failed', error)
            if after is not None and after > record['observation_deadline']:
                fail('wait_returned_after_deadline')
            if not record['errors']:
                record['status'] = 'observed'
            return record
        if record['errors']:
            return record
        if on_poll is not None:
            try:
                on_poll(pid)
                tighten_deadline()
                if stopped():
                    fail('cancelled')
                after = clock_read()  # guard/callback latency consumes the budget
            except Exception as error:
                fail('live_guard_failed', error)
            if record['errors']:
                return record
        if after >= record['observation_deadline']:
            fail('deadline_without_exit')
            return record
        if record['polls'] == max_polls:
            break
        try:
            time.sleep(min(poll_interval, record['observation_deadline'] - after))
        except Exception as error:
            fail('poll_pause_failed', error)
            return record
    fail('poll_limit')
    return record


def qualify_completion(record, *, expected_pid, invocation_id, expected_clock,
                       finalization_deadline):
    """Pure structural qualification; source/launch binding remains caller-owned.

    This checks only exit/accounting evidence, not whole-run native acceptance,
    process-group closure, host pressure or final persistence before the deadline.
    """
    gaps = []
    if type(record) is not dict:
        return {'status': 'evidence_incomplete', 'gaps': ['missing_completion_record']}
    if record.get('scope') != 'owned_direct_child_wait4' or record.get('signal_authority') is not False:
        gaps.append('invalid_scope')
    if (type(expected_pid) is not int or expected_pid <= 0
            or type(record.get('pid')) is not int or record['pid'] != expected_pid
            or type(invocation_id) is not str or not invocation_id
            or record.get('invocation_id') != invocation_id):
        gaps.append('identity_mismatch')
    clock = record.get('clock')
    if not _valid_clock(clock) or not _valid_clock(expected_clock) or clock != expected_clock:
        gaps.append('clock_mismatch_or_unavailable')
    if record.get('status') != 'observed' or record.get('errors') != []:
        gaps.append('observation_incomplete')
    try:
        code = _decode_status(record.get('raw_wait_status'))
    except (ValueError, OverflowError):
        code = None
    if (record.get('reaped') is not True or type(record.get('returncode')) is not int
            or record['returncode'] != 0 or code != 0):
        gaps.append('successful_exit_unconfirmed')
    upper = record.get('wait_returned_monotonic_upper')
    observed_limit = record.get('observation_deadline')
    if (not _finite(upper) or upper < 0 or not _finite(finalization_deadline)
            or not _finite(observed_limit)
            or upper > min(finalization_deadline, observed_limit)):
        gaps.append('exit_before_deadline_unconfirmed')
    if not _valid_cpu(record.get('cpu'), clock.get('platform') if type(clock) is dict else None):
        gaps.append('cpu_accounting_unavailable')
    return {'status': 'qualified' if not gaps else 'evidence_incomplete', 'gaps': gaps}
