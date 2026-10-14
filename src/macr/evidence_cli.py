"""Thin local CLI; only the public supervisor collects source evidence."""
from __future__ import annotations

import errno
import json
import re
import sys
from dataclasses import fields, replace
from pathlib import Path


def add_parser(subparsers):
    parser = subparsers.add_parser('evidence-locate', help='Locate captured static source evidence; no diagnosis or target execution.')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--out', required=True)
    question = parser.add_mutually_exclusive_group(required=True)
    question.add_argument('--question')
    question.add_argument('--question-file')
    parser.add_argument('--entry', action='append', default=[], help='Relative file[:qualified_symbol|line|start-end]; repeatable.')
    parser.add_argument('--symbol', action='append', default=[], help='Qualified symbol; repeatable.')
    parser.add_argument('--traceback-file')
    parser.add_argument('--profile', choices=['standard', 'larger'], default='standard')
    parser.add_argument('--budget-file', help='Strict UTF8 JSON profile overrides; local regular file.')
    parser.add_argument('--limit', action='append', default=[], metavar='FIELD=VALUE', help='Positive finite numeric override; repeatable, last value wins.')
    parser.add_argument('--json', action='store_true', help='One report envelope; stdout empty if its effective cap cannot represent JSON.')
    return parser


def _diagnostic(stage, code, action):
    # Only known machine identifiers reach stderr, never raw error/input text.
    clean = lambda value: value if type(value) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) else 'internal_error'
    sys.stderr.write(f'stage={clean(stage)} code={clean(code)} action={clean(action)}\n')


def _numeric_overrides(values):
    from .investigation.evidence_profiles import EvidenceProfile, load_json
    from .investigation.validation import ContractError
    allowed = {field.name for field in fields(EvidenceProfile)} - {'name', 'version', 'exclude_patterns'}
    result = {}
    for item in values:
        name, separator, value = item.partition('=')
        if not separator or name not in allowed:
            raise ContractError('input_invalid', 'limit')
        parsed = load_json(value)
        if type(parsed) not in (int, float):
            raise ContractError('input_invalid', 'limit')
        result[name] = parsed
    # Validate overridden field types/ranges before auxiliary IO.
    replace(EvidenceProfile(), **result)
    return result


def _auxiliary(path, cap, field):
    from .investigation.scope import read_safe, validate_workspace_path
    from .investigation.evidence_budget import BudgetExceeded
    from .investigation.validation import ContractError
    path = Path(path).absolute()
    validate_workspace_path(path)
    try:
        data = read_safe(path.parent, path.name, cap, stage='cli_input', limit_field=field)
        return data.decode('utf-8', errors='strict')
    except BudgetExceeded:
        raise ContractError('input_invalid', field) from None
    except UnicodeError:
        raise ContractError('input_invalid', 'input_encoding') from None
    except OSError as error:
        code = 'forbidden_path' if error.errno in (errno.ELOOP, errno.ENOTDIR) else 'input_invalid'
        raise ContractError(code, 'auxiliary_file') from None


def _profile(args, profile, overrides):
    from .investigation.evidence_profiles import load_json
    from .investigation.validation import ContractError
    if args.budget_file:
        # Explicit decode limits bootstrap the budget file; the file cannot grant
        # permission for its own already-performed read.
        decode_profile = replace(profile, **overrides)
        text = _auxiliary(args.budget_file, decode_profile.max_input_bytes, 'max_input_bytes')
        values = load_json(text, max_bytes=decode_profile.max_input_bytes, max_depth=decode_profile.max_input_depth)
        allowed = {field.name for field in fields(type(profile))} - {'name', 'version'}
        if type(values) is not dict or set(values) - allowed:
            raise ContractError('input_invalid', 'budget_fields')
        profile = replace(profile, **values)
    return replace(profile, **overrides)


def _request(args, profile):
    from .investigation.evidence_records import EvidenceRequest
    question = _auxiliary(args.question_file, profile.max_question_bytes, 'max_question_bytes') if args.question_file else args.question
    traceback = _auxiliary(args.traceback_file, profile.max_traceback_bytes, 'max_traceback_bytes') if args.traceback_file else ''
    return EvidenceRequest(args.repo, args.out, question, args.entry, args.symbol, traceback, profile)


def _exit_code(report):
    if report.run.get('worker_started') and report.run.get('worker_exit_confirmed') is not True:
        return 4
    if report.status == 'completed': return 0
    if report.status == 'partial': return 1
    if report.status == 'cancelled': return 130
    codes = {item['code'] for item in report.errors}
    if codes & {'forbidden_path', 'unsupported_environment', 'citation_invalid', 'snapshot_unstable', 'snapshot_corrupt', 'stale_snapshot', 'case_collision', 'output_conflict'}:
        return 3
    if codes & {'contract_invalid', 'input_invalid'}:
        return 2
    return 4


def _failure(error, profile):
    from .investigation.evidence_records import StaticEvidenceReport
    from .investigation.validation import ContractError
    code = error.code if isinstance(error, ContractError) else 'cancelled' if isinstance(error, KeyboardInterrupt) else 'internal_error'
    return StaticEvidenceReport(None, 'cancelled' if code == 'cancelled' else 'rejected' if code in ('input_invalid', 'contract_invalid', 'forbidden_path') else 'failed',
        run={'phase':'cli_input', 'publication':'uncommitted', 'worker_started':False, 'worker_exit_confirmed':None},
        errors=[{'code':code, 'stage':'cli_input', 'field':getattr(error, 'field', 'cli'), 'retryable':False}],
        limits={'effective':profile.to_dict(), 'exceeded':[]})


def run(args):
    from .investigation.evidence_profiles import EvidenceProfile
    from .investigation.evidence_report import render_json, _display_limit
    from .investigation.evidence_budget import BudgetExceeded
    from .investigation.evidence_supervisor import collect_evidence
    from .investigation.validation import ContractError
    profile = EvidenceProfile.select(args.profile)
    try:
        overrides = _numeric_overrides(args.limit)
        # Retain caller-approved bounds even when later input is rejected.
        profile = replace(profile, **overrides)
        profile = _profile(args, EvidenceProfile.select(args.profile), overrides)
        request = _request(args, profile)
    except (ContractError, OSError, ValueError, TypeError, KeyboardInterrupt) as error:
        report = _failure(error, profile)
    else:
        try:
            report = collect_evidence(request)
        except (Exception, KeyboardInterrupt):
            # The public API normally returns typed failures. An escaped failure
            # cannot establish worker closure or publication; never invent them.
            report = _failure(RuntimeError(), profile)
            report.run.update(phase='cli_collect', worker_started=True, worker_exit_confirmed=None, publication='unknown')
    code = _exit_code(report)
    try:
        if args.json:
            try:
                output = render_json(report)
            except BudgetExceeded:
                output = json.dumps(report.to_dict(), ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n'
                _display_limit(report, output, 'max_report_bytes', 'cli_json_render')
            print(output, end='')
        else:
            summary = {'status':report.status, 'diagnosis_status':'not_performed', 'cause':None,
                       'publication':report.run.get('publication', 'unavailable')}
            if report.run.get('publication') == 'committed':
                summary['receipt'] = str(Path(args.out) / 'COMMITTED')
            output = json.dumps(summary, ensure_ascii=True) + '\n'
            _display_limit(report, output, 'max_markdown_bytes', 'cli_terminal_render')
            print(output, end='')
    except KeyboardInterrupt:
        _diagnostic('cli_output', 'cancelled', 'inspect_receipt' if report.run.get('publication') == 'committed' else 'inspect_run')
        return 130
    except (ContractError, ValueError, TypeError):
        # Impossible caps have no JSON representation. Empty stdout is the
        # bounded protocol; do not enlarge limits or invent a one-byte envelope.
        _diagnostic('cli_output', 'output_limit_unrepresentable', 'increase_output_limit')
        return 4
    except OSError:
        _diagnostic('cli_output', 'output_unavailable', 'inspect_receipt')
        return 4
    if code not in (0, 1):
        _diagnostic('cli_result', report.errors[0]['code'] if report.errors else 'internal_error', 'inspect_report')
    return code
