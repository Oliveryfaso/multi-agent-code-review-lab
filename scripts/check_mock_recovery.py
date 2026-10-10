"""Check optional mock recovery in owned, bounded fresh processes; never install.

Run with the Python whose packages match the committed hash lock. This is a
convenience guard for trusted project tests, not a sandbox for untrusted code.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import time
from uuid import uuid4

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]

MIB = 1024 * 1024
WALL_SECONDS = 120
CHILD_SECONDS = 30
OUTPUT_BYTES = 64 * 1024
FIXTURE_BYTES = 64 * MIB
RECORD_BYTES = 2 * MIB
ENVIRONMENT = {'PATH': os.defpath, 'LANG': 'C.UTF-8',
               'LANGSMITH_TRACING_V2': 'false', 'LANGCHAIN_TRACING_V2': 'false',
               'LANGSMITH_OTEL_ENABLED': 'false', 'LANGSMITH_OTEL_ONLY': 'false'}
UNKNOWN_EXPLANATION = (
    'The simulated external outcome is unknown. Automatic replay is disabled; '
    'no external action was performed.')
SUITES = (('test_mock_recovery_check.MockRecoveryCheckTests', 9),
          ('test_langgraph_mock_recovery.LangGraphMockRecoveryTests', 21),
          ('test_mock_recovery_cli.MockRecoveryCLITests', 16))
CASES = (
    ('normal', 'start', 'completed', 0, {'evidence': 1, 'report': 1}, 1),
    ('normal', 'resume', 'completed', 0, {'evidence': 0, 'report': 0}, 0),
    ('pause', 'start', 'paused', 2, {'evidence': 1, 'report': 0}, 0),
    ('pause', 'status', 'paused', 2, {'evidence': 0, 'report': 0}, 0),
    ('pause', 'resume', 'completed', 0, {'evidence': 0, 'report': 1}, 1),
    ('pause', 'resume', 'completed', 0, {'evidence': 0, 'report': 0}, 0),
    ('unknown', 'start', 'blocked', 2, {'evidence': 1, 'report': 0}, 0),
    ('unknown', 'status', 'blocked', 2, {'evidence': 0, 'report': 0}, 0),
    ('unknown', 'resume', 'rejected', 2, None, 0),
)
SUITE_CHILD = r'''
import json, sys, unittest
from pathlib import Path
project, label, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path[:0] = [str(project / name) for name in ('src', 'tests', 'scripts')]
from run_offline_tests import run_suite
loader = unittest.TestLoader()
suite = loader.loadTestsFromName(label)
if loader.errors:
    raise SystemExit('explicit_suite_load_failed')
report = run_suite(suite, runs_root=output)
print(json.dumps(report))
raise SystemExit(0 if report['status'] == 'passed' else 1)
'''
NO_SITE_CHILD = r'''
import contextlib, importlib.metadata, io, json, sys
from pathlib import Path
project, arguments = Path(sys.argv[1]), json.loads(sys.argv[2])
packages = ('langgraph', 'langgraph-checkpoint-sqlite', 'langgraph-checkpoint')
metadata = {}
for package in packages:
    try:
        metadata[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        metadata[package] = None
sys.path.insert(0, str(project / 'src'))
stdout, stderr = io.StringIO(), io.StringIO()
code = 0
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    from macr.cli import main
    sys.argv = ['agent-review', *arguments]
    try:
        main()
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
frameworks = sorted(name for name in sys.modules if name == 'langgraph' or
                    name.startswith('langgraph.') or name == 'langsmith' or
                    name.startswith('langsmith.'))
print(json.dumps({'cli_stdout': stdout.getvalue(), 'cli_stderr': stderr.getvalue(),
                  'cli_exit': code, 'optional_metadata': metadata,
                  'framework_modules': frameworks}))
raise SystemExit(code)
'''


def locked_versions():
    """Validate every installed version without importing optional packages."""
    pins = {}
    lock = PROJECT / 'requirements/mock-recovery-py312-macos-arm64.lock'
    for line in lock.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^ ]+) --hash=sha256:[0-9a-f]{64}', line)
        if match is None:
            raise RuntimeError('lock_format_invalid')
        name = re.sub('[-_.]+', '-', match[1]).lower()
        if name in pins:
            raise RuntimeError('duplicate_lock_distribution')
        pins[name] = match[2]
    if len(pins) != 41:
        raise RuntimeError('lock_distribution_count_mismatch')
    for name, expected in pins.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise RuntimeError('locked_dependency_unavailable: ' + name) from None
        if actual != expected:
            raise RuntimeError('locked_dependency_version_mismatch: ' + name)
    return pins


def fixture_bytes(roots):
    allocated = 0
    for root in roots:
        if not root.exists() and not root.is_symlink():
            continue
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise RuntimeError('unexpected_fixture_root')
        # Trusted rejection tests briefly create links; count their own inode only.
        for directory, directories, files in os.walk(root, followlinks=False):
            entries = [Path(directory), *(Path(directory) / name for name in files)]
            entries += [Path(directory) / name for name in directories
                        if (Path(directory) / name).is_symlink()]
            for path in entries:
                try:
                    entry = path.lstat()
                except FileNotFoundError:
                    continue  # The directly owned child may have just cleaned this entry.
                if not any(check(entry.st_mode) for check in
                           (stat.S_ISDIR, stat.S_ISREG, stat.S_ISLNK)):
                    raise RuntimeError('unexpected_fixture_entry')
                allocated += entry.st_blocks * 512
    if allocated > FIXTURE_BYTES:
        raise RuntimeError('allocated_bytes_limit')
    return allocated


def run_child(command, remaining, roots):
    """Capture bounded pipes and terminate only this directly created child."""
    if remaining <= 0:
        raise RuntimeError('wall_seconds_limit')
    started = time.monotonic()
    deadline = started + min(CHILD_SECONDS, remaining)
    fixture_bytes(roots)
    process = subprocess.Popen(command, cwd=PROJECT, env=ENVIRONMENT,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    selector = None
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    error, peak = None, 0
    try:
        selector = selectors.DefaultSelector()
        for name in captured:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                error = 'child_timeout'
                break
            peak = max(peak, fixture_bytes(roots))
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = OUTPUT_BYTES - sum(map(len, captured.values()))
                captured[key.data].extend(chunk[:available])
                if len(chunk) > available:
                    error = 'child_output_limit'
                    break
            if error:
                break
        if error and process.poll() is None:
            process.kill()
        process.wait(timeout=max(0.001, min(1, deadline - time.monotonic())))
        peak = max(peak, fixture_bytes(roots))
    except Exception as failure:
        error = error or str(failure) or type(failure).__name__
    finally:
        # Native worker threads cannot extend the parent's test budget.
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        if selector is not None:
            selector.close()
        process.stdout.close()
        process.stderr.close()
    return {name: bytes(value).decode('utf-8', errors='replace')
            for name, value in captured.items()} | {
                'exit_code': process.returncode, 'bound_error': error,
                'output_bytes': sum(map(len, captured.values())),
                'allocated_peak_bytes': peak,
                'elapsed_seconds': round(time.monotonic() - started, 6)}


def mock_envelope(output, calls):
    if (output.get('mock_only') is not True or
            output.get('production_resume') is not False or
            output.get('dispatch_permitted') is not False or
            output.get('state_authority') != 'langgraph_sqlite' or
            any(type(output.get(key)) is not int or output[key] != 0
                for key in ('model_calls', 'tool_calls')) or
            type(output.get('mock_provider_calls_this_call')) is not int or
            output['mock_provider_calls_this_call'] != calls):
        raise RuntimeError('mock_boundary_or_call_count_mismatch')


def check_mock_case(output, case):
    scenario, operation, status, _, executed, calls = case
    mock_envelope(output, calls)
    if output.get('status') != status:
        raise RuntimeError('mock_status_mismatch')
    if executed is not None:
        actual = output.get('executed_mock_steps_this_call')
        if (actual != executed or not isinstance(actual, dict) or
                any(type(value) is not int for value in actual.values())):
            raise RuntimeError('actual_mock_execution_mismatch')
    if operation == 'resume' and executed == {'evidence': 0, 'report': 0} \
            and output.get('cached_result') is not True:
        raise RuntimeError('completed_result_not_cached')
    if scenario == 'unknown' and output.get('explanation') != UNKNOWN_EXPLANATION:
        raise RuntimeError('unknown_explanation_missing')
    if executed is None and output.get('error') != 'outcome_unknown':
        raise RuntimeError('unknown_outcome_not_blocked')


def check_cases(report, roots, batch_root, started):
    owned = (batch_root, *roots.values())

    def child(name, command, expected):
        item = run_child(command, WALL_SECONDS - (time.monotonic() - started), owned)
        item['name'] = name
        report['cases'].append(item)
        report['allocated_peak_bytes'] = max(report['allocated_peak_bytes'],
                                             item['allocated_peak_bytes'])
        if item['bound_error']:
            raise RuntimeError(item['bound_error'])
        if item['exit_code'] != expected or item['stderr']:
            raise RuntimeError('child_exit_or_stderr_mismatch')
        output = json.loads(item['stdout'])
        if not isinstance(output, dict):
            raise RuntimeError('child_record_invalid')
        item['output'] = output
        return output

    for label, expected in SUITES:
        output = child(label, [sys.executable, '-I', '-B', '-c', SUITE_CHILD,
                              str(PROJECT), label, str(batch_root / 'suites')], 0)
        if (output.get('status') != 'passed' or output.get('tests_expected') != expected or
                output.get('tests_run') != expected or output.get('skipped') != 0 or
                output.get('failed_tests') != [] or output.get('error') is not None):
            raise RuntimeError('explicit_suite_failed_or_count_mismatch')
    script = PROJECT / 'cli/agent_review.py'
    for index, case in enumerate(CASES):
        scenario, operation, _, expected, _, _ = case
        arguments = [sys.executable, '-I', '-B', str(script), 'mock-recovery', operation,
                     '--root', str(roots[scenario])]
        if operation == 'start':
            arguments += ['--scenario', scenario]
        output = child(f'{index + 1}:{scenario}:{operation}', arguments, expected)
        check_mock_case(output, case)
    for name, arguments, expected in (
            ('no_site_default_help', ['--help'], 0),
            ('no_site_mock_missing', ['mock-recovery', 'start', '--root', str(roots['nodeps'])], 2)):
        output = child(name, [sys.executable, '-I', '-S', '-B', '-c', NO_SITE_CHILD,
                             str(PROJECT), json.dumps(arguments)], expected)
        if (output.get('cli_exit') != expected or output.get('cli_stderr') != '' or
                output.get('framework_modules') != [] or
                output.get('optional_metadata') != {key: None for key in
                    ('langgraph', 'langgraph-checkpoint-sqlite', 'langgraph-checkpoint')}):
            raise RuntimeError('no_site_smoke_mismatch')
        if name == 'no_site_mock_missing':
            result = json.loads(output['cli_stdout'])
            mock_envelope(result, 0)
            if (result.get('status') != 'rejected' or result.get('error') != 'dependency_unavailable'
                    or roots['nodeps'].exists() or roots['nodeps'].is_symlink()):
                raise RuntimeError('missing_dependency_created_or_dispatched_run')
        elif 'usage:' not in output.get('cli_stdout', ''):
            raise RuntimeError('default_help_missing')


def main():
    started = time.monotonic()
    report = {'status': 'running', 'synthetic_only': True, 'production_resume': False,
              'real_model_calls': 0, 'real_tool_calls': 0, 'cases': [],
              'allocated_peak_bytes': 0,
              'limits': {'wall_seconds': WALL_SECONDS, 'child_seconds': CHILD_SECONDS,
                         'child_output_bytes': OUTPUT_BYTES, 'allocated_fixture_bytes': FIXTURE_BYTES,
                         'record_bytes': RECORD_BYTES}}
    batch_root = None
    try:
        report['locked_versions'] = locked_versions()
        token = uuid4().hex
        candidate = PROJECT / 'artifacts/test-runs' / ('mock-check-' + token)
        runs = PROJECT / 'artifacts/implementation/langgraph-mock-recovery-01/runs'
        roots = {name: runs / ('ci-' + name + '-' + token)
                 for name in ('normal', 'pause', 'unknown', 'nodeps')}
        if any(root != root.resolve() or root.exists() or root.is_symlink()
               for root in (candidate, *roots.values())):
            raise RuntimeError('fresh_fixture_path_invalid')
        candidate.mkdir(parents=True)
        batch_root = candidate
        report['run_dir'] = str(batch_root)
        report['mock_roots'] = {key: str(value) for key, value in roots.items()}
        check_cases(report, roots, batch_root, started)
        report['allocated_peak_bytes'] = max(report['allocated_peak_bytes'],
                                             fixture_bytes((batch_root, *roots.values())))
        if time.monotonic() - started >= WALL_SECONDS:
            raise RuntimeError('wall_seconds_limit')
        report['status'] = 'passed'
    except Exception as error:
        report['status'], report['error'] = 'failed', str(error) or type(error).__name__
    report['elapsed_seconds'] = round(time.monotonic() - started, 6)
    data = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(data) > RECORD_BYTES:
        report = {key: value for key, value in report.items() if key != 'cases'}
        report.update(status='failed', error='record_bytes_limit')
        data = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if batch_root is not None and batch_root.is_dir():
        target = batch_root / 'summary.json'
        with target.open('xb') as stream:
            stream.write(data)
        print(json.dumps({'status': report['status'], 'report': str(target),
                          'cases_run': len(report.get('cases', [])),
                          'elapsed_seconds': report['elapsed_seconds'],
                          'error': report.get('error')}))
    else:
        print(data.decode('utf-8'), end='')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    if sys.argv[1:]:
        raise SystemExit('This fixed mock check accepts no arguments.')
    raise SystemExit(main())
