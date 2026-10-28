"""Export bounded source evidence without loading gold or executing fixture code."""
import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / 'src'))
from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.scope import read_safe
from macr.investigation.validation import ContractError

VERSION = 'deepseek-policy-dev-v1'
CASE_FIELDS = {'id', 'snapshot_root', 'stratum', 'evidence_parity', 'authorized_files',
               'initial_evidence_files', 'entrypoint', 'question', 'scenario', 'assumptions'}


def _require(condition):
    if not condition:
        raise ValueError('invalid development dataset boundary')


def _read(root, relative, limit):
    try:
        return read_safe(root, relative, limit).decode('utf-8')
    except (OSError, ContractError, BudgetExceeded, UnicodeError):
        raise ValueError('invalid development dataset boundary') from None


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def export_cases(arm, *, root=ROOT):
    """A receives all raw code; B/C/D receive only declared initial evidence.

    The result is a source packet, not a model request or semantic score. Arm and
    stratum metadata belong in records; only source/question/scenario/assumptions
    should become diagnostic prompt content.
    """
    _require(arm in ('A', 'B', 'C', 'D'))
    root = Path(root).absolute()
    _require(root == root.resolve() and root.is_dir())
    manifest = json.loads(_read(root, 'cases.json', 64 * 1024), object_pairs_hook=_object)
    _require(set(manifest) == {'version', 'split', 'cases'})
    _require(manifest['version'] == VERSION and manifest['split'] == 'development')
    _require(type(manifest['cases']) is list and 1 <= len(manifest['cases']) <= 12)
    snapshots = root / 'snapshots'
    _require(not snapshots.is_symlink() and snapshots.is_dir())
    output, seen = [], set()
    for case in manifest['cases']:
        _require(type(case) is dict and set(case) == CASE_FIELDS)
        key = case['id']
        _require(type(key) is str and re.fullmatch(r'p(?:0[1-9]|1[0-2])', key) and key not in seen)
        seen.add(key)
        _require(case['snapshot_root'] == 'snapshots/' + key)
        snapshot = root / case['snapshot_root']
        _require(not snapshot.is_symlink() and snapshot.is_dir() and snapshot == snapshot.resolve())
        authorized, initial = case['authorized_files'], case['initial_evidence_files']
        for names in (authorized, initial):
            _require(type(names) is list and 1 <= len(names) <= 2 and
                     all(type(name) is str and re.fullmatch(r'[a-z_]+\.py', name) for name in names) and
                     names == sorted(set(names)))
        _require(set(initial) <= set(authorized))
        children = list(snapshot.iterdir())
        _require(all(not child.is_symlink() and child.is_file() for child in children))
        _require(sorted(child.name for child in children) == authorized)
        full = case['stratum'] == 'full_context'
        _require(case['stratum'] in ('full_context', 'retrieval_required'))
        _require(case['evidence_parity'] == ('equal' if full else 'unequal_by_design'))
        _require(initial == authorized if full else len(initial) < len(authorized))
        entry = case['entrypoint']
        _require(type(entry) is dict and set(entry) == {'file', 'symbol'} and
                 entry['file'] in initial and type(entry['symbol']) is str and entry['symbol'].isidentifier())
        for field in ('question', 'scenario'):
            _require(type(case[field]) is str and 0 < len(case[field]) <= 2000)
        _require(type(case['assumptions']) is list and 1 <= len(case['assumptions']) <= 6 and
                 all(type(value) is str and 0 < len(value) <= 500 for value in case['assumptions']))
        # Validate the whole authorized inventory even when the initial view is smaller.
        texts = {name: _read(root, case['snapshot_root'] + '/' + name, 2048)
                 for name in authorized}
        _require(all(1 <= len(text.splitlines()) <= 24 for text in texts.values()))
        provided = authorized if arm == 'A' else initial
        output.append({field: case[field] for field in ('id', 'stratum', 'evidence_parity',
                      'question', 'scenario', 'assumptions', 'entrypoint', 'authorized_files')} |
                      {'provided_files': provided,
                       'source': [{'file': name, 'start': 1, 'end': len(texts[name].splitlines()),
                                   'body': texts[name]} for name in provided]})
    return {'version': VERSION, 'split': 'development', 'arm': arm, 'cases': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('A', 'B', 'C', 'D'), required=True)
    args = parser.parse_args()
    print(json.dumps(export_cases(args.arm), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
