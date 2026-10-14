"""Pure faithful JSON and safe display rendering; no source reads or diagnostics."""
from __future__ import annotations

import copy
import html
import json
import math
import re
import unicodedata
from pathlib import Path

from .evidence_records import StaticEvidenceReport
from .evidence_budget import BudgetExceeded
from .validation import ContractError, validate_relative

_FRAME = re.compile(r'^\s*File "([^"\r\n]+)", line ([0-9]+)(?:, in ([^\r\n]+))?\s*$')


def _relative_frame_path(path, repo):
    """Shared lexical containment only; reading still needs scope authorization."""
    if Path(path).is_absolute():
        path = Path(path).relative_to(Path(repo).absolute()).as_posix()
    validate_relative(path)
    return path


def _stable_traceback(text, repo):
    lines = text.split('\n')
    for position, line in enumerate(lines):
        match = _FRAME.fullmatch(line.rstrip('\r'))
        if match and Path(match.group(1)).is_absolute():
            try:
                relative = _relative_frame_path(match.group(1), repo)
            except (ValueError, ContractError):
                continue
            start, end = match.span(1)
            lines[position] = line[:start] + relative + line[end:]
    return '\n'.join(lines)


def stable_result(report: StaticEvidenceReport) -> dict:
    """Retain semantic limitations and errors; omit only named volatile metadata."""
    raw = copy.deepcopy(report.to_dict())
    if raw['request']:
        repo = raw['request'].pop('repo')
        raw['request'].pop('out')
        raw['request']['traceback'] = _stable_traceback(raw['request']['traceback'], repo)
    for key in ('run_id', 'started_at', 'finished_at', 'elapsed_seconds', 'worker_pid',
                'snapshot_path', 'closure', 'publication', 'phase'):
        raw['run'].pop(key, None)
    if raw['source_version']:
        ref = raw['source_version'].get('snapshot_ref', {})
        for key in ('snapshot_id', 'root_ref', 'content_manifest_ref'):
            ref.pop(key, None)
    for item in raw['selection'].get('resolved', []):
        if item.get('empty_source'):
            item['empty_source'].pop('snapshot_id', None)
    for item in raw['evidence']:
        item.pop('snapshot_id', None)
    for key in ('max_io_bytes', 'max_metadata_entries', 'max_metadata_bytes', 'max_ast_nodes_per_file',
                'max_ast_depth', 'max_ast_nodes', 'max_symbols', 'max_relations', 'active_seconds',
                'max_allocated_bytes', 'min_free_bytes', 'output_bytes', 'worker_rss_bytes',
                'guard_metadata_entries', 'guard_metadata_bytes', 'elapsed_seconds',
                'final_validation_bytes', 'final_validation_seconds', 'max_worker_rss_bytes',
                'max_control_bytes', 'max_stderr_bytes', 'control_input_written_bytes',
                'control_output_read_bytes', 'stderr_read_bytes'):
        raw['usage'].pop(key, None)
    for key in ('excerpt_read_bytes', 'guard_read_bytes'):
        raw['coverage'].pop(key, None)
    return raw


def render_json(report: StaticEvidenceReport) -> str:
    text = json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + '\n'
    _display_limit(report, text, 'max_report_bytes', 'json_render')
    return text


def _display_limit(report, text, field, stage):
    limit = getattr(report.request.profile, field) if report.request else 4 * 1024 * 1024
    if report.request is None:
        effective = report.limits.get('effective', {})
        if type(effective) is dict and field in effective:
            limit = effective[field]
            if type(limit) not in (int, float) or limit <= 0 or type(limit) is float and not math.isfinite(limit):
                raise ContractError('output_limit_unrepresentable', field)
        elif report.run.get('effective_profile_detail_status') == 'unavailable':
            raise ContractError('output_limit_unrepresentable', field)
    observed = len(text.encode('utf-8'))
    if observed > limit:
        raise BudgetExceeded(field, stage, limit, observed)


def _display(value, *, prose=False):
    text = str(value)
    text = ''.join(f'\\u{ord(char):04x}' if (unicodedata.category(char) in ('Cc', 'Cf', 'Cs')
                   and char not in ('\n', '\r', '\t')) else char for char in text)
    # CR remains in the captured text; display makes it visible rather than overwriting terminal rows.
    text = text.replace('\r', '\\r')
    text = html.escape(text, quote=True)
    if prose:
        text = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', text)
        text = text.replace('\n', '\\n').replace('\t', '\\t')
    return text


def _block(value):
    text = _display(value)
    longest = max((len(match.group()) for match in re.finditer(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}text\n{text}' + ('' if text.endswith('\n') else '\n') + fence + '\n'


def render_markdown(report: StaticEvidenceReport) -> str:
    raw = report.to_dict()
    parts = ['# Static source evidence\n', f"Status: {_display(raw['status'], prose=True)}\n",
             'Diagnosis: not_performed; cause: null; data_flow_proven: false.\n']
    if report.request:
        parts += ['## Reading task\n', _display(report.request.question, prose=True) + '\n']
    parts += ['## Selection\n', _block(json.dumps(raw['selection'], ensure_ascii=True, indent=2))]
    for item in raw['evidence']:
        span = item['actual_span']
        parts += [f"## {_display(item['file'], prose=True)} lines {span['start']}–{span['end']}\n",
                  f"Reference: {_display(item['evidence_id'], prose=True)}; complete_range: {str(item['complete_range']).lower()}; complete_symbol: {str(item['complete_symbol']).lower()}.\n",
                  _block(item['text'])]
    for title, key in (('Observations', 'observations'), ('Coverage', 'coverage'), ('Gaps', 'gaps'),
                       ('Errors', 'errors'), ('Effective limits', 'limits'), ('Usage', 'usage')):
        parts += ['## ' + title + '\n', _block(json.dumps(raw[key], ensure_ascii=True, indent=2))]
    text = '\n'.join(parts)
    _display_limit(report, text, 'max_markdown_bytes', 'markdown_render')
    return text


def render_terminal(report: StaticEvidenceReport) -> str:
    # The same escaped local display remains safe when printed in a terminal.
    return render_markdown(report)
