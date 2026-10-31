"""Freeze and inspect a full-source diagnostic comparison; live is explicitly off."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'scripts')]

from macr.evals.static_diagnostic_stage import (
    prepare_stage, propose_approval, preflight_stage, run_dryrun, execute_stage)
from macr.evals.deepseek_policy_manifest import PolicyManifestError
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.evals.deepseek_study_profile import StudyError, read_json, require
from run_deepseek_value_stage import _credential_reader


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StudyError('static_stage_cli_arguments_invalid')


def _read_file(path, limit=65536):
    path = Path(path).absolute()
    return read_json(path.parent, path.name, limit)


def _scripted_replies(path):
    raw = _read_file(path, 2097152)
    require(type(raw) is dict and 1 <= len(raw) <= 24
        and all(type(cell) is str and bool(cell)
            and (type(reply) is dict or reply == 'timeout')
            for cell, reply in raw.items()), 'static_stage_cli_script_invalid')
    return raw


def _select(value, names):
    return {key: value[key] for key in names if key in value}


def _safe_summary(value):
    require(type(value) is dict, 'static_stage_cli_input_invalid')
    safe = _select(value, ('schema', 'plan_sha256', 'profile', 'provenance',
        'planned_cells', 'actual_requests', 'model_calls_live', 'fixture_model_calls',
        'known_accrued_usd', 'new_unknown_held_usd', 's0_qualified', 'stage_halted'))
    if type(value.get('phases')) is dict:
        safe['phases'] = {}
        for phase in ('S0', 'S1'):
            entry = value['phases'].get(phase)
            if type(entry) is not dict:
                continue
            safe['phases'][phase] = _select(entry, ('qualified', 'gate_passed', 'state',
                'status', 'planned_cells', 'processed_cells', 'halted'))
            accounting = entry.get('accounting')
            if type(accounting) is dict:
                safe['phases'][phase]['accounting'] = _select(accounting,
                    ('state', 'request_count', 'known_accrued_usd', 'unknown_held_usd',
                     'full_miss_sensitivity_usd', 'max_requests', 'max_usd'))
    if type(value.get('rows')) is list:
        safe['rows'] = [_select(row, ('cell_id', 'task_id', 'phase', 'arm', 'status', 'error_code'))
            for row in value['rows'] if type(row) is dict]
    return safe


def _prepared_summary(plan):
    safe = _select(plan, ('plan_sha256', 'profile', 'stage_id', 'model', 'endpoint'))
    safe['budgets'] = {phase: _select(plan['phases'][phase]['budget'], ('max_requests', 'max_usd'))
        for phase in ('S0', 'S1')}
    safe.update(planned_cells=24, planned_request_ceiling=24,
        execution_enabled=False, model_calls_live=0)
    return safe


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--development-pack', required=True, type=Path)
    prepare.add_argument('--qualification-pack', required=True, type=Path)
    prepare.add_argument('--custodian', required=True, type=Path,
        help='Hash-only commitment; never a private answer file.')
    prepare.add_argument('--stage-id', required=True)
    prepare.add_argument('--out', required=True, type=Path)
    prepare.add_argument('--seed', type=int, default=20261031)
    proposal = commands.add_parser('proposal')
    proposal.add_argument('--control', required=True, type=Path)
    proposal.add_argument('--project-env', type=Path,
        help='Bind an approved project .env path, never a Key value.')
    for name in ('preflight', 'execute'):
        action = commands.add_parser(name)
        action.add_argument('--control', required=True, type=Path)
        action.add_argument('--approval', required=True, type=Path)
        if name == 'execute':
            action.add_argument('--enable-live', action='store_true')
    dryrun = commands.add_parser('dry-run')
    dryrun.add_argument('--control', required=True, type=Path)
    dryrun.add_argument('--replies', required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        if args.command == 'prepare':
            plan = prepare_stage(args.development_pack, args.qualification_pack,
                custodian=_read_file(args.custodian), out=args.out,
                project_root=PROJECT, stage_id=args.stage_id, seed=args.seed)
            result = _prepared_summary(plan)
        elif args.command == 'proposal':
            source = None if args.project_env is None else {
                'kind': 'project_env', 'path': str(args.project_env.absolute())}
            result = propose_approval(args.control, credential_source=source)
        elif args.command == 'dry-run':
            result = _safe_summary(run_dryrun(args.control,
                scripted_by_cell=_scripted_replies(args.replies)))
        else:
            if args.command == 'execute':
                require(args.enable_live, 'static_stage_cli_live_disabled')
            approval = _read_file(args.approval)
            plan = preflight_stage(args.control, approval)
            if args.command == 'preflight':
                result = {'plan_sha256': plan['plan_sha256'], 'preflight': 'passed',
                    'execution_enabled': False, 'model_calls_live': 0}
            else:
                result = _safe_summary(execute_stage(args.control, approval,
                    enabled=True, key_reader=_credential_reader(approval)))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1 if args.command == 'execute' and result.get('stage_halted') else 0
    except KeyboardInterrupt:
        print('static_stage_cli_interrupted_claim_retained', file=sys.stderr)
        return 130
    except (StudyError, PolicyManifestError, PolicyRunError) as error:
        print(error.code, file=sys.stderr)
        return 2
    except OSError:
        print('static_stage_cli_io_invalid', file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError):
        print('static_stage_cli_input_invalid', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
