"""Reproducible scripted repair-feedback demo. Never execute the fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'src'))
from macr.investigation.repair_loop import (
    ScriptedPatchProvider, ReplayVerifier, run_simulated_loop, verify_loop,
    source_id, test_manifest, _files,
)
from macr.investigation.repair_edits import apply_edits
from macr.investigation.validation import ContractError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('simulate', 'verify'))
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'verify':
            report = verify_loop(args.out)
        else:
            repo = PROJECT/'examples/repair-preview-development/baseline'
            files = _files(repo)
            editable = ['app.py', 'builder.py', 'records.py']
            old = 'return (subtotal + delivery_cents) * (100 - discount_percent) // 100'
            fixed = 'return subtotal * (100 - discount_percent) // 100 + delivery_cents'
            edits = [[{'file':'builder.py', 'old':old, 'new':'return 2000'}],
                     [{'file':'builder.py', 'old':'return 2000', 'new':fixed}]]
            wrong = apply_edits(files, edits[0], editable_paths=editable)
            corrected = apply_edits(wrong, edits[1], editable_paths=editable)
            names = test_manifest(files, ['checks.py'])
            def observation(bodies, failures):
                return {'source_id':source_id(bodies), 'execution_status':'completed',
                        'outcomes':{n:'failed' if n in failures else 'passed' for n in names}}
            provider = ScriptedPatchProvider([{'status':'candidate', 'summary':'Authored scripted proposal.',
                                              'edits':change} for change in edits])
            verifier = ReplayVerifier([observation(files, names[:1]), observation(wrong, names[1:]),
                                       observation(corrected, [])])
            report = run_simulated_loop(repo, 'Item discounts must exclude delivery charges.',
                editable_paths=editable, test_paths=['checks.py'], provider=provider,
                verifier=verifier, out=args.out)
        print(json.dumps({key:report[key] for key in ('status', 'simulation', 'proposal_requests',
            'candidate_origin', 'verification_origin', 'repaired', 'target_code_executed',
            'real_model_calls', 'paid_requests')}))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status':'rejected', 'error':error.code if isinstance(error, ContractError)
                          else 'local_record_unavailable', 'target_code_executed':False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
