"""Prepare a same-source diagnostic pair or replay literal offline API envelopes.

No Key reader, live flag, target import/execution, service or automatic retry.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import monotonic

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'scripts')]

from macr.investigation.diagnostic_packet import (
    PROFILE, MAX_FILES, MAX_SOURCE_BYTES, prepare_pair, run_offline_pair)
from macr.investigation.evidence_profiles import load_json
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope, open_directory, read_safe, validate_workspace_path
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError
from macr.providers.base import ModelBudget
from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive

BUDGET = ModelBudget(8000, 8192, 60)
MAX_RECORD_BYTES = 1024 * 1024


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ContractError('static_diagnostic_cli_arguments_invalid')


def _require(ok, code):
    if not ok:
        raise ContractError(code)


def _replies(path):
    path = Path(path).absolute()
    value = load_json(read_safe(path.parent, path.name, 262144).decode('utf-8'),
                      max_bytes=262144, max_depth=32)
    _require(type(value) is dict and set(value) == {'direct', 'evidence'}
        and all(type(row) is dict or row == 'timeout' for row in value.values()),
        'static_diagnostic_cli_replies_invalid')
    return value


def _capture(repo, output):
    repo, output = Path(repo).absolute(), Path(output).absolute()
    validate_workspace_path(repo)
    validate_workspace_path(output, output=True)
    _require(repo.is_dir() and not output.is_relative_to(repo)
        and not output.exists(), 'static_diagnostic_cli_output_invalid')
    with open_directory(output.parent) as fd:
        os.mkdir(output.name, 0o700, dir_fd=fd)
    started = monotonic()
    profile = ScopeProfile(profile_id=PROFILE, max_python_files=MAX_FILES,
        max_text_bytes=MAX_SOURCE_BYTES, max_file_bytes=MAX_SOURCE_BYTES)
    scope = RepositoryScope().inventory(repo, profile)
    _require(not scope.errors and not scope.unreadable and len(scope.eligible) <= MAX_FILES,
             'static_diagnostic_cli_scope_unavailable')
    # A file excluded for size must not become a silently weakened full-source baseline.
    _require(not any('size' in item.reason or 'large' in item.reason for item in scope.excluded),
             'static_diagnostic_cli_source_limit')
    store = SnapshotStore(output / 'snapshots', allowed_root=output)
    bundle = store.capture(repo, scope)
    capture_seconds = monotonic() - started
    index = RepositoryIndex(store, output / 'index', allowed_root=output)
    return bundle, index, capture_seconds


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    for name in ('prepare', 'offline'):
        command = commands.add_parser(name)
        command.add_argument('--repo', required=True, type=Path)
        command.add_argument('--entry', required=True, action='append')
        command.add_argument('--question', required=True)
        command.add_argument('--scenario', required=True)
        command.add_argument('--assumption', action='append', default=[])
        command.add_argument('--out', required=True, type=Path)
        if name == 'offline':
            command.add_argument('--replies', required=True, type=Path,
                                 help='Exact direct/evidence map of literal provider envelopes or timeout.')
    try:
        args = parser.parse_args(argv)
        replies = _replies(args.replies) if args.command == 'offline' else None
        bundle, index, capture = _capture(args.repo, args.out)
        task = dict(entries=args.entry, question=args.question, scenario=args.scenario,
                    assumptions=args.assumption, budget=BUDGET)
        if args.command == 'prepare':
            prepared = prepare_pair(bundle, index, **task)
            result = None
        else:
            result = run_offline_pair(bundle, index, replies=replies, **task)
            prepared = result['prepared']
            result['pipeline_timing_seconds'] = {'capture_common': capture,
                'preparation': prepared.get('timing_seconds', {})}
        prepared['pipeline_timing_seconds'] = {'capture_common': capture,
            'preparation': prepared.get('timing_seconds', {})}
        output = Path(args.out).absolute()
        _write_exclusive(output / 'prepared.json', prepared, MAX_RECORD_BYTES)
        if result is not None:
            _write_exclusive(output / 'result.json', result, MAX_RECORD_BYTES)
        failed = bool(result and any(row['status'] == 'failed' for row in result['rows']))
        print(json.dumps({'status': 'failed' if failed else 'prepared' if result is None else 'semantic_pending',
            'profile': PROFILE, 'output': str(output), 'model_calls_live': 0,
            'fixture_model_calls': 0 if result is None else result['fixture_model_calls'],
            'packet_sha256': prepared['packet_sha256']}, sort_keys=True, allow_nan=False))
        return 1 if failed else 0
    except (ContractError, PolicyRunError) as error:
        print(json.dumps({'status': 'rejected', 'error_code': error.code,
                          'model_calls_live': 0}, sort_keys=True))
        return 2
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print(json.dumps({'status': 'rejected', 'error_code': 'static_diagnostic_cli_input_or_io_invalid',
                          'model_calls_live': 0}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
