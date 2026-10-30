"""Prepare or inspect a frozen pilot; live execution needs separate approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
PROJECT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(PROJECT/'src'),str(PROJECT/'scripts')]

from macr.evals.deepseek_comparison_pilot import (
    prepare_pilot, propose_pilot_approval, preflight_approved_pilot,
    execute_approved_pilot, run_pilot_dryrun)
from macr.evals import deepseek_comparison_pilot as pilot
from macr.evals.deepseek_study_profile import StudyError, read_json, require
from run_deepseek_study_live import _read_key_without_echo


def _read_file(path,limit=262144):
    path=path.absolute()
    return read_json(path.parent,path.name,limit)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare',help='Freeze metadata without credentials or requests.')
    prepare.add_argument('--phase',choices=['R','U'],required=True)
    prepare.add_argument('--pack',type=Path,required=True)
    prepare.add_argument('--custodian',type=Path,required=True,help='Hash-only commitment JSON, never gold.')
    prepare.add_argument('--selection',type=Path,help='R-only custodian truncation-selection JSON.')
    prepare.add_argument('--regression-control',type=Path,help='U-only completed R control root.')
    prepare.add_argument('--out',type=Path,required=True)
    freeze=commands.add_parser('freeze-unseen',help='Freeze U scope only, without an execution plan or R receipt.',
        description='Return a pre-freeze scope_sha256, not an execution plan hash; no R receipt is created.')
    freeze.add_argument('--pack',type=Path,required=True)
    freeze.add_argument('--custodian',type=Path,required=True,help='Hash-only commitment JSON, never gold.')
    freeze.add_argument('--out',type=Path,required=True)
    finalize=commands.add_parser('finalize-unseen',help='Create the U plan only after a qualified real R receipt.',
        description='Write the final U plan only after an eligible real R receipt; this does not execute it.')
    finalize.add_argument('--control',type=Path,required=True)
    finalize.add_argument('--regression-control',type=Path,required=True)
    for name in ('proposal','preflight','execute','dry-run'):
        action=commands.add_parser(name)
        action.add_argument('--control',type=Path,required=True)
        if name in ('preflight','execute'):
            action.add_argument('--approval',type=Path,required=True,help='Exact approval JSON, never a Key file.')
        if name=='execute':action.add_argument('--enable-live',action='store_true')
        if name=='dry-run':action.add_argument('--scripts',type=Path,required=True,help='Literal offline replies JSON.')
    args=parser.parse_args(argv)
    try:
        if args.command=='freeze-unseen':
            result=pilot.freeze_unseen_inputs(args.pack,custodian=_read_file(args.custodian),
                out=args.out,project_root=PROJECT,seed=20261030)
        elif args.command=='finalize-unseen':
            result=pilot.finalize_unseen_pilot(args.control,regression_control=args.regression_control)
        elif args.command=='prepare':
            require(args.phase!='R' or args.selection is not None,'pilot_cli_selection_missing')
            require(args.phase!='U' or args.regression_control is not None,'pilot_cli_regression_control_missing')
            require(not (args.phase=='R' and args.regression_control is not None)
                and not (args.phase=='U' and args.selection is not None),'pilot_cli_phase_flags_invalid')
            result=prepare_pilot(args.pack,custodian=_read_file(args.custodian),out=args.out,
                phase=args.phase,project_root=PROJECT,
                regression_selection=_read_file(args.selection) if args.selection is not None else None,
                regression_control=args.regression_control,seed=20261030)
        elif args.command=='proposal':
            result=propose_pilot_approval(args.control)
        elif args.command=='dry-run':
            scripts=_read_file(args.scripts,2097152)
            require(type(scripts) is dict and bool(scripts) and all(type(replies) is list
                and 1<=len(replies)<=3 for replies in scripts.values()),'pilot_cli_script_invalid')
            result=run_pilot_dryrun(args.control,
                scripted_by_cell={cell:tuple(replies) for cell,replies in scripts.items()})
        else:
            if args.command=='execute':require(args.enable_live,'pilot_cli_live_disabled')
            approval=_read_file(args.approval,65536)
            plan=preflight_approved_pilot(args.control,approval)
            if args.command=='preflight':
                result={'phase':plan['phase'],'plan_sha256':plan['plan_sha256'],
                    'preflight':'passed','execution_enabled':False,'model_calls_live':0}
            else:
                require(sys.stdin.isatty() and sys.stderr.isatty(),'pilot_cli_interactive_tty_required')
                result=execute_approved_pilot(args.control,approval,enabled=True,
                    key_reader=_read_key_without_echo)
        print(json.dumps(result,sort_keys=True))
        return 1 if args.command in ('execute','dry-run') and result.get('batch_halted') else 0
    except KeyboardInterrupt:
        print('pilot_cli_interrupted_no_retry',file=sys.stderr)
        return 130
    except (StudyError,OSError) as error:
        print(getattr(error,'code','pilot_cli_io_invalid'),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
