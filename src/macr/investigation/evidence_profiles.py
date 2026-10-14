"""Versioned configurable limits; increasing limits never expands read authority."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields, replace

from .records import ScopeProfile
from .validation import ContractError


def utf8_size(text):
    try:
        return len(text.encode('utf-8'))
    except UnicodeError:
        raise ContractError(field='utf8_text') from None


def bounded_json(value, *, max_bytes=128 * 1024, max_depth=32):
    for name, limit in (('max_bytes', max_bytes), ('max_depth', max_depth)):
        if type(limit) is not int or limit <= 0:
            raise ContractError(field=name)
    def check(item, depth=0):
        if depth > max_depth:
            raise ContractError(field='json_depth')
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ContractError(field='json_type')
    check(value)
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode('utf-8'))
    except (ValueError, OverflowError, RecursionError):
        raise ContractError(field='json_serialization') from None
    if encoded_size > max_bytes:
        raise ContractError(field='json_bytes')
    return value


def load_json(text, *, max_bytes=128 * 1024, max_depth=32):
    for name, limit in (('max_bytes', max_bytes), ('max_depth', max_depth)):
        if type(limit) is not int or limit <= 0:
            raise ContractError(field=name)
    if type(text) is not str or utf8_size(text) > max_bytes:
        raise ContractError(field='json_bytes')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError(field='duplicate_json_key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError(field='nonfinite')))
        return bounded_json(value, max_bytes=max_bytes, max_depth=max_depth)
    except ContractError:
        raise
    except (ValueError, RecursionError, UnicodeError):
        raise ContractError(field='json') from None


@dataclass(frozen=True)
class EvidenceProfile:
    name: str = 'standard'
    version: str = '1'
    exclude_patterns: list[str] = field(default_factory=list)
    max_python_files: int = 2000
    max_text_files: int = 2000
    max_python_lines: int = 100000
    max_text_bytes: int = 64 * 1024 * 1024
    max_file_bytes: int = 512 * 1024
    max_metadata_entries: int = 20000
    max_metadata_bytes: int = 4 * 1024 * 1024
    max_entries: int = 6
    max_functions: int = 6
    max_body_bytes: int = 64 * 1024
    max_collector_bytes: int = 32 * 1024
    max_facts: int = 32
    max_bindings: int = 256
    max_refs: int = 256
    max_candidates: int = 50
    max_ast_nodes_per_file: int = 100000
    max_ast_depth: int = 512
    max_ast_nodes: int = 1000000
    max_symbols: int = 100000
    max_relations: int = 200000
    max_io_bytes: int = 1024 * 1024 * 1024
    active_seconds: float = 120.0
    cleanup_seconds: float = 5.0
    max_report_bytes: int = 4 * 1024 * 1024
    max_markdown_bytes: int = 4 * 1024 * 1024
    max_log_bytes: int = 1024 * 1024
    max_scope_manifest_bytes: int = 4 * 1024 * 1024
    max_index_cache_bytes: int = 32 * 1024 * 1024
    max_allocated_bytes: int = 512 * 1024 * 1024
    min_free_bytes: int = 1024 * 1024 * 1024
    max_worker_rss_bytes: int = 1024 * 1024 * 1024
    max_control_bytes: int = 16 * 1024
    max_stderr_bytes: int = 64 * 1024
    max_question_bytes: int = 8 * 1024
    max_traceback_bytes: int = 64 * 1024
    max_traceback_frames: int = 64
    max_input_bytes: int = 128 * 1024
    max_input_depth: int = 32

    def __post_init__(self):
        if self.name not in ('standard', 'larger') or type(self.name) is not str or type(self.version) is not str or self.version != '1':
            raise ContractError(field='profile_version')
        if type(self.exclude_patterns) is not list or any(type(item) is not str or not item or '\x00' in item for item in self.exclude_patterns):
            raise ContractError(field='exclude_patterns')
        for definition in fields(self):
            if definition.name in ('name', 'version', 'exclude_patterns'):
                continue
            value = getattr(self, definition.name)
            if definition.name.endswith('_seconds'):
                try:
                    valid = type(value) in (int, float) and math.isfinite(value) and value > 0
                except OverflowError:
                    valid = False
            else:
                valid = type(value) is int and value > 0
            if not valid:
                raise ContractError(field=definition.name)

    @classmethod
    def select(cls, name='standard', overrides=None):
        base = cls(name=name)
        if name == 'larger':
            base = replace(base, max_python_files=4000, max_text_files=4000,
                           max_python_lines=200000, max_text_bytes=128 * 1024 * 1024,
                           max_body_bytes=256 * 1024, active_seconds=240.0,
                           max_io_bytes=2 * 1024 * 1024 * 1024,
                           max_allocated_bytes=1024 * 1024 * 1024,
                           max_worker_rss_bytes=2 * 1024 * 1024 * 1024)
        if overrides is None:
            return base
        bounded_json(overrides)
        if type(overrides) is not dict or set(overrides) - {f.name for f in fields(cls) if f.name not in ('name', 'version')}:
            raise ContractError(field='profile_overrides')
        return replace(base, **overrides)

    def to_dict(self):
        self.__post_init__()
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        bounded_json(raw)
        if type(raw) is not dict or set(raw) - {f.name for f in fields(cls)}:
            raise ContractError(field='profile_fields')
        if type(raw.get('version', '1')) is not str or raw.get('version', '1') != '1':
            raise ContractError(field='profile_version')
        return cls.select(raw.get('name', 'standard'), {key: value for key, value in raw.items() if key not in ('name', 'version')})

    @classmethod
    def from_json(cls, text, name='standard'):
        return cls.select(name, load_json(text))

    def scope_profile(self):
        self.__post_init__()
        return ScopeProfile(profile_id=self.name, version=self.version,
                            max_python_files=self.max_python_files, max_python_lines=self.max_python_lines,
                            max_text_bytes=self.max_text_bytes, max_file_bytes=self.max_file_bytes,
                            exclude_patterns=list(self.exclude_patterns))
