"""Committed scoring-only test bytes; never parse, import or execute an overlay.

The whole manifest binds every planned task and applies identically to all arms.
These helpers do not expose hidden bodies through a repair or generation API.
"""
from __future__ import annotations

from hashlib import sha256
import re
from unicodedata import normalize

from macr.investigation.records import ScopeProfile
from macr.investigation.repair_edits import MAX_FILES, MAX_PATH_BYTES
from macr.investigation.repair_io import _root, utf8_size
from macr.investigation.scope import exclusion, read_safe
from macr.investigation.validation import ContractError, validate_relative
from .repair_comparison import result_digest, validate_comparison


MAX_OVERLAY_FILE_BYTES = 1024 * 1024
MAX_OVERLAY_TASK_BYTES = 8 * 1024 * 1024
MAX_OVERLAY_FILES = 8


def _require(ok, field):
    if not ok:
        raise ContractError('repair_scoring_overlay_invalid', field)


def _path(path):
    _require(type(path) is str and utf8_size(path) <= MAX_PATH_BYTES and path.endswith('.py'), 'overlay_path')
    validate_relative(path)
    _require(path == normalize('NFC', path), 'overlay_path_normalization')
    _require(not exclusion(path, ScopeProfile()), 'overlay_reserved_path')
    return path


def _declared_path(declared, path):
    # v2 retains reviewed spelling metadata; only canonical path is used for I/O.
    _require(type(declared) is str and utf8_size(declared) <= MAX_PATH_BYTES
             and not declared.startswith('/') and not declared.endswith('/')
             and not any(c in declared for c in '\\\x00\r\n:'), 'overlay_declared_path')
    parts = declared.split('/')
    _require(all(part not in ('.', '..') for part in parts)
             and '/'.join(part for part in parts if part) == path, 'overlay_declared_path')


def _namespace(paths):
    seen = set(); directories = {}
    for path in paths:
        parts = path.split('/')
        for end in range(1, len(parts)):
            spelling = '/'.join(parts[:end])
            key = normalize('NFC', spelling.casefold())
            _require(key not in directories or directories[key] == spelling, 'overlay_directory_alias')
            directories[key] = spelling
        folded = normalize('NFC', path.casefold())
        _require(folded not in seen and all(not folded.startswith(other + '/')
                 and not other.startswith(folded + '/') for other in seen), 'overlay_path_collision')
        seen.add(folded)


def validate_overlay_manifest(manifest, plan):
    """Validate canonical metadata and its pre-generation scoring commitment."""
    try:
        plan = validate_comparison(plan)
    except (ValueError, TypeError, KeyError):
        raise ContractError('repair_scoring_overlay_invalid', 'comparison_plan') from None
    _require(type(manifest) is dict and set(manifest) == {'schema', 'tasks'}
             and manifest['schema'] in ('macr.repair.scoring-overlay.v1', 'macr.repair.scoring-overlay.v2'),
             'overlay_manifest')
    mapped = manifest['schema'] == 'macr.repair.scoring-overlay.v2'
    tasks = manifest['tasks']
    _require(type(tasks) is list and 1 <= len(tasks) <= 32, 'overlay_tasks')
    task_ids = []
    for task in tasks:
        _require(type(task) is dict and set(task) == {'task_id', 'files'}
                 and type(task['task_id']) is str
                 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', task['task_id']), 'overlay_task')
        task_ids.append(task['task_id'])
        files = task['files']
        _require(type(files) is list and 1 <= len(files) <= MAX_OVERLAY_FILES, 'overlay_files')
        paths = []; total = 0
        for entry in files:
            fields = {'path', 'bytes', 'sha256'} | ({'declared_path'} if mapped else set())
            _require(type(entry) is dict and set(entry) == fields, 'overlay_file')
            paths.append(_path(entry['path']))
            if mapped: _declared_path(entry['declared_path'], entry['path'])
            _require(type(entry['bytes']) is int and 1 <= entry['bytes'] <= MAX_OVERLAY_FILE_BYTES
                     and type(entry['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']),
                     'overlay_file_commitment')
            total += entry['bytes']
        _require(paths == sorted(paths) and total <= MAX_OVERLAY_TASK_BYTES, 'overlay_file_order_or_budget')
        _namespace(paths)
    _require(task_ids == sorted(task_ids) and len({task_id.casefold() for task_id in task_ids}) == len(task_ids),
             'overlay_task_order_or_collision')
    _require(set(task_ids) == {task['task_id'] for task in plan['spec']['tasks']}, 'overlay_task_scope')
    _require(result_digest(manifest) == plan['spec']['scoring']['hidden_tests_sha256'], 'overlay_manifest_commitment')
    return manifest


def _authority(paths, original_files, field):
    _require(type(paths) is list and 1 <= len(paths) <= len(original_files)
             and all(type(path) is str and path in original_files for path in paths)
             and len(set(paths)) == len(paths), field)
    return set(paths)


def read_overlay(root, manifest, plan, task_id, *, original_files, protected_paths, editable_paths):
    """Read only the selected task's committed files, preserving all original bytes."""
    validate_overlay_manifest(manifest, plan)
    _require(type(task_id) is str, 'overlay_task_id')
    task = next((task for task in manifest['tasks'] if task['task_id'] == task_id), None)
    _require(task is not None, 'overlay_task_id')
    _require(type(original_files) is dict and 1 <= len(original_files) <= MAX_FILES
             and all(type(path) is str and type(body) is bytes for path, body in original_files.items()),
             'original_files')
    for path in original_files: _path(path)
    protected = _authority(protected_paths, original_files, 'protected_paths')
    editable = _authority(editable_paths, original_files, 'editable_paths')
    _require(not protected & editable, 'overlay_edit_authority')
    paths = [entry['path'] for entry in task['files']]
    _require(all(path not in original_files or path in protected and path not in editable for path in paths),
             'overlay_implementation_overlap')
    _namespace(list(original_files) + [path for path in paths if path not in original_files])
    try:
        root = _root(root)
        result = {}; total = 0
        for entry in task['files']:
            body = read_safe(root, task_id + '/' + entry['path'], entry['bytes'])
            _require(len(body) == entry['bytes'], 'overlay_file_size')
            _require(sha256(body).hexdigest() == entry['sha256'], 'overlay_file_digest')
            total += len(body)
            _require(total <= MAX_OVERLAY_TASK_BYTES, 'overlay_task_bytes')
            result[entry['path']] = body
    except OSError:
        raise ContractError('repair_scoring_overlay_invalid', 'overlay_file_unavailable') from None
    return result
