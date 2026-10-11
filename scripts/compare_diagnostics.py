"""Prepare a dataset or compare captured responses offline; never contact a provider."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from macr.evals.diagnostic_compare import prepare_comparison, record_response, compare_results, save_comparison
from macr.investigation.scope import exclusion, read_safe
from macr.investigation.records import ScopeProfile
from macr.providers.base import ModelResponse, ProviderError, ProviderCapabilities
from macr.providers.local_http import _object
from macr.providers.profiles import ModelProfile


def _input_bytes(path, limit):
    path = path.absolute()
    if path.suffix != '.json' or not path.is_relative_to(PROJECT) or path != path.resolve():
        raise ValueError('input_path_invalid')
    relative = path.relative_to(PROJECT)
    if any(exclusion(part, ScopeProfile()) in ('secret_path', 'appledouble') for part in relative.parts):
        raise ValueError('input_path_invalid')
    return read_safe(PROJECT, relative.as_posix(), limit)


def _profiles(raws):
    if type(raws) is not list or not 1 <= len(raws) <= 4:
        raise ValueError('profile_list_invalid')
    profiles = []
    for raw in raws:
        if set(raw['capabilities']) != {'json_mode', 'usage', 'cancel', 'health'}:
            raise ValueError('profile_capabilities_invalid')
        profiles.append(ModelProfile(**(raw | {'capabilities': ProviderCapabilities(**raw['capabilities'])})))
    return profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--input', type=Path, help='explicit project-local JSON replay bundle')
    mode.add_argument('--dataset', type=Path, help='project-local cases JSON; freeze only, no responses')
    parser.add_argument('--reference', type=Path, help='separate dataset reference JSON')
    parser.add_argument('--profiles', type=Path, help='project-local JSON list of frozen profiles')
    parser.add_argument('--out', type=Path, required=True, help='new directory under project artifacts')
    args = parser.parse_args()
    if args.dataset is not None and (args.reference is None or args.profiles is None):
        parser.error('--dataset requires --reference and --profiles')
    if args.input is not None and (args.reference is not None or args.profiles is not None):
        parser.error('--reference and --profiles require --dataset')
    try:
        if args.dataset is not None:
            from macr.evals.diagnostic_dataset import load_dataset
            # The existing strict decoder also checks nested duplicates in a JSON list.
            profile_document = _object(b'{"profiles":' + _input_bytes(args.profiles, 64 * 1024) + b'}')
            if set(profile_document) != {'profiles'}:
                raise ValueError('profile_list_invalid')
            profiles = _profiles(profile_document['profiles'])
            cases, reference = load_dataset(args.dataset, args.reference, timeout_seconds=profiles[0].timeout_seconds)
            result = prepare_comparison(args.out, cases, profiles,
                prompt_version=cases[0]['request']['metadata']['prompt_version'],
                dataset_version=reference['version'], reference=reference)
            print(json.dumps({'prepared': True, **result}))
            return 0
        bundle = _object(_input_bytes(args.input, 1024 * 1024))
        if set(bundle) != {'cases', 'profiles', 'prompt_version', 'dataset_version', 'reference', 'results', 'reviews'}:
            raise ValueError('replay_fields_invalid')
        profiles = _profiles(bundle['profiles'])
        if type(bundle['results']) is not list or len(bundle['results']) > 48:
            raise ValueError('replay_result_limit')
        prepare_comparison(args.out, bundle['cases'], profiles,
            prompt_version=bundle['prompt_version'], dataset_version=bundle['dataset_version'], reference=bundle['reference'])
        for result in bundle['results']:
            if set(result) != {'profile_id', 'case_id', 'response', 'error_code', 'proof', 'provenance'}:
                raise ValueError('replay_result_fields_invalid')
            response = ModelResponse(**result['response']) if result['response'] is not None else None
            error = ProviderError(result['error_code']) if result['error_code'] is not None else None
            row = record_response(args.out, result['profile_id'], result['case_id'], response=response,
                error=error, proof=result['proof'], provenance=result['provenance'])
            if row['failure_class'] == 'infrastructure':
                break
        report = compare_results(args.out, reviews=bundle['reviews'])
        paths = save_comparison(args.out, report)
        print(json.dumps({'report': str(paths['json']), 'markdown': str(paths['markdown']),
            'recorded_responses': len(report['cases']), 'new_model_calls': 0,
            'diagnostic_passes': sum(m['diagnostic_passes'] for m in report['models']),
            'real_model_accuracy_measured': False, 'stopped': report['stopped']}))
        return 1 if report['stopped'] else 0
    except (ValueError, TypeError, KeyError, OSError, ProviderError) as error:
        parser.exit(2, f'comparison failed: {type(error).__name__}; retained output: {args.out}\n')


if __name__ == '__main__':
    raise SystemExit(main())
