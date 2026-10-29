"""Review a frozen study; execution requires separate approval and explicit flags."""
from __future__ import annotations
import argparse
import getpass
import json
from pathlib import Path
import sys
import warnings

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from macr.evals.deepseek_study_live import (execute_approved_study,
    preflight_approved_study, propose_study_live_approval)
from macr.evals.deepseek_study_profile import StudyError, read_json, require


def _read_key_without_echo():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass('DeepSeek API Key (hidden): ', stream=sys.stderr)
    except Exception:
        raise StudyError('study_live_no_echo_input_unavailable') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', required=True, type=Path)
    parser.add_argument('--approval', type=Path, help='Explicit approval record, never a Key file.')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--proposal', action='store_true')
    actions.add_argument('--preflight', action='store_true')
    actions.add_argument('--execute', action='store_true')
    parser.add_argument('--enable-live', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not args.preflight and not args.execute:
            require(not args.enable_live and args.approval is None, 'study_live_flags_invalid')
            result = propose_study_live_approval(args.control)
        else:
            require(args.approval is not None, 'study_live_approval_missing')
            path = args.approval.absolute()
            approval = read_json(path.parent, path.name, 65536)
            study = preflight_approved_study(args.control, approval)
            if args.preflight:
                require(not args.enable_live, 'study_live_flags_invalid')
                result = {'profile_sha256': study['profile_sha256'], 'preflight': 'passed',
                          'execution_enabled': False, 'model_calls_live': 0}
            else:
                require(args.enable_live, 'study_live_disabled')
                require(sys.stdin.isatty() and sys.stderr.isatty(), 'study_live_interactive_tty_required')
                result = execute_approved_study(args.control, approval, enabled=True,
                    key_reader=_read_key_without_echo)
        print(json.dumps(result, sort_keys=True))
        return 1 if args.execute and result['batch_halted'] else 0
    except KeyboardInterrupt:
        print('study_live_interrupted_claim_retained', file=sys.stderr)
        return 130
    except (StudyError, OSError) as error:
        print(getattr(error, 'code', 'study_live_io_invalid'), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
