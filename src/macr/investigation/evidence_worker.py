"""Fixed trusted worker bootstrap; input source is never imported or executed."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

# -I/-S removes ambient package resolution; only this installed product is granted.
if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from macr.investigation.evidence_budget import EvidenceBudget
from macr.investigation.evidence_profiles import load_json
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_report import render_json, render_markdown
from macr.investigation.evidence_workspace import EvidenceWorkspace, encode_bounded
from macr.investigation.validation import ContractError, validate_identifier


def document(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}


def source_association(report, workspace):
    """Bounded manifest identity check, without rereading all saved source bytes."""
    if report.source_version is None:
        if report.evidence or report.selection.get('resolved') or any(
            report.observations.get(key) for key in ('symbols', 'facts', 'bindings', 'relations')):
            raise ContractError('citation_invalid', 'source_less_report')
        return {'verified': True, 'captured_source_verified': False, 'content_id': None,
                'manifest': None}
    ref = report.source_version.get('snapshot_ref')
    if type(ref) is not dict:
        raise ContractError('citation_invalid', 'snapshot_ref')
    identifier = ref.get('snapshot_id')
    validate_identifier(identifier)
    relative = 'snapshots/' + identifier + '/manifest.json'
    if (ref.get('content_manifest_ref') != str(workspace.root / relative)
            or ref.get('root_ref') != str(workspace.root / 'snapshots' / identifier / 'files')
            or ref.get('content_id') != report.source_version.get('content_id')
            or any(item.get('snapshot_id') != identifier for item in report.evidence)):
        raise ContractError('citation_invalid', 'snapshot_association')
    raw = workspace.read(relative, limit=workspace.profile.max_scope_manifest_bytes)
    identity = document(raw)
    if identity['sha256'] != ref['content_id']:
        raise ContractError('citation_invalid', 'manifest_identity')
    return {'verified': True, 'captured_source_verified': True,
            'content_id': ref['content_id'], 'manifest': {'relative': relative, **identity}}


def run(input_limit, *, context=None):
    from macr.investigation.static_evidence import prepare_evidence, validate_report_sources
    import resource
    raw = sys.stdin.buffer.read(input_limit + 1)
    payload = load_json(raw.decode('utf-8'), max_bytes=input_limit, max_depth=64)
    if type(payload) is not dict or set(payload) != {'request', 'token', 'deadline', 'started', 'initial_usage'}:
        raise ContractError('input_invalid', 'worker_control')
    request = EvidenceRequest.from_dict(payload['request'])
    if context is not None:
        context['profile'] = request.profile
    budget = EvidenceBudget(request.profile, deadline=payload['deadline'])
    if context is not None:
        context['budget'] = budget
    budget.started = payload['started']
    budget.usage.update(payload['initial_usage'])
    # The bounded stdin read already happened, including when its deadline expired.
    budget.usage['max_io_bytes'] = budget.usage.get('max_io_bytes', 0) + len(raw)
    budget.observe('max_io_bytes', budget.usage['max_io_bytes'], 'worker_input')
    workspace = EvidenceWorkspace.reopen(request.repo, request.out, request.profile,
                                         payload['token'], budget=budget)
    report = prepare_evidence(request, workspace)
    if report.status not in ('completed', 'partial'):
        report.validate()
        if context is not None:
            context['preparation_report'] = report
        first = report.errors[0] if report.errors else {'code': 'worker_failed', 'field': 'preparation_status'}
        error = ContractError(first['code'], first.get('field', 'preparation_status'))
        error.stage = first.get('stage', budget.stage)
        raise error
    validate_report_sources(report, workspace)
    source = source_association(report, workspace)
    budget.check('worker_render')
    report.run['phase'] = 'written_pending'
    report.usage.update(budget.usage)
    report.validate()
    staged = {'report.json': render_json(report).encode('utf-8'),
              'report.md': render_markdown(report).encode('utf-8'),
              'worker.log': ('stage=written_pending status=' + report.status + '\n').encode('ascii')}
    for name, data in staged.items():
        field = {'report.json': 'max_report_bytes', 'report.md': 'max_markdown_bytes',
                 'worker.log': 'max_log_bytes'}[name]
        workspace.write('pending/' + name, data, limit=getattr(request.profile, field), limit_field=field)
    # No source writes occur after the final full source validation above.
    workspace.check_storage('worker_final_storage')
    documents = {}
    for name in staged:
        documents[name] = document(workspace.read('pending/' + name,
            limit={'report.json': request.profile.max_report_bytes,
                   'report.md': request.profile.max_markdown_bytes,
                   'worker.log': request.profile.max_log_bytes}[name]))
    budget.check('worker_envelope')
    envelope = {'schema_version': 1, 'status': report.status, 'documents': documents,
                'source': source, 'request_sha256': document(encode_bounded(request.to_dict(), request.profile.max_input_bytes))['sha256'],
                'workspace_identity': list(workspace.identity), 'usage': budget.usage,
                'worker_peak_rss_bytes': int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
                'worker_rss_units': 'bytes'}
    encoded = encode_bounded(envelope, request.profile.max_control_bytes,
                             budget=budget, stage='worker_envelope', limit_field='max_control_bytes')
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main(input_limit):
    """Emit bounded sanitized failure control, or no details if its cap cannot fit."""
    context = {}
    try:
        run(input_limit, context=context)
        return 0
    except BaseException as error:
        profile, budget = context.get('profile'), context.get('budget')
        if profile is not None:
            from macr.investigation.evidence_budget import BudgetExceeded
            code = error.code if isinstance(error, ContractError) else 'storage_unavailable' if isinstance(error, OSError) else 'worker_failed'
            field = getattr(error, 'field', 'worker')
            stage = getattr(error, 'stage', budget.stage if budget else 'worker_input')
            safe = lambda value: type(value) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) is not None
            code, field, stage = (value if safe(value) else fallback for value, fallback in
                                  ((code, 'worker_failed'), (field, 'worker'), (stage, 'worker')))
            exceeded = {'field': field, 'stage': stage, 'effective': error.limit, 'observed': error.observed} if isinstance(error, BudgetExceeded) else None
            control = {'schema_version': 1, 'kind': 'worker_failure',
                       'error': {'code': code, 'field': field, 'stage': stage, 'exceeded': exceeded},
                       'usage': dict(budget.usage) if budget else {}}
            prepared = context.get('preparation_report')
            if prepared is not None:
                errors = []
                for item in prepared.errors:
                    errors.append({key: value if safe(value) else fallback for key, value, fallback in
                        (('code', item['code'], 'worker_failed'), ('field', item.get('field', 'worker'), 'worker'),
                         ('stage', item.get('stage', stage), 'worker'))})
                    errors[-1]['retryable'] = item.get('retryable') is True
                control['preparation'] = {'status': prepared.status, 'errors': errors,
                                          'exceeded': prepared.limits.get('exceeded', [])}
            try:
                encoded = encode_bounded(control, profile.max_control_bytes)
                sys.stdout.buffer.write(encoded)
                sys.stdout.buffer.flush()
            except BaseException:
                pass
        # No oversized fallback, raw exception text, source or diagnostic spill.
        return 4


if __name__ == '__main__':
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or int(sys.argv[1]) <= 0:
        raise SystemExit(4)
    raise SystemExit(main(int(sys.argv[1])))
