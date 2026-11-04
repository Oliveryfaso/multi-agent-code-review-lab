"""Inventory caller-supplied BugsInPy paths, without fetching their blobs.

Only explicitly mapped neutral tasks enter the existing repair input loader.
Input admission and runtime calibration remain separate observations.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re

from macr.investigation.repair_edits import MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from macr.investigation.repair_entry import load_task
from macr.investigation.repair_io import _test_names, bounded_json, encode_bounded, load_json
from macr.investigation.repair_loop import PROFILE, output_contract
from macr.investigation.scope import read_safe, safe_parent, validate_workspace_path
from macr.investigation.validation import ContractError, validate_relative


JSON_LIMIT = 4 * 1024 * 1024
STATES = ('input_compatible', 'input_unsupported', 'not_assessed')


def _require(ok, field):
    if not ok:
        raise ContractError('bugsinpy_inventory_invalid', field)


def _hash(value):
    return sha256(encode_bounded(value, JSON_LIMIT)).hexdigest()


def _safe_error(error):
    # Loader errors identify contract fields; never retain exception text/paths.
    return {name: value if type(value) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value)
            else 'input_invalid' for name, value in (('code', error.code), ('field', error.field))}


def probe_test_sources(test_paths, sources, *, origin):
    """Probe only supplied public test bytes; never load a repo or construct a task.

    Totals are lower bounds for the unassessed whole source snapshot. Accepted
    declarations establish neither selector equivalence nor actual collection.
    """
    _require(type(origin) is str and origin in ('fixed_revision_overlay', 'buggy_revision_probe_only'),
             'test_source_origin')
    _require(type(test_paths) is list and all(type(path) is str for path in test_paths)
             and len(set(test_paths)) == len(test_paths), 'probe_test_paths')
    _require(type(sources) is dict and all(type(path) is str for path in sources)
             and set(sources) <= set(test_paths)
             and all(type(body) is bytes for body in sources.values()), 'probe_test_sources')
    rows = []
    for path in test_paths:
        row = {'path': path, 'bytes': None, 'lines': None, 'content_sha256': None,
               'declared_test_count': None, 'state': 'missing', 'reasons': []}
        if path in sources:
            body = sources[path]
            row.update(bytes=len(body), lines=len(body.splitlines()), content_sha256=sha256(body).hexdigest())
        else:
            row['reasons'].append('test_source_missing')
        try:
            validate_relative(path)
            _require(len(path.encode('utf-8')) <= 4096, 'source_path')
        except (ContractError, UnicodeError) as error:
            row['state'] = 'unsupported'
            row['reasons'].append('test_path_invalid')
            row['error'] = (_safe_error(error) if isinstance(error, ContractError)
                            else {'code': 'bugsinpy_inventory_invalid', 'field': 'source_path'})
        else:
            if not path.endswith('.py'):
                row['state'] = 'unsupported'
                row['reasons'].append('test_path_not_python_file')
            elif path in sources:
                try:
                    row['declared_test_count'] = len(_test_names(sources[path]))
                except ContractError as error:
                    row['state'] = 'unsupported'
                    row['reasons'].append('declared_test_file_rejected')
                    row['error'] = _safe_error(error)
                else:
                    row['state'] = 'accepted_by_static_profile'
        rows.append(row)
    observed_bytes = sum(row['bytes'] or 0 for row in rows)
    observed_lines = sum(row['lines'] or 0 for row in rows)
    declared = sum(row['declared_test_count'] or 0 for row in rows)
    reasons = []
    for blocked, reason in ((not test_paths, 'test_paths_empty'),
            (len(test_paths) > 8, 'test_file_count_exceeded'),
            (declared > 32, 'declared_test_count_exceeded'),
            (observed_bytes > MAX_TOTAL_BYTES, 'test_subset_total_bytes_exceeded'),
            (observed_lines > PROFILE.max_python_lines, 'test_subset_total_lines_exceeded'),
            (any(row['state'] == 'unsupported' for row in rows), 'declared_test_file_rejected')):
        if blocked: reasons.append(reason)
    coverage_complete = len(sources) == len(test_paths)
    state = 'unsupported' if reasons else 'accepted_by_static_profile' if coverage_complete else 'incomplete'
    if not coverage_complete: reasons.append('test_sources_missing')
    return {'schema': 'macr.bugsinpy.test-source-probe.v1', 'origin': origin,
            'assessment_scope': 'declared_test_files_only', 'coverage_complete': coverage_complete,
            'state': state, 'reasons': reasons, 'test_file_count': len(test_paths),
            'observed_bytes': observed_bytes, 'observed_lines': observed_lines,
            'known_declared_test_count': declared, 'rows': rows, 'runtime_state': 'uncalibrated',
            'whole_source_snapshot': 'not_assessed', 'selector_equivalence': 'not_assessed',
            'target_code_executed': False}


def build_inventory(index, *, revision, task_map=None):
    """Keep every indexed bug; directory presence does not establish source access.

    ``revision`` is the caller's explicit commit pin, not a verified relationship
    to a tree-object SHA. Explicit derived projections carry no tree-object SHA.
    Task-map keys use ``project/number``.
    """
    _require(type(revision) is str and re.fullmatch(r'[0-9a-f]{40}', revision), 'revision')
    bounded_json(index, max_bytes=JSON_LIMIT, max_depth=12, utf8_bytes=True)
    _require(type(index) is dict and index.get('truncated') is False
             and type(index.get('tree')) is list, 'recursive_tree')
    projection = index.get('kind') == 'derived_path_projection'
    if projection:
        _require(set(index) == {'schema', 'kind', 'revision', 'raw_response_retained', 'truncated', 'tree'}
                 and index['schema'] == 'macr.bugsinpy.path-projection.v1'
                 and index['revision'] == revision and index['raw_response_retained'] is False,
                 'path_projection')
        source = {'kind': 'caller_provided_derived_path_projection', 'tree_sha': None,
                  'raw_response_retained': False}
    else:
        _require('kind' not in index and type(index.get('sha')) is str
                 and re.fullmatch(r'[0-9a-f]{40}', index['sha']), 'recursive_tree')
        source = {'kind': 'caller_provided_github_recursive_tree', 'tree_sha': index['sha']}
    paths = {}; bugs = set()
    for entry in index['tree']:
        _require(type(entry) is dict and {'path', 'type'} <= set(entry)
                 and (not projection or set(entry) == {'path', 'type'})
                 and type(entry['path']) is str and len(entry['path'].encode('utf-8')) <= 4096
                 and entry['type'] in ('blob', 'tree', 'commit'), 'tree_entry')
        path = validate_relative(entry['path'])
        _require(path not in paths, 'duplicate_index_path')
        paths[path] = entry['type']
        parts = path.split('/')
        if (len(parts) >= 4 and parts[0] == 'projects' and parts[2] == 'bugs'
                and parts[3].isascii() and parts[3].isdigit()):
            bugs.add((parts[1], parts[3]))
    task_map = {} if task_map is None else task_map
    bounded_json(task_map, max_bytes=JSON_LIMIT, max_depth=4, utf8_bytes=True)
    _require(type(task_map) is dict and set(task_map) <= {p + '/' + n for p, n in bugs}, 'task_map_ids')
    for mapping in task_map.values():
        _require(type(mapping) is dict and set(mapping) == {'repo', 'task'}
                 and all(type(value) is str and 0 < len(value.encode('utf-8')) <= 4096
                         for value in mapping.values()), 'task_map_entry')
    rows = []
    for project, number in sorted(bugs, key=lambda item: (item[0], int(item[1]), item[1])):
        bug_id = project + '/' + number
        prefix = f'projects/{project}/bugs/{number}'
        metadata = {name: paths.get(prefix + '/' + name) == 'blob'
                    for name in ('bug.info', 'run_test.sh')}
        row = {'id': bug_id, 'project': project, 'bug_number': number,
               'metadata': metadata, 'input_state': 'not_assessed',
               'runtime_state': 'uncalibrated', 'reasons': []}
        for name, reason in (('bug.info', 'missing_bug_info'), ('run_test.sh', 'missing_run_test_sh')):
            if not metadata[name]: row['reasons'].append(reason)
        if bug_id not in task_map:
            row['reasons'].append('neutral_task_not_supplied')
        else:
            mapping = task_map[bug_id]
            try:
                task=load_task(mapping['repo'], mapping['task'])
            except ContractError as error:
                row.update(input_state='input_unsupported', input_error=_safe_error(error))
                row['reasons'].append('neutral_task_rejected')
            except (OSError, ValueError, TypeError, RuntimeError):
                row['input_state'] = 'input_unsupported'
                row['reasons'].append('neutral_task_rejected')
                row['input_error'] = {'code': 'repair_input_unavailable', 'field': 'input'}
            else:
                row['input_state'] = 'input_compatible'
                row['reasons'].append('neutral_task_accepted')
                if task.get('source_scope') is not None:
                    from macr.investigation.repair_sources import source_view
                    row['source_view']=source_view(task['source_scope'])
        rows.append(row)
    return {'schema': 'macr.bugsinpy.inventory.v1',
        'source': dict(source, revision=revision, index_sha256=_hash(index), remote_verified=False),
        'task_map_sha256': _hash(task_map), 'bug_count': len(rows),
        'project_count': len({row['project'] for row in rows}),
        'input_states': {state: sum(row['input_state'] == state for row in rows) for state in STATES},
        'runtime_state': 'uncalibrated', 'target_code_executed': False,
        'repair_capacity': {'max_source_files': MAX_FILES, 'max_file_bytes': MAX_FILE_BYTES,
                            'max_total_source_bytes': MAX_TOTAL_BYTES,
                            'max_python_lines': PROFILE.max_python_lines,
                            'max_edits_per_proposal': output_contract()['max_edits']},
        'rows': rows}


def _read_json(path):
    path = Path(path).absolute()
    validate_workspace_path(path, output=True)
    return load_json(read_safe(path.parent, path.name, JSON_LIMIT).decode('utf-8'),
                     max_bytes=JSON_LIMIT, max_depth=12, utf8_bytes=True)


def _write_exclusive(path, report):
    path = Path(path).absolute()
    validate_workspace_path(path, output=True)
    data = encode_bounded(report, JSON_LIMIT - 1) + b'\n'
    with safe_parent(path.parent, path.name) as (parent, leaf):
        try:
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        except FileExistsError:
            raise ContractError('bugsinpy_inventory_invalid', 'output_exists') from None
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)


def run(args):
    """CLI adapter: full report in one new file; bounded summaries on stdout."""
    try:
        index = _read_json(args.index)
        task_map = _read_json(args.task_map) if getattr(args, 'task_map', None) is not None else None
        report = build_inventory(index, revision=args.revision, task_map=task_map)
        if task_map:
            out = Path(args.out).resolve()
            _require(all(not out.is_relative_to(Path(mapping['repo']).resolve())
                         for mapping in task_map.values()), 'output_source_overlap')
        _write_exclusive(args.out, report)
        summary = {key: report[key] for key in ('schema', 'bug_count', 'project_count', 'input_states',
                                               'runtime_state', 'target_code_executed')}
        summary['status'] = 'recorded'
        print(json.dumps(summary, sort_keys=True))
        return 0
    except ContractError as error:
        failure = _safe_error(error)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, EOFError, KeyboardInterrupt):
        failure = {'code': 'bugsinpy_inventory_invalid', 'field': 'input_or_output'}
    print(json.dumps({'status': 'rejected', 'error': failure['code'], 'field': failure['field'],
                      'target_code_executed': False}, sort_keys=True))
    return 2
