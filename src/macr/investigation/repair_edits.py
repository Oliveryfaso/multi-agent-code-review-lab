"""Pure, bounded source edits; parsing/compilation never executes source."""
from .repair_io import _syntax
from .repair_windows import _physical_lines, project_source_windows, validate_visible_edits
from .validation import ContractError, validate_relative


MAX_FILES = 32
MAX_FILE_BYTES = 16 * 1024
MAX_TOTAL_BYTES = 64 * 1024
MAX_TOTAL_LINES = 4096
MAX_FULL_FILE_BYTES = 128 * 1024
MAX_FULL_TOTAL_BYTES = 512 * 1024
MAX_FULL_TOTAL_LINES = 16384
MAX_PATH_BYTES = 4096


def _require(condition, detail):
    if not condition:
        raise ContractError('repair_edits_invalid', detail)


def _utf8_size(value):
    try:
        return len(value.encode('utf-8'))
    except UnicodeError:
        raise ContractError('repair_edits_invalid', 'text_encoding') from None


def _path(value):
    _require(type(value) is str and len(value) <= MAX_PATH_BYTES, 'source_path')
    _require(_utf8_size(value) <= MAX_PATH_BYTES, 'source_path')
    _require(validate_relative(value).endswith('.py'), 'source_path')
    return value


def validate_files(files: dict[str, bytes], *, full_originals=False) -> None:
    """Validate the entire bounded source set without changing or executing it."""
    _require(type(full_originals) is bool, 'source_profile')
    _require(type(files) is dict and 1 <= len(files) <= MAX_FILES, 'source_inventory')
    file_limit = MAX_FULL_FILE_BYTES if full_originals else MAX_FILE_BYTES
    total_limit = MAX_FULL_TOTAL_BYTES if full_originals else MAX_TOTAL_BYTES
    line_limit = MAX_FULL_TOTAL_LINES if full_originals else MAX_TOTAL_LINES
    total = lines = 0
    # Complete size/shape checks precede parsing or copying any source content.
    for name, body in files.items():
        _path(name)
        _require(type(body) is bytes and len(body) <= file_limit, 'source_size')
        total += len(body)
        _require(total <= total_limit, 'source_total_size')
        lines += len(_physical_lines(body) if full_originals else body.splitlines())
        _require(lines <= line_limit, 'source_total_lines')
    for name, body in files.items():
        if full_originals: _syntax(body, name, full_originals=True)
        else: _syntax(body, name)


def apply_edits(files: dict[str, bytes], edits: list[dict], *,
                editable_paths: list[str], full_originals=False,
                source_windows=None) -> dict[str, bytes]:
    """Return new source bytes for 1–3 explicit edits; never modify input files.

    Editable paths are caller authority, not inferred from filenames. All other
    source, including tests excluded from that list, stays byte-identical.
    """
    validate_files(files, full_originals=full_originals)
    _require(full_originals == (source_windows is not None), 'source_window_profile')
    text_limit = MAX_FILE_BYTES
    _require(type(editable_paths) is list and 1 <= len(editable_paths) <= len(files),
             'editable_scope')
    editable = set()
    for name in editable_paths:
        _path(name)
        _require(name in files and name not in editable, 'editable_scope')
        editable.add(name)
    _require(type(edits) is list and 1 <= len(edits) <= 3, 'edit_count')
    seen = set()
    for edit in edits:
        _require(type(edit) is dict and set(edit) == {'file', 'old', 'new'}, 'edit_shape')
        name = _path(edit['file'])
        _require(name in editable and name not in seen, 'edit_scope')
        seen.add(name)
        for field in ('old', 'new'):
            value = edit[field]
            _require(type(value) is str and 0 < len(value) <= text_limit,
                     'edit_text')
            _require(_utf8_size(value) <= text_limit and value.strip(),
                     'edit_text')
        _require(edit['old'] != edit['new'], 'edit_noop')
    if full_originals: validate_visible_edits(files, edits, source_windows)
    result = dict(files)
    for edit in edits:
        name = edit['file']
        text = result[name].decode('utf-8')
        _require(text.count(edit['old']) == 1, 'edit_match')
        result[name] = text.replace(edit['old'], edit['new'], 1).encode('utf-8')
        validate_files(result, full_originals=full_originals)
    if full_originals: project_source_windows(result, source_windows)
    return result
