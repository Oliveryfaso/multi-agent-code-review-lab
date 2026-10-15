"""Synthetic committed byte fixtures, never a native worker acceptance."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_report import render_json, render_markdown
from macr.investigation.evidence_workspace import EvidenceWorkspace, encode_bounded
from macr.investigation.static_evidence import prepare_evidence
from macr.investigation.evidence_worker import document, source_association


def committed_fixture(base, *, partial=False):
    base = Path(base).resolve()
    repo, out = base / 'source', base / 'owned-out'
    repo.mkdir()
    (repo / 'entry.py').write_bytes(b'def read():\r\n    return "hello"\r\n')
    (repo / 'other.txt').write_bytes(b'captured but not displayed\n')
    profile = replace(EvidenceProfile(), min_free_bytes=1, max_body_bytes=18 if partial else 65536)
    request = EvidenceRequest(str(repo), str(out), 'Read captured source', ['entry.py:read'], profile=profile)
    workspace = EvidenceWorkspace(repo, out, profile)
    report = prepare_evidence(request, workspace)
    docs = {'report.json': render_json(report).encode(), 'report.md': render_markdown(report).encode(),
            'worker.log': b'stage=written_pending status=completed\n'}
    for name, data in docs.items():
        workspace.write('pending/' + name, data, limit=profile.max_report_bytes)
    receipt = {'schema_version': 1, 'tool_version': '0.1.0', 'status': report.status,
               'profile': profile.to_dict(), 'documents': {name: document(data) for name, data in docs.items()},
               'source': source_association(report, workspace),
               'worker': {'pid': 42, 'returncode': 0, 'exit_confirmed': True}, 'usage_at_verification': {}}
    raw = encode_bounded(receipt, profile.max_control_bytes)
    workspace.commit(raw)
    info = out.stat()
    binding = {'out_identity': [info.st_dev, info.st_ino], 'request': request.to_dict(), 'receipt': document(raw)}
    return out, binding, report


def replace_report(out, binding, transform):
    """Changed fixture documents stay bound; consumer must still check raw refs."""
    report_path = out / 'pending/report.json'
    raw = json.loads(report_path.read_bytes())
    transform(raw)
    data = (json.dumps(raw) + '\n').encode()
    report_path.write_bytes(data)
    receipt = json.loads((out / 'COMMITTED').read_bytes())
    receipt['documents']['report.json'] = document(data)
    updated = encode_bounded(receipt, 16384)
    (out / 'COMMITTED').write_bytes(updated)
    binding['receipt'] = document(updated)


def publish_fixture_request(request, *, cancelled=None):
    """Pure fixture publisher for adapter branches, not a real worker receipt."""
    workspace = EvidenceWorkspace(request.repo, request.out, request.profile)
    report = prepare_evidence(request, workspace)
    data = {'report.json': render_json(report).encode(), 'report.md': render_markdown(report).encode(), 'worker.log': b'fixture\n'}
    for name, raw in data.items():
        workspace.write('pending/' + name, raw, limit=request.profile.max_report_bytes)
    receipt = {'schema_version': 1, 'tool_version': '0.1.0', 'status': report.status, 'profile': request.profile.to_dict(),
               'documents': {name: document(raw) for name, raw in data.items()}, 'source': source_association(report, workspace),
               'worker': {'pid': 42, 'returncode': 0, 'exit_confirmed': True}, 'usage_at_verification': {}}
    workspace.commit(encode_bounded(receipt, request.profile.max_control_bytes))
    report.run.update(publication='committed', worker_started=True, worker_exit_confirmed=True, worker_pid=42)
    return report


def failed_budget_fixture(request, *, cancelled=None):
    """Synthetic budget stop; PID42 is never a native process proof."""
    from macr.investigation.evidence_records import StaticEvidenceReport
    return StaticEvidenceReport(request, 'failed', run={'worker_started': True,
        'worker_exit_confirmed': True, 'worker_pid': 42, 'publication': 'uncommitted'},
        errors=[{'code': 'scope_limit', 'field': 'max_metadata_entries',
                 'stage': 'supervisor_storage', 'retryable': False}],
        limits={'effective': request.profile.to_dict(), 'exceeded': [
            {'field': 'max_metadata_entries', 'stage': 'supervisor_storage',
             'effective': request.profile.max_metadata_entries,
             'observed': request.profile.max_metadata_entries + 1}]})
