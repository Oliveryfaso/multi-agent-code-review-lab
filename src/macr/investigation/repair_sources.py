"""Read an explicit committed source subset without inventorying the raw repo.

Unlisted files are never read; source or dependency completeness is not inferred.
The selected bytes retain the existing bounded repair syntax/snapshot profile.
"""
from __future__ import annotations

from hashlib import sha256
import re
from unicodedata import normalize

from .records import ScopeProfile
from .repair_edits import (MAX_FILES, MAX_FILE_BYTES, MAX_PATH_BYTES, MAX_TOTAL_BYTES,
    MAX_TOTAL_LINES, MAX_FULL_FILE_BYTES, MAX_FULL_TOTAL_BYTES, MAX_FULL_TOTAL_LINES, validate_files)
from .repair_io import _root, utf8_size
from .repair_windows import _physical_lines
from .scope import exclusion, read_safe
from .validation import ContractError, validate_relative


MAX_SOURCE_ENTRIES = 64


def _require(ok, field):
    if not ok:
        raise ContractError('repair_sources_invalid', field)


def source_view(mode):
    """Describe the opt-in source view without granting broader read authority."""
    if mode is None: return None
    _require(type(mode) is str and mode == 'declared_files', 'source_scope')
    return {'mode': 'declared_files', 'coverage': 'partial_repository',
            'unlisted_files': 'not_read', 'dependency_completeness': 'not_verified'}


def _commitments(expected_hashes):
    _require(type(expected_hashes) is dict and 1 <= len(expected_hashes) <= MAX_FILES, 'source_commitment')
    expected = dict(expected_hashes)
    for path, digest in expected.items():
        _require(type(path) is str and utf8_size(path) <= MAX_PATH_BYTES and path.endswith('.py'), 'source_path')
        validate_relative(path)
        _require(path == normalize('NFC', path), 'source_path_normalization')
        _require(not exclusion(path, ScopeProfile()), 'source_reserved_path')
        _require(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest), 'source_commitment')
    files = set(); directories = {}
    for path in expected:
        parts = path.split('/')
        for end in range(1, len(parts)):
            spelling = '/'.join(parts[:end])
            key = normalize('NFC', spelling.casefold())
            _require(key not in directories or directories[key] == spelling, 'source_directory_alias')
            directories[key] = spelling
        folded = normalize('NFC', path.casefold())
        _require(folded not in files and all(not folded.startswith(other + '/')
                 and not other.startswith(folded + '/') for other in files), 'source_path_collision')
        files.add(folded)
    _require(len(files) + len(directories) <= MAX_SOURCE_ENTRIES, 'source_entry_budget')
    return expected


def read_committed_sources(root, expected_hashes, *, full_originals=False):
    """Validate all authority first, then read and recheck only its exact files."""
    _require(type(full_originals) is bool, 'source_profile')
    expected = _commitments(expected_hashes)
    file_limit = MAX_FULL_FILE_BYTES if full_originals else MAX_FILE_BYTES
    total_limit = MAX_FULL_TOTAL_BYTES if full_originals else MAX_TOTAL_BYTES
    line_limit = MAX_FULL_TOTAL_LINES if full_originals else MAX_TOTAL_LINES
    root = _root(root)
    files = {}; total_bytes = total_lines = 0
    for path in sorted(expected):
        body = read_safe(root, path, min(file_limit, total_limit - total_bytes))
        total_bytes += len(body)
        total_lines += len(_physical_lines(body) if full_originals else body.splitlines())
        _require(total_bytes <= total_limit, 'source_total_size')
        _require(total_lines <= line_limit, 'source_total_lines')
        _require(sha256(body).hexdigest() == expected[path], 'source_digest_mismatch')
        files[path] = body
    validate_files(files, full_originals=full_originals)
    return files
