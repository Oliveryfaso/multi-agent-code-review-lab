"""Prepare or inspect a bounded value stage; execution needs exact approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'scripts')]

from macr.evals.deepseek_value_stage import (
    prepare_value_stage, propose_value_approval, preflight_value_stage,
    run_value_dryrun, execute_value_stage)
from macr.evals.deepseek_policy_manifest import PolicyManifestError
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.evals.deepseek_study_profile import StudyError, read_json, require
from run_deepseek_tool_name_probe import _read_project_key, _read_key_without_echo


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StudyError('value_cli_arguments_invalid')


def _read_file(path, limit=65536):
    path = Path(path).absolute()
    return read_json(path.parent, path.name, limit)


def _scripted_replies(path):
    raw = _read_file(path, 2097152)
    require(type(raw) is dict and set(raw) == {'S0', 'S1'}, 'value_cli_script_invalid')
    for phase in raw.values():
        require(type(phase) is dict and all(type(cell) is str and bool(cell)
            and type(replies) is list and 1 <= len(replies) <= 3
            for cell, replies in phase.items()), 'value_cli_script_invalid')
    return {phase: {cell: tuple(replies) for cell, replies in cells.items()}
            for phase, cells in raw.items()}


def _select(value, fields):
    return {key: value[key] for key in fields if key in value}


def _safe_accounting(value):
    return _select(value, ('state', 'request_count', 'known_accrued_usd',
        'unknown_held_usd', 'full_miss_sensitivity_usd', 'model_calls_live',
        'basis', 'max_requests', 'max_usd'))


def _safe_phase(value):
    if value is None:
        return None
    require(type(value) is dict, 'value_cli_input_invalid')
    safe = _select(value, ('plan_sha256', 'profile_sha256', 'phase', 'state', 'status',
        'planned_cells', 'processed_cells', 'model_calls_live', 'batch_halted',
        'qualified', 'gate_passed', 'passed', 'provenance'))
    accounting = value.get('accounting')
    if type(accounting) is dict:
        safe['accounting'] = _safe_accounting(accounting)
    rows = value.get('rows')
    if type(rows) is list:
        safe['rows'] = []
        for row in rows:
            if type(row) is not dict:
                continue
            entry = _select(row, ('case_id', 'task_id', 'cell_id', 'strategy',
                'status', 'state', 'error_code'))
            observation = row.get('observation')
            if type(observation) is dict:
                entry['observation'] = _select(observation, ('classification',
                    'third_decision_reached', 'expected_terminal', 'observed_terminal',
                    'terminal_matches_requested', 'flow_followed', 'tool_name_reason',
                    'tool_name_family', 'policy_decision', 'declared_tool_set'))
            safe['rows'].append(entry)
    return safe


def _safe_summary(value):
    require(type(value) is dict, 'value_cli_input_invalid')
    safe = _select(value, ('plan_sha256', 'provenance', 'model_calls_live',
        'planned_cells', 'processed_cells', 'S1_not_attempted_cells', 'stage_halted',
        'actual_requests', 'known_accrued_usd', 'new_unknown_held_usd'))
    if type(value.get('accounting')) is dict:
        safe['accounting'] = _safe_accounting(value['accounting'])
    gate = value.get('S0_gate')
    if type(gate) is bool:
        safe['S0_gate'] = gate
    elif type(gate) is dict:
        safe['S0_gate'] = _select(gate, ('qualified', 'passed', 'eligible', 'state',
            'status', 'error_code', 'identity_verified', 'flow_followed'))
    for phase in ('S0', 'S1'):
        if phase in value:
            safe[phase] = _safe_phase(value[phase])
    return safe


def _prepared_summary(plan):
    require(type(plan) is dict, 'value_cli_input_invalid')
    safe = _select(plan, ('plan_sha256', 'control_root', 'pack_sha256', 'order_seed',
        'planned_cells', 'planned_request_ceiling', 'max_requests', 'max_usd'))
    budgets = plan.get('budgets')
    if type(budgets) is dict:
        safe['budgets'] = {phase: _select(budgets[phase], ('max_requests', 'max_usd'))
            for phase in ('S0', 'S1') if type(budgets.get(phase)) is dict}
    for name in ('budget', 'stage_budget'):
        if type(plan.get(name)) is dict:
            safe[name] = _select(plan[name], ('max_requests', 'max_usd'))
    for phase in ('S0', 'S1'):
        if type(plan.get(phase)) is dict:
            safe[phase] = _safe_phase(plan[phase])
            if type(plan[phase].get('budget')) is dict:
                safe[phase]['budget'] = _select(plan[phase]['budget'], ('max_requests', 'max_usd'))
    safe.update(execution_enabled=False, model_calls_live=0)
    return safe


def _credential_reader(approval):
    source = approval.get('credential_source') if type(approval) is dict else None
    require(type(source) is dict, 'value_cli_credential_source_invalid')
    if source.get('kind') == 'terminal':
        require(set(source) == {'kind'}, 'value_cli_credential_source_invalid')
        require(sys.stdin.isatty() and sys.stderr.isatty(), 'value_cli_interactive_tty_required')
        return _read_key_without_echo
    if source.get('kind') == 'project_env':
        require(set(source) == {'kind', 'path'} and type(source.get('path')) is str
            and '\x00' not in source['path'], 'value_cli_credential_source_invalid')
        path = Path(source['path'])
        require(path.is_absolute() and path.name == '.env', 'value_cli_credential_source_invalid')
        return lambda: _read_project_key(path)
    raise StudyError('value_cli_credential_source_invalid')


def main(argv=None):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True, parser_class=_Parser)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--pack-root', required=True, type=Path)
    prepare.add_argument('--custodian', required=True, type=Path, help='Hash-only independent commitment JSON.')
    prepare.add_argument('--out', required=True, type=Path)
    prepare.add_argument('--seed', type=int, default=20261030)
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
            plan = prepare_value_stage(args.pack_root, custodian=_read_file(args.custodian),
                out=args.out, project_root=PROJECT, seed=args.seed)
            result = _prepared_summary(plan)
        elif args.command == 'proposal':
            source = (None if args.project_env is None else
                {'kind': 'project_env', 'path': str(args.project_env.absolute())})
            result = propose_value_approval(args.control, credential_source=source)
        elif args.command == 'dry-run':
            result = _safe_summary(run_value_dryrun(args.control,
                scripted_by_cell=_scripted_replies(args.scripts)))
        else:
            if args.command == 'execute':
                require(args.enable_live, 'value_cli_live_disabled')
            approval = _read_file(args.approval)
            plan = preflight_value_stage(args.control, approval)
            if args.command == 'preflight':
                result = {'plan_sha256': plan['plan_sha256'], 'preflight': 'passed',
                    'execution_enabled': False, 'model_calls_live': 0}
            else:
                result = _safe_summary(execute_value_stage(args.control, approval,
                    enabled=True, key_reader=_credential_reader(approval)))
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 1 if args.command == 'execute' and result.get('stage_halted') else 0
    except KeyboardInterrupt:
        print('value_cli_interrupted_claim_retained', file=sys.stderr)
        return 130
    except (StudyError, PolicyManifestError, PolicyRunError) as error:
        print(error.code, file=sys.stderr)
        return 2
    except OSError:
        print('value_cli_io_invalid', file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError):
        print('value_cli_input_invalid', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
