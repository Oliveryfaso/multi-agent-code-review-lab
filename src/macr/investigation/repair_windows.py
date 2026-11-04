"""Truthful, bounded model windows over complete current source bytes.

Only physical LF, CRLF and CR delimit lines. Offsets and digests retain every
original byte; parsing/compilation never imports or executes target source.
This module depends only on I/O/validation primitives to avoid runtime cycles.
"""
from __future__ import annotations

from hashlib import sha256
import re

from .repair_io import _syntax, encode_bounded, utf8_size
from .validation import ContractError, validate_relative


SELECTION_SCHEMA = 'macr.repair.source-windows.v1'
PROJECTION_SCHEMA = 'macr.repair.source-window-projection.v1'
VISIBLE_SELECTION_SCHEMA = 'macr.repair.source-windows.v2'
VISIBLE_PROJECTION_SCHEMA = 'macr.repair.source-window-projection.v2'
VISIBLE_EDIT_POLICY = 'unique_shown_span'
PROJECTION_SELECTION_SCHEMAS = {PROJECTION_SCHEMA: SELECTION_SCHEMA,
                                VISIBLE_PROJECTION_SCHEMA: VISIBLE_SELECTION_SCHEMA}
MAX_WINDOWS = 32
MAX_WINDOWS_PER_FILE = 8
MAX_SHOWN_LINES = 256
MAX_SHOWN_BYTES = 16384
CONTEXT_BYTES = 1024 * 1024


def _require(ok, field):
    if not ok:
        raise ContractError('repair_windows_invalid', field)


def _path(value):
    _require(type(value) is str and utf8_size(value) <= 4096, 'source_path')
    _require(validate_relative(value).endswith('.py'), 'source_path')
    return value


def _physical_lines(body):
    """Return raw physical lines with exact terminators and no phantom EOF line."""
    return re.findall(rb'[^\r\n]*(?:\r\n|\r|\n|$)', body)[:-1]


def _full_files(files):
    _require(type(files) is dict and 1 <= len(files) <= 32, 'source_inventory')
    total_bytes = total_lines = 0
    for path, body in files.items():
        _path(path)
        _require(type(body) is bytes and len(body) <= 128 * 1024, 'source_size')
        total_bytes += len(body); total_lines += len(_physical_lines(body))
        _require(total_bytes <= 512 * 1024, 'source_total_size')
        _require(total_lines <= 16384, 'source_total_lines')
    for path, body in files.items():
        _syntax(body, path, full_originals=True)


def validate_selection_shape(selection, expected_paths=None):
    """Validate canonical inclusive current-line ranges, never normalize them."""
    _require(type(selection) is dict and set(selection) == {'schema', 'files'}
             and selection['schema'] in (SELECTION_SCHEMA, VISIBLE_SELECTION_SCHEMA), 'selection_shape')
    chosen = selection['files']
    _require(type(chosen) is dict and 1 <= len(chosen) <= 32, 'selection_files')
    expected = None
    if expected_paths is not None:
        _require(type(expected_paths) in (dict, list, tuple, set, frozenset), 'expected_paths')
        _require(1 <= len(expected_paths) <= 32, 'expected_paths')
        expected = {_path(path) for path in expected_paths}
        _require(len(expected) == len(expected_paths), 'expected_paths')
    windows = shown_lines = 0
    for path, ranges in chosen.items():
        _path(path)
        _require(expected is None or path in expected, 'selection_scope')
        _require(type(ranges) is list and 1 <= len(ranges) <= MAX_WINDOWS_PER_FILE, 'selection_ranges')
        previous = -1
        for entry in ranges:
            _require(type(entry) is dict and set(entry) == {'start_line', 'end_line'}, 'selection_range')
            start, end = entry['start_line'], entry['end_line']
            _require(type(start) is int and type(end) is int and 1 <= start <= end <= 16384,
                     'selection_range')
            _require(start > previous + 1, 'selection_range_order')
            previous = end
            windows += 1; shown_lines += end - start + 1
            _require(windows <= MAX_WINDOWS and shown_lines <= MAX_SHOWN_LINES, 'selection_budget')
    _require(windows > 0, 'selection_empty')
    return selection


def project_source_windows(files, selection, protected_paths=()):
    """Expose exact bounded windows plus explicit omissions for every full file."""
    _full_files(files)
    validate_selection_shape(selection, files)
    _require(type(protected_paths) in (list, tuple, set, frozenset)
             and len(protected_paths) <= len(files), 'protected_paths')
    protected = set()
    for path in protected_paths:
        _path(path)
        _require(path in files and path not in protected, 'protected_paths')
        protected.add(path)
        # Public test handling deliberately retains the legacy parser profile.
        _syntax(files[path], path)
    result = {}; total_shown_bytes = 0
    for path, body in sorted(files.items()):
        lines = _physical_lines(body); windows = []; omitted = []; cursor = 1
        for entry in selection['files'].get(path, []):
            start, end = entry['start_line'], entry['end_line']
            _require(end <= len(lines), 'selection_current_lines')
            raw = b''.join(lines[start - 1:end])
            total_shown_bytes += len(raw)
            _require(total_shown_bytes <= MAX_SHOWN_BYTES, 'projection_bytes')
            if cursor < start: omitted.append({'start_line': cursor, 'end_line': start - 1})
            windows.append({'start_line': start, 'end_line': end,
                'line_numbers': list(range(start, end + 1)), 'text': raw.decode('utf-8'),
                'sha256': sha256(raw).hexdigest()})
            cursor = end + 1
        if cursor <= len(lines): omitted.append({'start_line': cursor, 'end_line': len(lines)})
        _require(path not in protected or not omitted, 'protected_file_visibility')
        result[path] = {'sha256': sha256(body).hexdigest(), 'bytes': len(body),
            'line_count': len(lines), 'windows': windows, 'omitted_ranges': omitted}
    if selection['schema'] == VISIBLE_SELECTION_SCHEMA:
        return {'schema': VISIBLE_PROJECTION_SCHEMA, 'edit_policy': VISIBLE_EDIT_POLICY, 'files': result}
    return {'schema': PROJECTION_SCHEMA, 'files': result}


def resolve_visible_edits(files, edits, selection):
    """Validate edits and return their unique current full-source byte spans."""
    projection = project_source_windows(files, selection)
    _require(type(edits) is list and 1 <= len(edits) <= 3, 'edit_count')
    seen = set(); spans = {}
    for edit in edits:
        _require(type(edit) is dict and set(edit) == {'file', 'old', 'new'}, 'edit_shape')
        path = _path(edit['file'])
        _require(path in files and path not in seen, 'edit_scope')
        seen.add(path)
        for key in ('old', 'new'):
            value = edit[key]
            _require(type(value) is str and bool(value.strip())
                     and utf8_size(value) <= 16384, 'edit_text')
        old = edit['old']; needle = old.encode('utf-8')
        if selection['schema'] == SELECTION_SCHEMA:
            text = files[path].decode('utf-8'); first = text.find(old)
            _require(first >= 0 and first == text.rfind(old), 'edit_match')
            _require(old != edit['new'], 'edit_noop')
            _require(any(old in window['text'] for window in projection['files'][path]['windows']),
                     'edit_visibility')
            start = len(text[:first].encode('utf-8'))
            spans[path] = (start, start + len(needle))
        else:
            _require(old != edit['new'], 'edit_noop')
            lines = _physical_lines(files[path]); matches = []
            for window in projection['files'][path]['windows']:
                base = sum(len(line) for line in lines[:window['start_line'] - 1])
                raw = window['text'].encode('utf-8'); offset = raw.find(needle)
                while offset >= 0:
                    matches.append((base + offset, base + offset + len(needle)))
                    _require(len(matches) <= 1, 'edit_match')
                    # Overlapping matches are ambiguous too.
                    offset = raw.find(needle, offset + 1)
            _require(len(matches) == 1, 'edit_match')
            spans[path] = matches[0]
    return spans


def validate_visible_edits(files, edits, selection):
    """Validate the explicitly selected matching policy without applying edits."""
    resolve_visible_edits(files, edits, selection)


def windowed_context_id(files, issue_sha256, selection, source_scope='declared_files', test_plan=None):
    """Commit full hashes, issue hash and exact current projection canonically."""
    _require(type(issue_sha256) is str and re.fullmatch(r'[0-9a-f]{64}', issue_sha256), 'issue_sha256')
    _require(type(source_scope) is str and source_scope == 'declared_files', 'source_scope')
    projection = project_source_windows(files, selection)
    value = {'schema': 'macr.repair.windowed-initial-context.v1',
        'source_files': {path: sha256(body).hexdigest() for path, body in sorted(files.items())},
        'issue_sha256': issue_sha256, 'source_windows': projection,
        'source_scope': source_scope, 'test_plan': test_plan}
    return sha256(encode_bounded(value, CONTEXT_BYTES)).hexdigest()


def _merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1] + 1:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def propose_source_windows(files, candidates):
    """Greedily propose windows from already verified ranked locator evidence.

    This accepts in-memory selected bytes/evidence only, never saved packets.
    Complete hashes survive omitted views. Candidates remain advisory.
    """
    _full_files(files)
    _require(type(candidates) is list and len(candidates) <= 32, 'candidates')
    encode_bounded(candidates, 256 * 1024)
    priorities = {'symbol': 0, 'signature': 1, 'exact_phrase': 2, 'body': 3}
    physical = {path: _physical_lines(body) for path, body in files.items()}

    def fits(trial):
        # Files were validated once above. Budget attempts need only ranges and
        # raw byte lengths, not repeated compilation of every complete source.
        validate_selection_shape({'schema': SELECTION_SCHEMA, 'files': trial}, files)
        shown = sum(sum(len(line) for line in physical[path][entry['start_line'] - 1:entry['end_line']])
                    for path, ranges in trial.items() for entry in ranges)
        _require(shown <= MAX_SHOWN_BYTES, 'projection_bytes')

    chosen = {}; gaps = []; seen = set()
    for candidate in candidates:
        _require(type(candidate) is dict, 'candidate')
        path = _path(candidate.get('path'))
        _require(path in files and path not in seen, 'candidate_scope'); seen.add(path)
        body = files[path]; digest = sha256(body).hexdigest(); count = len(physical[path])
        _require(candidate.get('sha256') == digest and type(candidate.get('bytes')) is int
                 and candidate['bytes'] == len(body), 'candidate_binding')
        evidence = candidate.get('evidence')
        _require(type(evidence) is list and len(evidence) <= 4096, 'candidate_evidence')
        positions = []
        for item in evidence:
            _require(type(item) is dict, 'candidate_evidence')
            kind = item.get('kind')
            _require(type(kind) is str, 'candidate_evidence')
            if kind not in priorities: continue
            _require(item.get('file') == path and item.get('source_sha256') == digest
                     and type(item.get('source_bytes')) is int and item['source_bytes'] == len(body)
                     and item.get('position_basis', 'source') == 'source', 'evidence_binding')
            line = item.get('line')
            _require(type(line) is int and 1 <= line <= count, 'evidence_line')
            positions.append((priorities[kind], line))
        if not positions:
            gaps.append({'code': 'header_context_fallback', 'file': path,
                         'reason': 'no_ranked_source_position'})
            if count: positions = [(4, 1)]
        retained = []
        for _, line in sorted(set(positions)):
            ranges = _merge_ranges(retained + [(max(1, line - 6), min(count, line + 6))])
            if len(ranges) > 2: continue
            trial = dict(chosen)
            trial[path] = [{'start_line': start, 'end_line': end} for start, end in ranges]
            try:
                fits(trial)
            except ContractError as error:
                if error.field not in ('selection_budget', 'projection_bytes'): raise
                continue
            chosen = trial; retained = ranges
        unshown = sum(not any(start <= line <= end for start, end in retained)
                      for _, line in sorted(set(positions)))
        if unshown:
            gaps.append({'code': 'source_positions_unshown', 'file': path, 'count': unshown,
                         'reason': 'window_limit_or_view_budget'})
    _require(bool(chosen), 'no_displayable_windows')
    selection = {'schema': SELECTION_SCHEMA, 'files': chosen}
    projection = project_source_windows(files, selection)
    for path in sorted(files):
        if not projection['files'][path]['windows']:
            gaps.append({'code': 'source_file_unshown', 'file': path,
                         'reason': 'no_evidence_or_window_budget'})
    return {'source_files': {path: sha256(body).hexdigest() for path, body in sorted(files.items())},
            'source_windows': selection, 'projection': projection, 'gaps': gaps}
