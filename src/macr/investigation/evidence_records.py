"""Strict static evidence envelopes; no diagnostic or execution claims."""
from __future__ import annotations

import keyword
import re
from dataclasses import asdict, dataclass, field, fields

from .evidence_profiles import EvidenceProfile, bounded_json, load_json, utf8_size
from .validation import ContractError, validate_relative
from .source_text import physical_lines


@dataclass
class EvidenceRequest:
    repo: str
    out: str
    question: str
    entries: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    traceback: str = ''
    profile: EvidenceProfile = field(default_factory=EvidenceProfile)

    def __post_init__(self):
        self.validate()

    def validate(self):
        if not isinstance(self.profile, EvidenceProfile):
            raise ContractError(field='profile')
        self.profile.__post_init__()
        for name in ('repo', 'out'):
            value = getattr(self, name)
            if type(value) is not str or not value or any(c in value for c in '\x00\n\r'):
                raise ContractError(field=name)
        for name, cap in (('question', self.profile.max_question_bytes), ('traceback', self.profile.max_traceback_bytes)):
            value = getattr(self, name)
            if type(value) is not str or utf8_size(value) > cap:
                raise ContractError(field=name)
        if not self.question.strip():
            raise ContractError(field='question')
        if type(self.entries) is not list or type(self.symbols) is not list:
            raise ContractError(field='entries')
        if len(self.entries) + len(self.symbols) > self.profile.max_entries:
            raise ContractError(field='max_entries')
        if not self.entries and not self.symbols and not self.traceback.strip():
            raise ContractError(field='entry_required')
        for entry in self.entries:
            if type(entry) is not str:
                raise ContractError(field='entry')
            path, separator, selector = entry.partition(':')
            validate_relative(path)
            if separator:
                if re.fullmatch(r'[0-9]+(?:-[0-9]+)?', selector):
                    numbers = selector.split('-')
                    try:
                        start, end = int(numbers[0]), int(numbers[-1])
                    except ValueError:
                        raise ContractError(field='entry_range') from None
                    if start < 1 or end < start:
                        raise ContractError(field='entry_range')
                elif not _symbol(selector):
                    raise ContractError(field='entry_symbol')
        if any(not _symbol(value) for value in self.symbols):
            raise ContractError(field='symbol')
        if len(re.findall(r'^\s*File "[^"\r\n]+", line [0-9]+', self.traceback, re.M)) > self.profile.max_traceback_frames:
            raise ContractError(field='traceback_frames')
        bounded_json(asdict(self), max_bytes=self.profile.max_input_bytes, max_depth=self.profile.max_input_depth)
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        if type(raw) is not dict or set(raw) - {f.name for f in fields(cls)}:
            raise ContractError(field='request_fields')
        values = dict(raw)
        values['profile'] = EvidenceProfile.from_dict(values.get('profile', {}))
        bounded_json(raw, max_bytes=values['profile'].max_input_bytes, max_depth=values['profile'].max_input_depth)
        try:
            return cls(**values)
        except TypeError:
            raise ContractError(field='request_fields') from None

    @classmethod
    def from_json(cls, text, *, max_bytes=128 * 1024, max_depth=32):
        """Decode within caller-approved bounds, then validate the effective profile."""
        return cls.from_dict(load_json(text, max_bytes=max_bytes, max_depth=max_depth))


def _symbol(value):
    return type(value) is str and bool(value) and all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split('.'))


def _capabilities():
    return dict(repository_code_executed=False, dynamic_validation=False, full_repository_verified=False,
                diagnosis_status='not_performed', cause=None, data_flow_proven=False)


def _usage():
    return dict(model_calls=0, tokens=None)


def _span(value):
    if type(value) is not dict or set(value) != {'start', 'end'} or type(value['start']) is not int or type(value['end']) is not int or not 1 <= value['start'] <= value['end']:
        raise ContractError('citation_invalid', 'span')
    return value['start'], value['end']


def _evidence(item):
    required = {'evidence_id', 'content_id', 'file', 'requested_span', 'actual_span', 'text', 'complete_range', 'complete_symbol'}
    optional = {'snapshot_id', 'symbol_id', 'origin', 'encoding', 'parse_status', 'truncation_reason', 'utf8_start_column', 'utf8_end_column'}
    if set(item) - (required | optional) or required - set(item):
        raise ContractError('citation_invalid', 'evidence_fields')
    for key in ('evidence_id', 'content_id', 'file', 'text'):
        if type(item[key]) is not str or not item[key]:
            raise ContractError('citation_invalid', key)
    validate_relative(item['file'])
    requested_start, requested_end = _span(item['requested_span'])
    actual_start, actual_end = _span(item['actual_span'])
    if not requested_start <= actual_start <= actual_end <= requested_end or len(physical_lines(item['text'])) != actual_end - actual_start + 1:
        raise ContractError('citation_invalid', 'actual_span')
    if any(type(item[key]) is not bool for key in ('complete_range', 'complete_symbol')):
        raise ContractError('citation_invalid', 'completeness')
    if item['complete_range'] and (actual_start, actual_end) != (requested_start, requested_end):
        raise ContractError('citation_invalid', 'complete_range')
    if item['complete_symbol'] and (not item['complete_range'] or not item.get('symbol_id')):
        raise ContractError('citation_invalid', 'complete_symbol')
    for key in optional - {'symbol_id', 'utf8_start_column', 'utf8_end_column'}:
        if key in item and type(item[key]) is not str:
            raise ContractError('citation_invalid', key)
    if 'symbol_id' in item and item['symbol_id'] is not None and (type(item['symbol_id']) is not str or not item['symbol_id']):
        raise ContractError('citation_invalid', 'symbol_id')
    for key in ('utf8_start_column', 'utf8_end_column'):
        if key in item and (type(item[key]) is not int or item[key] < 0):
            raise ContractError('citation_invalid', key)


@dataclass
class StaticEvidenceReport:
    request: EvidenceRequest | None
    status: str
    run: dict = field(default_factory=dict)
    source_version: dict | None = None
    selection: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    observations: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=_usage)
    limits: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=_capabilities)
    kind: str = 'static_evidence_locate'
    schema_version: int = 1

    def validate(self):
        if self.request is None:
            if self.status not in ('rejected', 'failed', 'cancelled') or self.source_version is not None:
                raise ContractError(field='request')
        elif isinstance(self.request, EvidenceRequest):
            self.request.validate()
        else:
            raise ContractError(field='request')
        if type(self.schema_version) is not int or self.schema_version != 1 or self.kind != 'static_evidence_locate':
            raise ContractError(field='report_version')
        if type(self.status) is not str or self.status not in ('completed', 'partial', 'rejected', 'failed', 'cancelled'):
            raise ContractError(field='status')
        for name in ('run', 'selection', 'observations', 'coverage', 'usage', 'limits', 'capabilities'):
            if type(getattr(self, name)) is not dict:
                raise ContractError(field=name)
        if self.source_version is not None and type(self.source_version) is not dict:
            raise ContractError(field='source_version')
        for name in ('evidence', 'gaps', 'errors'):
            if type(getattr(self, name)) is not list or any(type(item) is not dict for item in getattr(self, name)):
                raise ContractError(field=name)
        profile = self.request.profile if self.request is not None else EvidenceProfile()
        raw = {definition.name: getattr(self, definition.name) for definition in fields(self)}
        raw['request'] = self.request.to_dict() if self.request is not None else None
        bounded_json(raw, max_bytes=profile.max_report_bytes, max_depth=64)
        expected = _capabilities()
        if set(self.capabilities) != set(expected) or any(type(self.capabilities[key]) is not type(value) or self.capabilities[key] != value for key, value in expected.items()):
            raise ContractError(field='capabilities')
        if type(self.usage.get('model_calls')) is not int or self.usage['model_calls'] != 0 or self.usage.get('tokens', 'missing') is not None:
            raise ContractError(field='usage')
        for group in (self.usage, self.coverage):
            for key, value in group.items():
                if key.startswith(('max_', 'total_')) or key.endswith(('_bytes', '_count', '_seconds')):
                    if type(value) not in (int, float) or value < 0:
                        raise ContractError(field=key)
        for item in self.errors:
            if type(item.get('code')) is not str or not re.fullmatch(r'[a-z][a-z0-9_]*', item['code']):
                raise ContractError(field='error_code')
        if self.status in ('rejected', 'failed', 'cancelled') and self.evidence:
            raise ContractError(field='failure_evidence')
        if self.source_version is None and (self.evidence or any(self.observations.get(name) for name in ('facts', 'bindings', 'relations'))):
            raise ContractError(field='source_required')
        if self.status == 'completed' and self.source_version is None:
            raise ContractError(field='source_required')
        if self.source_version is not None:
            content_id = self.source_version.get('content_id')
            if type(content_id) is not str or not content_id:
                raise ContractError(field='content_id')
            if any(item.get('content_id') != content_id for item in self.evidence):
                raise ContractError('citation_invalid', 'content_id')
        ids = set()
        for item in self.evidence:
            _evidence(item)
            if item['evidence_id'] in ids:
                raise ContractError('citation_invalid', 'duplicate_evidence')
            ids.add(item['evidence_id'])
        if self.observations.get('data_flow_proven', False) is not False:
            raise ContractError(field='data_flow_proven')
        if self.status == 'completed' and (self.selection.get('unresolved') or self.selection.get('ambiguous') or any(item.get('required', False) for item in self.gaps)):
            raise ContractError(field='completed_gaps')
        if 'status' in self.run and self.run['status'] != self.status:
            raise ContractError(field='run_status')
        if len(self.evidence) > profile.max_refs:
            raise ContractError(field='max_refs')
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        if type(raw) is not dict or set(raw) - {f.name for f in fields(cls)}:
            raise ContractError(field='report_fields')
        values = dict(raw)
        if 'request' not in values:
            raise ContractError(field='request')
        if values['request'] is not None:
            values['request'] = EvidenceRequest.from_dict(values['request'])
        try:
            return cls(**values).validate()
        except TypeError:
            raise ContractError(field='report_fields') from None

    @classmethod
    def from_json(cls, text, max_bytes=4 * 1024 * 1024):
        return cls.from_dict(load_json(text, max_bytes=max_bytes, max_depth=64))
