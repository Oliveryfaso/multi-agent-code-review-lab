"""Explicit local model declarations; a valid profile is not deployment attestation."""
from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

from macr.investigation.records import SCHEMA_VERSION
from macr.providers.base import ProviderCapabilities, ProviderError


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    model_id: str
    model_revision: str
    tokenizer_revision: str
    chat_template_version: str
    runtime: str
    runtime_version: str
    quantization: str
    endpoint: str
    context_limit: int
    output_limit: int
    sampling: dict
    timeout_seconds: float
    capabilities: ProviderCapabilities
    schema_version: int = SCHEMA_VERSION


def canonical_endpoint(endpoint: str) -> str:
    """No DNS lookup: localhost is pinned to the numeric IPv4 loopback."""
    try:
        if type(endpoint) is not str or any(c.isspace() or c in '\x00\\' for c in endpoint):
            raise ValueError()
        url = urlsplit(endpoint)
        port = url.port
        if (url.scheme != 'http' or url.username is not None or url.password is not None
                or url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or port is None or not 1 <= port <= 65535 or url.query or url.fragment
                or '?' in endpoint or '#' in endpoint
                or url.path not in ('/v1/chat/completions', '/chat/completions')):
            raise ValueError()
        host = '[::1]' if url.hostname == '::1' else '127.0.0.1'
        return f'http://{host}:{port}{url.path}'
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError('model_profile_invalid') from None


def validate_profile(profile: ModelProfile) -> ModelProfile:
    def require(condition):
        if not condition:
            raise ProviderError('model_profile_invalid')

    require(type(profile) is ModelProfile)
    require(type(profile.schema_version) is int and profile.schema_version == SCHEMA_VERSION)
    require(type(profile.profile_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', profile.profile_id))
    for name in ('model_id', 'model_revision', 'tokenizer_revision', 'chat_template_version',
                 'runtime', 'runtime_version', 'quantization'):
        value = getattr(profile, name)
        try:
            encoded = value.encode('utf-8') if type(value) is str else b''
        except UnicodeError:
            raise ProviderError('model_profile_invalid') from None
        require(type(value) is str and 0 < len(encoded) <= 256
                and value == value.strip() and not any(c in value for c in '\x00\n\r'))
        require(value.lower() not in ('main', 'master', 'latest', 'unknown'))
    canonical_endpoint(profile.endpoint)
    require(type(profile.context_limit) is int and 1 < profile.context_limit <= 2**20)
    require(type(profile.output_limit) is int and 0 < profile.output_limit < profile.context_limit)
    require(type(profile.timeout_seconds) in (int, float) and math.isfinite(profile.timeout_seconds)
            and 0 < profile.timeout_seconds <= 3600)
    require(type(profile.capabilities) is ProviderCapabilities
            and all(type(getattr(profile.capabilities, field.name)) is bool for field in fields(ProviderCapabilities)))
    require(type(profile.sampling) is dict and set(profile.sampling) <= {'temperature', 'top_p', 'seed'})
    for name, value in profile.sampling.items():
        if name == 'seed':
            require(type(value) is int and 0 <= value <= 2**32 - 1)
        else:
            require(type(value) in (int, float) and math.isfinite(value)
                    and (0 <= value <= 2 if name == 'temperature' else 0 < value <= 1))
    return profile


def load_profile(path: Path) -> ModelProfile:
    """Read one declared JSON below this project's artifacts; never discover configs.

    ModelProfile has no credential/environment/install fields. No directories are created.
    """
    try:
        path = Path(path)
        root = Path(__file__).resolve().parents[3] / 'artifacts'
        if '..' in path.parts or path.suffix != '.json':
            raise ValueError()
        path = path.absolute()
        relative = path.relative_to(root)
        current = root
        if root.is_symlink():
            raise ValueError()
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError()
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 65536:
            raise ValueError()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as handle:
            if stamp(os.fstat(handle.fileno())) != stamp(before):
                raise ValueError()
            raw = handle.read(65537)
            after_read = os.fstat(handle.fileno())
        if stamp(after_read) != stamp(before) or stamp(path.lstat()) != stamp(before) or len(raw) != before.st_size:
            raise ValueError()
        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid(value):
            raise ValueError()
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
        names = {field.name for field in fields(ModelProfile)}
        if type(data) is not dict or not names - {'schema_version'} <= set(data) <= names:
            raise ValueError()
        capabilities = data['capabilities']
        if type(capabilities) is not dict or set(capabilities) != {field.name for field in fields(ProviderCapabilities)}:
            raise ValueError()
        data['capabilities'] = ProviderCapabilities(**capabilities)
        return validate_profile(ModelProfile(**data))
    except ProviderError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ProviderError('model_profile_invalid') from None
