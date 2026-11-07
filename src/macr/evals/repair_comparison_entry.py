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
from macr.repair_cli import add_comparison_parser as add_parser

LIMIT = 2 * 1024 * 1024





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
        elif args.comparison_operation in ('prepare-final', 'import-final'):
            from .repair_final_scoring import prepare_final, import_final
            from macr.investigation.scope import paths_overlap
            inputs = [getattr(args, name) for name in ('plan', 'stage', 'record',
                'overlay_manifest', 'overlay_root', 'policy', 'custodian')]
            if args.comparison_operation == 'import-final': inputs.append(args.receipt)
            output = Path(args.out).absolute().resolve()
            if any(paths_overlap(output, Path(path).absolute().resolve()) for path in inputs):
                raise ValueError('comparison_final_output_overlap')
            plan = _read(args.plan)
            request = prepare_final(args.stage, args.record, plan, args.cell,
                _read(args.overlay_manifest), args.overlay_root, _read(args.policy), _read(args.custodian))
            values = {'request.json':request}
            summary = {'status':'prepared_pending_final_observation',
                'comparison_sha256':request['comparison_sha256'],
                'request_sha256':request['request_sha256'], 'scoring_credit_awarded':False}
            if args.comparison_operation == 'import-final':
                final, results, report = import_final(request, _read(args.receipt), plan=plan)
                values.update({'final.json':final, 'results.json':results, 'report.json':report})
                summary.update(status=final['status'], must_stop=final['must_stop'])
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
