"""Pure, bounded source edits; parsing/compilation never executes source."""
from .repair_io import _syntax
from .validation import ContractError, validate_relative


MAX_FILES = 32
MAX_FILE_BYTES = 16 * 1024
MAX_TOTAL_BYTES = 64 * 1024
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


def validate_files(files: dict[str, bytes]) -> None:
    """Validate the entire bounded source set without changing or executing it."""
    _require(type(files) is dict and 1 <= len(files) <= MAX_FILES, 'source_inventory')
    total = 0
    # Complete size/shape checks precede parsing or copying any source content.
    for name, body in files.items():
        _path(name)
        _require(type(body) is bytes and len(body) <= MAX_FILE_BYTES, 'source_size')
        total += len(body)
        _require(total <= MAX_TOTAL_BYTES, 'source_total_size')
    for name, body in files.items():
        _syntax(body, name)


def apply_edits(files: dict[str, bytes], edits: list[dict], *,
                editable_paths: list[str]) -> dict[str, bytes]:
    """Return new source bytes for 1–3 explicit edits; never modify input files.

    Editable paths are caller authority, not inferred from filenames. All other
    source, including tests excluded from that list, stays byte-identical.
    """
    validate_files(files)
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
            _require(type(value) is str and 0 < len(value) <= MAX_FILE_BYTES,
                     'edit_text')
            _require(_utf8_size(value) <= MAX_FILE_BYTES and value.strip(),
                     'edit_text')
        _require(edit['old'] != edit['new'], 'edit_noop')
    result = dict(files)
    for edit in edits:
        name = edit['file']
        text = result[name].decode('utf-8')
        _require(text.count(edit['old']) == 1, 'edit_match')
        result[name] = text.replace(edit['old'], edit['new'], 1).encode('utf-8')
        validate_files(result)
    return result
