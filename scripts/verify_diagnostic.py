"""Replay one captured candidate and check offline; never contact a provider."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from macr.evals.diagnostic_compare import _root, _write
from macr.evals.diagnostic_verification import run_pipeline
from macr.investigation.scope import read_safe
from macr.providers.base import ModelRequest, ModelResponse, ProviderError
from macr.providers.local_http import _object


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='project-local captured JSON bundle')
    parser.add_argument('--out', type=Path, required=True, help='new directory under artifacts')
    args = parser.parse_args()
    try:
        source = args.input.absolute()
        if source.suffix != '.json' or not source.is_relative_to(PROJECT) or source != source.resolve():
            raise ValueError('input_path_invalid')
        bundle = _object(read_safe(PROJECT, source.relative_to(PROJECT).as_posix(), 64 * 1024))
        if set(bundle) != {'request', 'candidate_raw', 'verification_response', 'error_code', 'observations'}:
            raise ValueError('replay_fields_invalid')
        if (bundle['verification_response'] is None) == (bundle['error_code'] is None):
            raise ValueError('replay_result_invalid')
        request = ModelRequest(**bundle['request'])
        response = ModelResponse(**bundle['verification_response']) if bundle['verification_response'] is not None else None
        if bundle['error_code'] is not None and (type(bundle['error_code']) is not str or not bundle['error_code']):
            raise ValueError('replay_error_invalid')
        out = _root(args.out)
        if out.exists():
            raise ValueError('replay_already_exists')

        def captured_check(unused_request):
            if response is None:
                raise ProviderError(bundle['error_code'])
            return response

        report = run_pipeline(request, bundle['candidate_raw'], captured_check, observations=bundle['observations'])
        report.update(provenance='offline_replay', new_model_calls=0)
        out.mkdir(parents=True)
        _write(out, 'verification.json', report)
        print(json.dumps({'status': report['status'], 'report': str(out / 'verification.json'),
                          'new_model_calls': 0, 'semantic_proof': False}))
        return 0 if report['status'] == 'accepted' else 1
    except (ValueError, TypeError, KeyError, OSError, ProviderError) as error:
        parser.exit(2, f'verification replay failed: {type(error).__name__}; retained output: {args.out}\n')


if __name__ == '__main__':
    raise SystemExit(main())
