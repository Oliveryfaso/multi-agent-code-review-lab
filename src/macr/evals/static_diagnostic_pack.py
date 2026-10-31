"""Strict new static-diagnostic packages, without gold or source execution."""
from __future__ import annotations

from collections import Counter
import re

from macr.evals.deepseek_policy_manifest import _read, _relative, _root, _task
from macr.evals.deepseek_policy_tools import _tree, StaticEvidenceError
from macr.evals.deepseek_study_profile import (
    TASK_FIELDS as LEGACY_TASK_FIELDS, _ID, _inventory, digest, read_json, require, StudyError)
from macr.investigation.source_text import SourceText
from macr.investigation.validation import ContractError

PACK_SCHEMA = 'macr.static-diagnostic.pack.v1'
TASK_FIELDS = frozenset(LEGACY_TASK_FIELDS | {'entries'})
_DEV_ROOTS = {'keyword-defect', 'keyword-healthy', 'dynamic'}
_DEV_IDS = {'dev-defect', 'dev-healthy', 'dev-dynamic'}
_SYMBOL = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z', re.ASCII)


def _text_bytes(text, limit):
    try:
        require(type(text) is str and len(text.encode('utf-8')) <= limit,
                'static_pack_text_invalid')
    except UnicodeError:
        raise StudyError('static_pack_text_invalid') from None


def _validate_task(task, *, development):
    require(type(task) is dict and set(task) == TASK_FIELDS, 'static_pack_task_fields_invalid')
    for name in ('id', 'project_id'):
        require(type(task[name]) is str and _ID.fullmatch(task[name]), 'static_pack_id_invalid')
    _relative(task['snapshot_root'])
    if development:
        require(task['snapshot_root'] in _DEV_ROOTS and task['id'] in _DEV_IDS,
                'static_pack_development_task_invalid')
    # Reuse legacy task field validation while keeping this new raw shape intact.
    legacy = {key: value for key, value in task.items()
              if key not in {'project_id', 'license', 'entries'}}
    legacy.update(stratum='full_context', evidence_parity='equal')
    if development:
        legacy['snapshot_root'] = 'snapshots/' + task['snapshot_root']
    _task(legacy)
    for name in ('question', 'scenario'):
        _text_bytes(task[name], 4096)
    for assumption in task['assumptions']:
        _text_bytes(assumption, 4096)
    license = task['license']
    require(type(license) is dict and set(license) == {'spdx', 'provenance'}
        and all(type(value) is str and 0 < len(value.strip()) <= 1000
                for value in license.values()), 'license_declaration_required')
    files = task['authorized_files']
    require((1 if development else 2) <= len(files) <= 4
        and all(name.endswith('.py') for name in files), 'static_pack_source_count_invalid')
    entries = task['entries']
    require(type(entries) is list and 1 <= len(entries) <= 6
        and all(type(entry) is str for entry in entries), 'static_pack_entries_invalid')
    require(len(entries) == len(set(entries)), 'static_pack_entries_invalid')
    for entry in entries:
        _text_bytes(entry, 256)
        require(entry.count(':') == 1, 'static_pack_entries_invalid')
        file, symbol = entry.split(':')
        _relative(file)
        require(file in files and len(symbol) <= 128 and _SYMBOL.fullmatch(symbol),
                'static_pack_entries_invalid')
    first_file, first_symbol = entries[0].split(':')
    require(task['entrypoint'] == {'file': first_file, 'symbol': first_symbol},
            'static_pack_entrypoint_mismatch')


def load_pack(root, *, development=False):
    """Return the unchanged validated neutral map; no grading data is opened."""
    require(type(development) is bool, 'static_pack_development_flag_invalid')
    root = _root(root)
    pack = read_json(root, 'pack.json', 65536)
    require(type(pack) is dict and set(pack) == {'schema', 'pack_id', 'tasks'}
        and pack['schema'] == PACK_SCHEMA and type(pack['pack_id']) is str
        and _ID.fullmatch(pack['pack_id']), 'static_pack_invalid')
    tasks = pack['tasks']
    require(type(tasks) is list and len(tasks) == (3 if development else 9),
            'static_pack_task_count_invalid')
    ids, roots, allowed = set(), set(), {'pack.json'}
    for task in tasks:
        _validate_task(task, development=development)
        require(task['id'] not in ids and task['snapshot_root'] not in roots,
                'static_pack_duplicate_task_or_root')
        ids.add(task['id']); roots.add(task['snapshot_root'])
        allowed.update(task['snapshot_root'] + '/' + name for name in task['authorized_files'])
    projects = Counter(task['project_id'] for task in tasks)
    require(len(projects) == 3 and set(projects.values()) == ({1} if development else {3}),
            'static_pack_project_grid_invalid')
    if development:
        require(ids == _DEV_IDS and roots == _DEV_ROOTS, 'static_pack_development_grid_invalid')
        if (root / 'README.md').exists():
            allowed.add('README.md')
    # Names and links are rejected before any declared source body is opened.
    _inventory(root, allowed)
    total = 0
    for task in tasks:
        task_bytes = 0
        for name in task['authorized_files']:
            data = _read(root, task['snapshot_root'] + '/' + name, 4097)
            task_bytes += len(data)
            require(bool(data) and task_bytes <= 4096, 'static_pack_source_limit')
            try:
                decoded = SourceText.from_bytes(data, name)
                _tree(name, decoded.text)
            except (ContractError, StaticEvidenceError):
                raise StudyError('static_pack_python_source_invalid') from None
        total += task_bytes
    require(total <= 36864, 'static_pack_total_source_limit')
    digest(pack)  # Validate canonical UTF-8, including escaped-surrogate metadata.
    return pack
