"""Bounded local comparison compilation/reporting, without dispatch authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from macr.investigation.repair_io import encode_bounded, load_json, safe_write
from macr.investigation.scope import read_safe, safe_parent, validate_workspace_path
from macr.investigation.validation import ContractError
from .repair_comparison import build_report, compile_comparison, generation_view

LIMIT = 2 * 1024 * 1024


def add_parser(subparsers):
    parser = subparsers.add_parser('repair-comparison', help='Local frozen repair plans and full-denominator reports; never dispatches calls or tests.')
    commands = parser.add_subparsers(dest='comparison_operation', required=True)
    compile_parser = commands.add_parser('compile')
    compile_parser.add_argument('--spec', type=Path, required=True, help='Explicit nonsecret comparison specification; no private scoring files are opened.')
    report_parser = commands.add_parser('report')
    report_parser.add_argument('--plan', type=Path, required=True)
    report_parser.add_argument('--results', type=Path, required=True, help='Sanitized result map by cell ID; omit missing cells, never invent passes.')
    handoff_parser = commands.add_parser('handoff', help='Revalidate one saved repair record and export a pending, unscored packet.')
    handoff_parser.add_argument('--plan', type=Path, required=True)
    handoff_parser.add_argument('--record', type=Path, required=True)
    handoff_parser.add_argument('--cell', required=True)
    scoring_parser = commands.add_parser('stage-scoring', help='Stage committed, whole evaluator tests over a revalidated record; never executes or scores.')
    scoring_parser.add_argument('--plan', type=Path, required=True)
    scoring_parser.add_argument('--record', type=Path, required=True)
    scoring_parser.add_argument('--cell', required=True)
    scoring_parser.add_argument('--overlay-manifest', type=Path, required=True)
    scoring_parser.add_argument('--overlay-root', type=Path, required=True)
    for command in (compile_parser, report_parser, handoff_parser, scoring_parser):
        command.add_argument('--out', type=Path, required=True, help='New exclusive local artifact directory; never overwritten or resumed.')


def _read(path):
    path = Path(path).absolute()
    validate_workspace_path(path, output=True)
    return load_json(read_safe(path.parent, path.name, LIMIT).decode('utf-8'),
                     max_bytes=LIMIT, max_depth=24, utf8_bytes=True)


def _write_new(out, values):
    # Validate and serialize every artifact before claiming a new output. Failed
    # writes remain partial artifacts and cannot be silently overwritten/resumed.
    prepared = {name: encode_bounded(value, LIMIT) for name, value in values.items()}
    if sum(map(len, prepared.values())) > LIMIT:
        raise ValueError('comparison_output_size')
    out = Path(out).absolute()
    validate_workspace_path(out, output=True)
    if out != out.resolve():
        raise ValueError('comparison_output_identity')
    with safe_parent(out.parent, out.name) as (parent, leaf):
        os.mkdir(leaf, 0o700, dir_fd=parent)
    for name, body in prepared.items():
        safe_write(out, name, body, limit=LIMIT)


def run(args):
    try:
        if args.comparison_operation == 'compile':
            plan = compile_comparison(_read(args.spec))
            values = {'plan.json': plan}
            for task in plan['spec']['tasks']:
                task_id = task['task_id']
                values['generation/' + task_id + '.json'] = generation_view(plan, task_id)
            summary = {'status': 'compiled', 'comparison_sha256': plan['comparison_sha256'],
                       'planned_cells': plan['planned_cells'], 'max_requests': plan['max_requests'],
                       'max_usd': plan['max_usd']}
        elif args.comparison_operation == 'stage-scoring':
            from .repair_scoring_stage import stage_scoring
            staging = stage_scoring(args.record,_read(args.plan),args.cell,
                _read(args.overlay_manifest),args.overlay_root,args.out,
                input_paths=(args.plan,args.overlay_manifest))
            summary = {'status':'staged_pending_independent_scoring',
                'comparison_sha256':staging['comparison_sha256'],
                'staging_sha256':staging['staging_sha256'],
                'scoring_credit_awarded':False}
        elif args.comparison_operation == 'handoff':
            from macr.investigation.repair_entry import score_handoff
            # A handoff must never add files to its immutable input record.
            # Resolve before any output claim, including linked path aliases.
            record = Path(args.record).absolute().resolve()
            output = Path(args.out).absolute().resolve()
            if output.is_relative_to(record) or record.is_relative_to(output):
                raise ValueError('comparison_record_output_overlap')
            packet = score_handoff(args.record, _read(args.plan), args.cell)
            values = {'packet.json': packet, 'results.json': {args.cell: packet['result']}}
            summary = {'status': 'pending_independent_review', 'comparison_sha256': packet['comparison_sha256'],
                       'packet_sha256': packet['packet_sha256'], 'scoring_credit_awarded': False}
        else:
            report = build_report(_read(args.plan), _read(args.results))
            values = {'report.json': report}
            summary = {'status': 'reported', 'comparison_sha256': report['comparison_sha256'],
                       'planned_cells': report['planned_cells'], 'must_stop': report['must_stop']}
        if args.comparison_operation != 'stage-scoring':
            _write_new(args.out, values)
    except (ValueError, TypeError, OSError, UnicodeError) as error:
        # Contract codes only: no raw path, output, source or exception repr.
        code = error.code if isinstance(error, ContractError) else str(error)
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', code):
            code = 'comparison_io_or_contract_invalid'
        summary = {'status': 'rejected', 'error_code': code}
        result = 2
    else:
        result = 0
    summary.update(model_calls=0, target_code_executed=False,
                   remote_enabled=False, execution_authorized=False)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return result
