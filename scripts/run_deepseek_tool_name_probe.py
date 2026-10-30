"""Prepare or inspect two developer checks; live access requires exact approval."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'scripts')]

from macr.evals.deepseek_tool_name_probe import (
    prepare_probe, propose_probe_approval, preflight_probe,
    execute_probe, run_probe_dryrun, PROMPT_VARIANTS)
from macr.evals.deepseek_policy_manifest import PolicyManifestError
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.evals.deepseek_study_profile import StudyError, read_json, require
from run_deepseek_study_live import _read_key_without_echo


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown argument values may be credentials; do not echo them.
        raise StudyError('probe_cli_arguments_invalid')


def _read_file(path):
    path = Path(path).absolute()
    return read_json(path.parent, path.name, 65536)


def _read_project_key(path):
    """Read one approval-bound project file only when the owner calls back."""
    values = {}
    try:
        path = Path(path)
        require(path.is_absolute() and path.name == '.env', 'probe_cli_key_source_invalid')
        require(path == path.resolve(strict=True), 'probe_cli_key_source_invalid')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_size <= 32768,
                    'probe_cli_key_source_invalid')
            raw = os.read(descriptor, 32769)
        finally:
            os.close(descriptor)
        require(len(raw) <= 32768, 'probe_cli_key_source_invalid')
        for line in raw.decode('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            name = name.strip().removeprefix('export ').strip()
            if name not in {'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL'}:
                continue
            require(name not in values, 'probe_cli_key_source_invalid')
            value = value.strip()
            if value.startswith(('"', "'")):
                require(len(value) >= 2 and value[-1] == value[0],
                        'probe_cli_key_source_invalid')
                value = value[1:-1]
            values[name] = value
        require(values.get('DEEPSEEK_BASE_URL') in {
            'https://api.deepseek.com', 'https://api.deepseek.com/'}
            and values.get('DEEPSEEK_MODEL') in {'deepseek-flash', 'deepseek-v4-flash'},
            'probe_cli_key_service_mismatch')
        key = values.get('DEEPSEEK_API_KEY')
        require(type(key) is str and 1 <= len(key) <= 4096 and key == key.strip()
            and all(32 < ord(char) < 127 for char in key), 'probe_cli_key_invalid')
        return key
    except UnicodeError:
        raise StudyError('probe_cli_key_source_invalid') from None
    except OSError:
        raise StudyError('probe_cli_key_source_unavailable') from None
    finally:
        values.clear()


def _safe_summary(result):
    fields = ('plan_sha256', 'planned_cells', 'processed_cells', 'model_calls_live',
              'batch_halted', 'provenance')
    safe = {key: result[key] for key in fields if key in result}
    accounting = result.get('accounting')
    if type(accounting) is dict:
        safe['accounting'] = {key: accounting[key] for key in (
            'state', 'request_count', 'known_accrued_usd', 'unknown_held_usd',
            'full_miss_sensitivity_usd', 'model_calls_live', 'basis') if key in accounting}
    rows = result.get('rows')
    if type(rows) is list:
        safe['rows'] = []
        for row in rows:
            if type(row) is not dict:
                continue
            entry = {key: row[key] for key in ('case_id', 'status', 'error_code') if key in row}
            observation = row.get('observation')
            if type(observation) is dict:
                entry['observation'] = {key: observation[key] for key in (
                    'classification', 'third_decision_reached', 'expected_terminal',
                    'observed_terminal', 'terminal_matches_requested', 'flow_followed',
                    'tool_name_reason', 'tool_name_family', 'policy_decision',
                    'declared_tool_set') if key in observation}
            safe['rows'].append(entry)
    return safe


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--inputs', required=True, type=Path)
    prepare.add_argument('--out', required=True, type=Path)
    prepare.add_argument('--prompt-variant', choices=PROMPT_VARIANTS, default='baseline')
    proposal = commands.add_parser('proposal')
    proposal.add_argument('--control', required=True, type=Path)
    proposal.add_argument('--project-env', type=Path, help='Bind the project .env path, never a Key value.')
    for name in ('preflight', 'execute'):
        action = commands.add_parser(name)
        action.add_argument('--control', required=True, type=Path)
        action.add_argument('--approval', required=True, type=Path)
        if name == 'execute':
            action.add_argument('--enable-live', action='store_true')
    dryrun = commands.add_parser('dry-run')
    dryrun.add_argument('--control', required=True, type=Path)
    dryrun.add_argument('--scripts', required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == 'prepare':
            variant = {} if args.prompt_variant == 'baseline' else {'prompt_variant': args.prompt_variant}
            plan = prepare_probe(args.inputs, out=args.out, project_root=PROJECT, **variant)
            result = {key: plan[key] for key in ('plan_sha256', 'control_root', 'planned_cells')}
            result.update(execution_enabled=False, model_calls_live=0)
        elif args.command == 'proposal':
            source = (None if args.project_env is None else
                {'kind': 'project_env', 'path': str(args.project_env.absolute())})
            result = propose_probe_approval(args.control, credential_source=source)
        elif args.command == 'dry-run':
            raw = _read_file(args.scripts)
            require(type(raw) is dict and bool(raw) and all(type(key) is str
                and type(value) is list and 1 <= len(value) <= 3
                for key, value in raw.items()), 'probe_cli_script_invalid')
            result = _safe_summary(run_probe_dryrun(args.control,
                scripted_by_cell={key: tuple(value) for key, value in raw.items()}))
        else:
            if args.command == 'execute':
                require(args.enable_live, 'probe_cli_live_disabled')
            approval = _read_file(args.approval)
            plan = preflight_probe(args.control, approval)
            if args.command == 'preflight':
                result = {'plan_sha256': plan['plan_sha256'], 'preflight': 'passed',
                          'execution_enabled': False, 'model_calls_live': 0}
            else:
                source = approval['credential_source']
                if source['kind'] == 'terminal':
                    require(sys.stdin.isatty() and sys.stderr.isatty(),
                            'probe_cli_interactive_tty_required')
                    reader = _read_key_without_echo
                elif source['kind'] == 'project_env':
                    reader = lambda: _read_project_key(Path(source['path']))
                else:
                    raise StudyError('probe_cli_credential_source_invalid')
                result = _safe_summary(execute_probe(args.control, approval,
                    enabled=True, key_reader=reader))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1 if args.command == 'execute' and result.get('batch_halted') else 0
    except KeyboardInterrupt:
        print('probe_cli_interrupted_claim_retained', file=sys.stderr)
        return 130
    except (StudyError, PolicyManifestError, PolicyRunError) as error:
        print(error.code, file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print('probe_cli_io_invalid', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
