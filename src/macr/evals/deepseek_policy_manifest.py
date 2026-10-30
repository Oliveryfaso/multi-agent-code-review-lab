"""Frozen neutral policy inputs; no gold loading, source execution, or dispatch."""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.scope import open_directory, read_safe, validate_workspace_path
from macr.investigation.source_text import SourceText
from macr.investigation.validation import ContractError

SCHEMA = 'macr.deepseek.policy.manifest.v1'
PROMPT_VERSION = 'deepseek-policy-contracts-v3'
STUDY_V2_PROMPT_VERSION = 'deepseek-study-8192-v2'
STUDY_EVIDENCE_PROMPT_VERSION = 'deepseek-study-evidence-v1'
STUDY_GAP_PROMPT_VERSION = 'deepseek-study-gap-budget-v1'
STUDY_VALUE_PROMPT_VERSION = 'deepseek-study-value-v1'
READABLE_PROMPT_VERSIONS = frozenset({'deepseek-policy-refs-v2', PROMPT_VERSION,
    STUDY_V2_PROMPT_VERSION, STUDY_EVIDENCE_PROMPT_VERSION, STUDY_GAP_PROMPT_VERSION,
    STUDY_VALUE_PROMPT_VERSION})
ARMS = ['A', 'B', 'C', 'D']
MAX_FILES = 24
MAX_FILE_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_BUDGET = {
    'max_total_input_tokens': 24000, 'max_total_output_tokens': 6000,
    'max_wall_seconds': 180, 'max_tool_reads': 4,
    'max_request_bytes': 131072, 'max_record_bytes': 1048576,
}
TASK_FIELDS = {'id', 'snapshot_root', 'question', 'scenario', 'assumptions',
               'authorized_files', 'initial_evidence_files', 'stratum',
               'evidence_parity', 'entrypoint'}
MANIFEST_FIELDS = {'schema', 'task', 'snapshot', 'model', 'prompt_version', 'arms',
                   'budget', 'repeat', 'manifest_sha256'}
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_FORBIDDEN = {'gold', 'rubric', 'reference', 'references', 'answer', 'answers',
              'expected', 'solution', 'solutions', 'repair', 'repairs', 'feedback',
              'oracle', 'oracles', 'test', 'tests', 'commits', '.git', '.hg',
              '.aws', '.ssh', '.agents', '.codex', 'secret', 'secrets', 'credential', 'credentials'}


class PolicyManifestError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise PolicyManifestError(code)


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PolicyManifestError('policy_json_invalid') from None


def _digest(value):
    return sha256(_encoded(value)).hexdigest()


def _text(value, limit, code='policy_task_invalid'):
    _require(type(value) is str and 0 < len(value.strip()) <= limit, code)
    _require('\x00' not in value, code)


def _identifier(value, code):
    _require(type(value) is str and _IDENTIFIER.fullmatch(value) is not None, code)


def _relative(value):
    _text(value, 512, 'policy_path_invalid')
    parts = value.split('/')
    _require(not PurePosixPath(value).is_absolute() and '\\' not in value
             and all(part not in ('', '.', '..') for part in parts)
             and all(ord(char) >= 32 and ord(char) != 127 for char in value),
             'policy_path_invalid')
    for part in parts:
        lower = part.casefold()
        words = set(re.split(r'[^a-z0-9]+', lower))
        _require(lower not in _FORBIDDEN and not words.intersection(_FORBIDDEN)
                 and not lower.startswith(('._', '.env', 'test_', 'id_rsa', 'id_ed25519'))
                 and not lower.endswith(('_test.py', '.pem', '.key')),
                 'policy_answer_or_private_path')
    return value


def _task(case):
    _require(type(case) is dict and set(case) == TASK_FIELDS, 'policy_task_fields_invalid')
    _identifier(case['id'], 'policy_task_id_invalid')
    root = _relative(case['snapshot_root'])
    _require(root.startswith('snapshots/') and len(root.split('/')) >= 2,
             'policy_snapshot_root_invalid')
    for key in ('question', 'scenario'):
        _text(case[key], 4000)
    assumptions = case['assumptions']
    _require(type(assumptions) is list and len(assumptions) <= 12,
             'policy_assumptions_invalid')
    for assumption in assumptions:
        _text(assumption, 1000)
    for field in ('authorized_files', 'initial_evidence_files'):
        paths = case[field]
        _require(type(paths) is list and 1 <= len(paths) <= MAX_FILES,
                 'policy_files_invalid')
        for relative in paths:
            _relative(relative)
        _require(len(set(paths)) == len(paths), 'policy_duplicate_file')
    authorized, initial = set(case['authorized_files']), set(case['initial_evidence_files'])
    _require(initial <= authorized, 'policy_initial_files_invalid')
    _require(case['stratum'] in ('full_context', 'retrieval_required')
             and case['evidence_parity'] in ('equal', 'unequal_by_design'),
             'policy_evidence_parity_invalid')
    _require((case['stratum'] == 'full_context' and case['evidence_parity'] == 'equal'
              and initial == authorized)
             or (case['stratum'] == 'retrieval_required'
                 and case['evidence_parity'] == 'unequal_by_design' and initial < authorized),
             'policy_evidence_parity_invalid')
    entry = case['entrypoint']
    _require(type(entry) is dict and set(entry) == {'file', 'symbol'}
             and entry['file'] in initial, 'policy_entrypoint_invalid')
    _require(type(entry['symbol']) is str and len(entry['symbol']) <= 128
             and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', entry['symbol'], re.ASCII),
             'policy_entrypoint_invalid')


def _model(value):
    allowed = {'model_id', 'provider', 'thinking', 'reasoning_effort'}
    _require(type(value) is dict and 'model_id' in value and set(value) <= allowed,
             'policy_model_fields_invalid')
    _identifier(value['model_id'], 'policy_model_invalid')
    if 'provider' in value:
        _require(value['provider'] == 'deepseek', 'policy_model_invalid')
    if 'thinking' in value:
        _require(type(value['thinking']) is bool, 'policy_model_invalid')
    if 'reasoning_effort' in value:
        effort = value['reasoning_effort']
        _require(effort in ('none', 'low', 'high', 'max')
                 and ((effort == 'none') == (not value.get('thinking', False))),
                 'policy_model_invalid')


def _budget(value):
    _require(type(value) is dict and set(value) == set(DEFAULT_BUDGET), 'policy_budget_fields_invalid')
    bounds = {'max_total_input_tokens': (3, 1000000), 'max_total_output_tokens': (3, 131072),
              'max_tool_reads': (0, 4), 'max_request_bytes': (1024, 1048576),
              'max_record_bytes': (1024, 4194304)}
    for key, (minimum, maximum) in bounds.items():
        _require(type(value[key]) is int and minimum <= value[key] <= maximum,
                 'policy_budget_invalid')
    wall = value['max_wall_seconds']
    _require(type(wall) in (int, float) and math.isfinite(wall) and 0 < wall <= 300,
             'policy_budget_invalid')


def _snapshot(value, case):
    _require(type(value) is dict and set(value) == {'root', 'files', 'sha256'}
             and value['root'] == case['snapshot_root'], 'policy_snapshot_invalid')
    files = value['files']
    _require(type(files) is dict and set(files) == set(case['authorized_files']),
             'policy_snapshot_files_invalid')
    total = 0
    for relative, row in files.items():
        _relative(relative)
        _require(type(row) is dict and set(row) == {'sha256', 'size_bytes'}
                 and type(row['sha256']) is str and _DIGEST.fullmatch(row['sha256'])
                 and type(row['size_bytes']) is int and 0 <= row['size_bytes'] <= MAX_FILE_BYTES,
                 'policy_snapshot_file_invalid')
        total += row['size_bytes']
    _require(total <= MAX_SOURCE_BYTES, 'policy_source_size_limit')
    _require(value['sha256'] == _digest({'root': value['root'], 'files': files}),
             'policy_snapshot_binding_invalid')


def _validate(manifest):
    _require(type(manifest) is dict and set(manifest) == MANIFEST_FIELDS,
             'policy_manifest_fields_invalid')
    _require(manifest['schema'] == SCHEMA, 'policy_schema_invalid')
    _task(manifest['task'])
    _snapshot(manifest['snapshot'], manifest['task'])
    _model(manifest['model'])
    _require(type(manifest['prompt_version']) is str and
             manifest['prompt_version'] in READABLE_PROMPT_VERSIONS, 'policy_prompt_version_invalid')
    _require(type(manifest['arms']) is list and manifest['arms'] == ARMS, 'policy_arms_invalid')
    _budget(manifest['budget'])
    _require(type(manifest['repeat']) is int and 1 <= manifest['repeat'] <= 1000,
             'policy_repeat_invalid')
    bound = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    _require(type(manifest['manifest_sha256']) is str
             and manifest['manifest_sha256'] == _digest(bound), 'policy_manifest_drift')
    _require(len(_encoded(manifest)) + 1 <= MAX_MANIFEST_BYTES, 'policy_manifest_size_limit')


def _root(path):
    path = Path(path).absolute()
    try:
        validate_workspace_path(path, output=True)
        with open_directory(path):
            pass
    except (OSError, ContractError):
        raise PolicyManifestError('policy_directory_invalid') from None
    return path


def _read(root, relative, limit):
    try:
        return read_safe(root, relative, limit)
    except (OSError, ContractError, BudgetExceeded):
        raise PolicyManifestError('policy_read_invalid_or_size_limit') from None


def _source(root, snapshot, relative):
    data = _read(root, snapshot['root'] + '/' + relative, MAX_FILE_BYTES)
    row = snapshot['files'][relative]
    _require(len(data) == row['size_bytes'] and sha256(data).hexdigest() == row['sha256'],
             'policy_snapshot_drift')
    try:
        return SourceText.from_bytes(data, relative).text
    except ContractError:
        raise PolicyManifestError('policy_source_encoding_invalid') from None


def prepare_policy_manifest(case: dict, *, dataset_root: Path, out: Path, model: dict,
                            repeat: int = 1, prompt_version: str = PROMPT_VERSION,
                            budget: dict | None = None) -> dict:
    """Freeze one neutral task to a new local directory containing manifest.json.

    Budget fields are planning reservations, not measured token or cost guarantees.
    Only explicitly authorized files are opened; no sibling gold is inspected.
    """
    _task(case)
    _model(model)
    _budget(DEFAULT_BUDGET if budget is None else budget)
    _require(type(repeat) is int and 1 <= repeat <= 1000, 'policy_repeat_invalid')
    _require(prompt_version in (PROMPT_VERSION, STUDY_V2_PROMPT_VERSION,
        STUDY_EVIDENCE_PROMPT_VERSION, STUDY_GAP_PROMPT_VERSION,
        STUDY_VALUE_PROMPT_VERSION), 'policy_prompt_version_invalid')
    root = _root(dataset_root)
    output = Path(out).absolute()
    try:
        validate_workspace_path(output, output=True)
    except ContractError:
        raise PolicyManifestError('policy_output_invalid') from None
    _require(not output.is_relative_to(root / 'snapshots'), 'policy_output_in_snapshot')
    parent = _root(output.parent)
    files, total = {}, 0
    for relative in sorted(case['authorized_files']):
        data = _read(root, case['snapshot_root'] + '/' + relative, MAX_FILE_BYTES)
        total += len(data)
        _require(total <= MAX_SOURCE_BYTES, 'policy_source_size_limit')
        try:
            SourceText.from_bytes(data, relative)
        except ContractError:
            raise PolicyManifestError('policy_source_encoding_invalid') from None
        files[relative] = {'sha256': sha256(data).hexdigest(), 'size_bytes': len(data)}
    snapshot = {'root': case['snapshot_root'], 'files': files}
    snapshot['sha256'] = _digest(snapshot)
    manifest = {'schema': SCHEMA, 'task': case, 'snapshot': snapshot, 'model': model,
                'prompt_version': prompt_version, 'arms': list(ARMS),
                'budget': dict(DEFAULT_BUDGET if budget is None else budget), 'repeat': repeat}
    manifest['manifest_sha256'] = _digest(manifest)
    _validate(manifest)
    payload = _encoded(manifest) + b'\n'
    try:
        with open_directory(parent) as parent_fd:
            os.mkdir(output.name, 0o700, dir_fd=parent_fd)
            descriptor = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=parent_fd)
            try:
                fd = os.open('manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=descriptor)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
    except FileExistsError:
        raise PolicyManifestError('policy_output_exists') from None
    except OSError:
        raise PolicyManifestError('policy_output_invalid') from None
    # Return a detached JSON value so caller mutations cannot alter input objects.
    return json.loads(payload)


def load_policy_manifest(out: Path, *, dataset_root: Path) -> dict:
    """Load bounded unique-key JSON and verify every authorized source before replay."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, 'policy_duplicate_json_key')
            value[key] = item
        return value

    def invalid_constant(_value):
        raise PolicyManifestError('policy_json_invalid')

    data = _read(_root(out), 'manifest.json', MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(data.decode('utf-8'), object_pairs_hook=unique,
                              parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, PolicyManifestError):
            raise
        raise PolicyManifestError('policy_json_invalid') from None
    _validate(manifest)
    root = _root(dataset_root)
    for relative in manifest['task']['authorized_files']:
        _source(root, manifest['snapshot'], relative)
    return manifest


def read_snapshot_file(manifest: dict, dataset_root: Path, relative: str) -> str:
    """Read one bound source, retaining its raw LF/CRLF/CR physical coordinates."""
    _validate(manifest)
    _relative(relative)
    _require(relative in manifest['snapshot']['files'], 'policy_file_not_authorized')
    return _source(_root(dataset_root), manifest['snapshot'], relative)
