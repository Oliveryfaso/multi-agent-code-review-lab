"""Read committed captured bytes; no target reads, AST, worker or writes."""
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import re

from ..investigation.evidence_budget import BudgetExceeded, EvidenceBudget
from ..investigation.evidence_profiles import EvidenceProfile, load_json
from ..investigation.evidence_records import EvidenceRequest, StaticEvidenceReport
from ..investigation.records import ContentManifest, ScopeManifest, SnapshotRef
from ..investigation.scope import open_directory, read_safe, validate_workspace_path
from ..investigation.source_text import SourceText, raw_physical_line_count
from ..investigation.validation import ContractError, decode_record, validate_identifier, validate_relative

READ_IO = 256 * 1024 * 1024
READ_SECONDS = 10.


def document(data):
    return {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _identity(raw, expected):
    if (type(expected) is not dict or set(expected) != {'size_bytes', 'sha256'}
            or type(expected['size_bytes']) is not int or expected['size_bytes'] < 0
            or type(expected['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', expected['sha256'])
            or document(raw) != expected):
        raise ContractError('history_corrupt', 'document_identity')


def read_committed(out: Path, binding: dict, *, envelope=None) -> dict:
    """Binding comes from this workbench's owned registration, never HTTP paths."""
    try:
        return _read_committed(Path(out).absolute(), binding, envelope)
    except BudgetExceeded as error:
        raise ContractError('history_budget_exceeded', error.field) from None
    except ContractError as error:
        if error.code in ('history_version_unsupported', 'history_corrupt', 'history_budget_exceeded'):
            raise
        raise ContractError('history_corrupt', 'saved_association') from None
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise ContractError('history_corrupt', 'saved_documents') from None


def _read_committed(out, binding, envelope):
    validate_workspace_path(out, output=True)
    request = EvidenceRequest.from_dict(binding['request'])
    if str(out) != request.out:
        raise ContractError('history_corrupt', 'out_association')
    profile = request.profile
    budget = EvidenceBudget(replace(profile, max_io_bytes=READ_IO, active_seconds=READ_SECONDS))
    with open_directory(out, observed=budget.anchors) as fd:
        info = os.fstat(fd)
        if binding['out_identity'] != [info.st_dev, info.st_ino]:
            raise ContractError('history_corrupt', 'out_identity')

    def read(relative, limit, root=out):
        budget.charge('max_metadata_entries', 1, 'history_read')
        budget.charge('max_metadata_bytes', len(relative.encode('utf-8')) + 128, 'history_read')
        try:
            return read_safe(root, relative, limit, budget=budget, stage='history_read')
        except BudgetExceeded as error:
            if error.field == 'max_file_bytes':
                raise ContractError('history_corrupt', 'saved_size') from None
            raise

    receipt_bytes = read('COMMITTED', profile.max_control_bytes)
    _identity(receipt_bytes, binding['receipt'])
    receipt = load_json(receipt_bytes.decode('utf-8'), max_bytes=profile.max_control_bytes, max_depth=64)
    if receipt.get('schema_version') != 1 or type(receipt.get('schema_version')) is not int:
        raise ContractError('history_version_unsupported', 'receipt_version')
    if (set(receipt) != {'schema_version', 'tool_version', 'status', 'profile', 'documents', 'source', 'worker', 'usage_at_verification'}
            or receipt['status'] not in ('completed', 'partial') or receipt['profile'] != profile.to_dict()
            or type(receipt['tool_version']) is not str):
        raise ContractError('history_corrupt', 'receipt_fields')
    worker = receipt['worker']
    if (type(worker) is not dict or set(worker) != {'pid', 'returncode', 'exit_confirmed'}
            or type(worker['pid']) is not int or worker['pid'] <= 0
            or type(worker['returncode']) is not int or worker['returncode'] != 0
            or worker['exit_confirmed'] is not True):
        raise ContractError('history_corrupt', 'worker_closure')
    documents = receipt['documents']
    if type(documents) is not dict or set(documents) != {'report.json', 'report.md', 'worker.log'}:
        raise ContractError('history_corrupt', 'documents')
    data = {}
    for name, cap in (('report.json', profile.max_report_bytes), ('report.md', profile.max_markdown_bytes), ('worker.log', profile.max_log_bytes)):
        data[name] = read('pending/' + name, cap)
        _identity(data[name], documents[name])
    raw_report = load_json(data['report.json'].decode('utf-8'), max_bytes=profile.max_report_bytes, max_depth=64)
    if raw_report.get('schema_version') != 1 or raw_report.get('kind') != 'static_evidence_locate':
        raise ContractError('history_version_unsupported', 'report_version')
    if raw_report.get('run', {}).get('selection_version') != 'exact-qualified-physical-span-v2':
        raise ContractError('history_version_unsupported', 'selection_version')
    report = StaticEvidenceReport.from_dict(raw_report)
    if report.request is None or report.request.to_dict() != request.to_dict() or report.status != receipt['status']:
        raise ContractError('history_corrupt', 'request_association')
    source = receipt['source']
    if (type(source) is not dict or set(source) != {'verified', 'captured_source_verified', 'content_id', 'manifest'}
            or source['verified'] is not True):
        raise ContractError('history_corrupt', 'source_association')
    if report.source_version is None:
        if (source['captured_source_verified'] is not False or source['content_id'] is not None
                or source['manifest'] is not None or report.evidence or report.selection.get('resolved')
                or any(report.observations.get(k) for k in ('symbols', 'facts', 'bindings', 'relations'))):
            raise ContractError('history_corrupt', 'source_less')
    else:
        ref = decode_record(SnapshotRef, report.source_version['snapshot_ref'])
        validate_identifier(ref.snapshot_id)
        folder = 'snapshots/' + ref.snapshot_id
        if (ref.root_ref != str(out / folder / 'files') or ref.content_manifest_ref != str(out / folder / 'manifest.json')
                or source['captured_source_verified'] is not True or source['content_id'] != ref.content_id
                or report.source_version['content_id'] != ref.content_id
                or source['manifest']['relative'] != folder + '/manifest.json'):
            raise ContractError('history_corrupt', 'snapshot_association')
        manifest_bytes = read(folder + '/manifest.json', profile.max_scope_manifest_bytes)
        _identity(manifest_bytes, {k: source['manifest'][k] for k in ('size_bytes', 'sha256')})
        if document(manifest_bytes)['sha256'] != ref.content_id:
            raise ContractError('history_corrupt', 'content_id')
        manifest = decode_record(ContentManifest, load_json(manifest_bytes.decode('utf-8'), max_bytes=profile.max_scope_manifest_bytes, max_depth=profile.max_input_depth))
        scope = decode_record(ScopeManifest, load_json(read(folder + '/scope.json', profile.max_scope_manifest_bytes).decode('utf-8'), max_bytes=profile.max_scope_manifest_bytes, max_depth=profile.max_input_depth))
        if (manifest.scope_version != ref.scope_version or scope.scope_version != ref.scope_version
                or manifest.scope_profile != asdict(scope.profile)
                or manifest.scope_profile != asdict(profile.scope_profile())):
            raise ContractError('history_corrupt', 'scope_profile')
        entries = {item.path: item for item in scope.eligible}
        paths = [item['path'] for item in manifest.files]
        if paths != sorted(set(paths)) or paths != sorted(entries):
            raise ContractError('history_corrupt', 'manifest_paths')
        refs = {}
        symbol_ids = {item.get('symbol_id') for item in report.observations.get('symbols', [])}
        for item in report.evidence:
            if item.get('snapshot_id') != ref.snapshot_id or item['file'] not in entries:
                raise ContractError('history_corrupt', 'evidence_association')
            if item.get('symbol_id') and item['symbol_id'] not in symbol_ids:
                raise ContractError('history_corrupt', 'symbol_association')
            refs.setdefault(item['file'], []).append(item)
        total = 0
        for item in manifest.files:
            validate_relative(item['path'])
            if (set(item) != {'path', 'kind', 'size_bytes', 'sha256', 'language', 'mode_intent'}
                    or item['kind'] != 'file' or item['mode_intent'] != 'non_executable'
                    or type(item['size_bytes']) is not int or not 0 <= item['size_bytes'] <= profile.max_file_bytes):
                raise ContractError('history_corrupt', 'manifest_entry')
            total += item['size_bytes']
            if total > profile.max_text_bytes:
                raise ContractError('history_corrupt', 'capture_limit')
            captured = read(folder + '/files/' + item['path'], item['size_bytes'])
            _identity(captured, {k: item[k] for k in ('size_bytes', 'sha256')})
            entry = entries[item['path']]
            if (entry.size_bytes != len(captured) or entry.language != item['language']
                    or entry.lines != raw_physical_line_count(captured)):
                raise ContractError('history_corrupt', 'scope_bytes')
            if item['path'] in refs:
                text = SourceText.from_bytes(captured, item['path'])
                for evidence in refs[item['path']]:
                    span = evidence['requested_span']
                    excerpt = text.excerpt(span['start'], span['end'], len(evidence['text'].encode('utf-8')))
                    if (excerpt.text != evidence['text'] or excerpt.complete_range != evidence['complete_range']
                            or evidence['actual_span'] != {'start': excerpt.actual_start, 'end': excerpt.actual_end}
                            or evidence.get('encoding', text.encoding) != text.encoding):
                        raise ContractError('history_corrupt', 'excerpt_bytes')
    final = None
    if envelope is not None:
        root = Path(envelope['root']).absolute()
        validate_workspace_path(root, output=True)
        with open_directory(root, observed=budget.anchors) as fd:
            info = os.fstat(fd)
            if envelope['root_identity'] != [info.st_dev, info.st_ino]:
                raise ContractError('history_corrupt', 'envelope_root')
        validate_relative(envelope['relative'])
        raw = read(envelope['relative'], profile.max_report_bytes, root)
        _identity(raw, envelope['identity'])
        decoded = load_json(raw.decode('utf-8'), max_bytes=profile.max_report_bytes, max_depth=64)
        StaticEvidenceReport.from_dict(decoded)
        for field in ('request', 'source_version', 'selection', 'evidence', 'observations', 'coverage', 'gaps', 'errors', 'limits', 'capabilities', 'status'):
            if decoded[field] != raw_report[field]:
                raise ContractError('history_corrupt', 'final_association')
        final = {'run': decoded['run'], 'usage': decoded['usage']}
    budget.check('history_complete')
    return {'report': raw_report, 'receipt': receipt, 'json_bytes': data['report.json'],
            'markdown_bytes': data['report.md'], 'read_usage': dict(budget.usage), 'final': final}
