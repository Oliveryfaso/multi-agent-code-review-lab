"""Public synthetic dictionaries only; no API, source execution or hidden data."""
from copy import deepcopy
import unittest

from macr.evals.deepseek_study_report import FLAGS, result_digest
from macr.evals.deepseek_policy_spend import _usage_evidence

try:
    from macr.evals.static_diagnostic_report import (
        build_report, export_packets, sanitize_report)
except ModuleNotFoundError:
    build_report = export_packets = sanitize_report = None


def fixture():
    """Mock a closed live receipt shape, not an observed experiment."""
    tasks = [{'id': f'mock-task-{n}', 'project_id': f'mock-project-{n // 3}',
              'question': 'Does this public mock function meet its requirement?',
              'scenario': 'Synthetic report fixture.', 'assumptions': []}
             for n in range(9)]
    categories = ['defect'] * 3 + ['healthy'] * 3 + ['external_gap'] * 3
    qualifications = {t['id']: {'category': category, 'stratum': 'full_context',
        'valid': True, 'reason': 'Public mock qualification.'}
        for t, category in zip(tasks, categories)}
    cells = [{'cell_id': f'mock-cell-{n}-{arm}', 'task_id': t['id'],
              'project_id': t['project_id'], 'phase': 'S1', 'arm': arm}
             for n, t in enumerate(tasks) for arm in ('direct', 'evidence')]
    s0 = [{'cell_id': f'mock-s0-{n}', 'task_id': f'dev-{n // 2}',
           'project_id': 'mock-dev', 'phase': 'S0',
           'arm': 'direct' if n % 2 == 0 else 'evidence'} for n in range(6)]
    plan = {'schema': 'macr.static-diagnostic.stage.v1',
        'profile': 'static-diagnostic-v1', 'plan_sha256': 'a' * 64,
        'model': {'provider': 'deepseek', 'model_id': 'deepseek-flash',
                  'thinking': True, 'reasoning_effort': 'high'}, 'phases': {
            'S0': {'tasks': [], 'cells': s0},
            'S1': {'tasks': tasks, 'cells': cells}}}
    usage = {'prompt_tokens': 100, 'completion_tokens': 40,
        'total_tokens': 140, 'prompt_cache_hit_tokens': 0,
        'prompt_cache_miss_tokens': 100,
        'completion_tokens_details': {'reasoning_tokens': 20}}
    cost = _usage_evidence(usage, 8192)[0]
    source = [{'id': 'e1', 'file': 'public_mock.py', 'line_start': 1,
        'line_end': 2, 'symbol': 'mock', 'source_tool': 'source',
        'body': 'def mock():\n    return True\n', 'citable': True, 'complete': True}]
    results = {}
    for cell in cells:
        task = next(t for t in tasks if t['id'] == cell['task_id'])
        category = qualifications[task['id']]['category']
        verdict = {'defect': 'supported_defect', 'healthy': 'no_defect_supported',
                   'external_gap': 'unknown'}[category]
        results[cell['cell_id']] = {'schema': 'macr.static-diagnostic.cell.v1',
            **cell, 'profile': 'static-diagnostic-v1', 'provenance': 'approved_live',
            'status': 'semantic_pending', 'error_code': None,
            'question': task['question'], 'scenario': task['scenario'],
            'assumptions': [], 'source': deepcopy(source),
            'answer': {'verdict': verdict, 'scope': 'mock-scope',
                'cause': None if verdict == 'unknown' else 'Synthetic mechanism.',
                'when': None if verdict == 'unknown' else 'Synthetic domain.',
                'refs': [], 'unknowns': ['Synthetic external gap.'] if verdict == 'unknown' else []},
            'initial_source_sha256': result_digest(source),
            'evidence_packet': {'mock': True} if cell['arm'] == 'evidence' else None,
            'request_sha256': 'b' * 64, 'request': {'max_output_tokens': 8192},
            'response': {'state': 'received', 'model': 'deepseek-flash',
                'system_fingerprint': 'mock-fingerprint', 'raw_usage': deepcopy(usage),
                'finish_reason': 'stop', 'latency_ms': 1000},
            'accounting': {'request_count': 1,
                'known_accrued_usd': f'{cost / 1000000:.6f}',
                'unknown_held_usd': '0.000000'},
            'model_calls_live': 1, 'fixture_model_calls': 0,
            'elapsed_seconds': 1.0, 'standalone_elapsed_seconds': 1.1,
            'preparation': {'common_seconds': .1, 'evidence_seconds': 0.,
                'packet_bytes': 30 if cell['arm'] == 'evidence' else 0,
                'wire_bytes': 250}}
    run = {'schema': 'macr.static-diagnostic.execution.v1',
        'profile': 'static-diagnostic-v1', 'plan_sha256': plan['plan_sha256'],
        'provenance': 'approved_live', 'planned_cells': 24, 's0_qualified': True,
        'stage_halted': False, 'rows': [], 'phases': {'S0': {}, 'S1': {}}}
    refresh(run, plan, results)
    return plan, run, results, qualifications


def refresh(run, plan, results):
    run['rows'] = [{**c, 'status': results[c['cell_id']]['status']
        if c['cell_id'] in results else 'not_attempted',
        'error_code': results.get(c['cell_id'], {}).get('error_code'),
        'result_sha256': result_digest(results[c['cell_id']])
        if c['cell_id'] in results else None}
        for phase in ('S0', 'S1') for c in plan['phases'][phase]['cells']]


def locked(plan, run, results, qualifications, flags_by_cell=None):
    exported = export_packets(plan, run, results)
    reviews, determinate = {}, {}
    for packet in exported['packets']:
        cell_id = exported['keeper_mapping'][packet['review_id']]
        category = qualifications[results[cell_id]['task_id']]['category']
        flags = dict.fromkeys(FLAGS, False)
        flags['reasonable_abstention' if category == 'external_gap' else 'full_solution'] = True
        flags.update((flags_by_cell or {}).get(cell_id, {}))
        review = {'result_sha256': packet['result_sha256'], 'status': 'locked',
                  'rationale': 'Locked public mock judgment.', 'flags': flags}
        reviews[packet['review_id']] = review
        determinate[packet['review_id']] = {'result_sha256': packet['result_sha256'],
            'review_sha256': result_digest(review), 'status': 'locked',
            'determinate_error': False, 'rationale': 'Locked public mock support check.'}
    return {'schema': 'macr.static-diagnostic.review-import.v1',
        'run_sha256': result_digest(run), 'reviews': reviews,
        'determinate_reviews': determinate}, exported['keeper_mapping']


class StaticDiagnosticReportTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(build_report), 'pure static report API is missing')
        self.plan, self.run, self.results, self.quals = fixture()

    def report(self, **kwargs):
        return build_report(self.plan, self.run, self.results, **kwargs)

    def test_export_is_bound_anonymous_common_source_without_capsule_or_arm(self):
        exported = export_packets(self.plan, self.run, self.results)
        self.assertEqual(len(exported['packets']), 18)
        self.assertEqual(exported['run_sha256'], result_digest(self.run))
        for packet in exported['packets']:
            self.assertEqual(set(packet), {'review_id', 'result_sha256', 'question',
                'scenario', 'assumptions', 'source', 'answer'})
            cell = self.results[exported['keeper_mapping'][packet['review_id']]]
            self.assertEqual(packet['source'], cell['source'])
            self.assertEqual(packet['result_sha256'], result_digest(cell))
        exported['packets'][0]['source'][0]['body'] = 'changed anonymous copy'
        self.assertIn('def mock', next(iter(self.results.values()))['source'][0]['body'])

    def test_unscored_full_denominator_has_no_semantic_ties(self):
        report = self.report()
        self.assertEqual(report['planned_cells'], 18)
        self.assertEqual(report['pair_counts'], {'win': 0, 'loss': 0, 'tie': 0, 'not_comparable': 9})
        self.assertEqual(report['selection']['decision'], 'inconclusive')
        self.assertEqual(report['strategies']['direct']['metrics']['complete_solvable']['denominator'], 6)

    def test_failures_stay_in_denominator_and_are_never_semantic_ties(self):
        for arm in ('direct', 'evidence'):
            self.results[f'mock-cell-0-{arm}'].update(status='contract_failed', answer=None,
                error_code='compact_contract_invalid')
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertEqual(report['pair_counts'], {'win': 0, 'loss': 0, 'tie': 8, 'not_comparable': 1})
        self.assertEqual(report['strategies']['direct']['counts']['contract_failed'], 1)
        self.assertEqual(report['selection']['decision'], 'inconclusive')

    def test_locked_two_pair_gain_requires_all_resource_readiness_gates(self):
        flags = {f'mock-cell-{n}-direct': {'full_solution': False, 'partial': True} for n in (0, 1)}
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals, flags)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertEqual(report['pair_counts']['win'], 2)
        self.assertEqual(report['selection']['decision'], 'advance_evidence_research')
        self.results['mock-cell-0-evidence']['standalone_elapsed_seconds'] = 100
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals, flags)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertEqual(report['selection']['decision'], 'continue_direct_baseline')
        self.assertFalse(report['selection']['checks']['elapsed_within_1_25'])

    def test_missing_or_contradictory_review_bindings_are_rejected(self):
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        for mutation in ('run', 'result', 'review', 'missing', 'strict_bool', 'partial_conflict'):
            changed = deepcopy(bundle)
            key = next(iter(changed['reviews']))
            if mutation == 'run': changed['run_sha256'] = 'c' * 64
            if mutation == 'result': changed['reviews'][key]['result_sha256'] = 'c' * 64
            if mutation == 'review': changed['determinate_reviews'][key]['review_sha256'] = 'c' * 64
            if mutation == 'missing': del changed['reviews'][key]
            if mutation == 'strict_bool': changed['reviews'][key]['flags']['full_solution'] = 1
            if mutation == 'partial_conflict':
                gap = next(k for k, c in mapping.items() if c == 'mock-cell-6-direct')
                changed['reviews'][gap]['flags']['partial'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.report(score_bundle=changed, keeper_mapping=mapping, qualifications=self.quals)

    def test_flags_must_match_actual_answer_and_private_category(self):
        self.results['mock-cell-3-direct']['answer']['verdict'] = 'supported_defect'
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        with self.assertRaisesRegex(ValueError, 'answer_conflict'):
            self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)

    def test_definite_external_gap_requires_determinate_error(self):
        self.results['mock-cell-6-direct']['answer']['verdict'] = 'no_defect_supported'
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals,
            {'mock-cell-6-direct': {'reasonable_abstention': False}})
        with self.assertRaisesRegex(ValueError, 'determinate'):
            self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)

    def test_unknown_and_missing_usage_prevent_adoption_and_preserve_hold(self):
        cell = self.results['mock-cell-0-evidence']
        cell.update(status='unknown', answer=None)
        cell['response']['raw_usage'] = None
        cell['accounting'].update(known_accrued_usd='0.000000', unknown_held_usd='0.330000')
        refresh(self.run, self.plan, self.results)
        report = self.report()
        actual = report['strategies']['evidence']['actual']
        self.assertEqual(actual['unknown_held_usd'], '0.330000')
        self.assertIsNone(actual['input_tokens'])
        self.assertEqual(report['selection']['decision'], 'inconclusive')

    def test_source_identity_or_metering_mismatch_is_not_gain_evidence(self):
        for mutation in ('source', 'identity', 'metering'):
            plan, run, results, quals = fixture()
            cell = results['mock-cell-0-evidence']
            if mutation == 'source': cell['initial_source_sha256'] = 'c' * 64
            if mutation == 'identity': cell['response']['system_fingerprint'] = 'different-mock-fp'
            if mutation == 'metering': cell['accounting']['known_accrued_usd'] = '0.000000'
            refresh(run, plan, results)
            bundle, mapping = locked(plan, run, results, quals)
            report = build_report(plan, run, results, score_bundle=bundle,
                keeper_mapping=mapping, qualifications=quals)
            self.assertEqual(report['selection']['decision'], 'inconclusive', mutation)

    def test_closed_summary_bindings_and_grid_are_strict(self):
        for mutation in ('row_digest', 'not_closed', 'duplicate_cell', 'extra_result'):
            plan, run, results, _ = fixture()
            if mutation == 'row_digest': run['rows'][-1]['result_sha256'] = 'c' * 64
            if mutation == 'not_closed': run['rows'].pop()
            if mutation == 'duplicate_cell': plan['phases']['S1']['cells'][-1] = plan['phases']['S1']['cells'][0]
            if mutation == 'extra_result': results['not-planned'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_report(plan, run, results)

    def test_s0_extras_are_accepted_but_never_exported_or_aggregated(self):
        first = self.plan['phases']['S0']['cells'][0]
        cell = deepcopy(self.results['mock-cell-0-direct']) | first
        self.results[first['cell_id']] = cell
        refresh(self.run, self.plan, self.results)
        self.assertEqual(len(export_packets(self.plan, self.run, self.results)['packets']), 18)
        self.assertEqual(self.report()['strategies']['direct']['actual']['requests'], 9)

    def test_sanitized_report_contains_only_aggregate_not_private_maps_or_cases(self):
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        sanitized = sanitize_report(report)
        self.assertNotIn('cells', sanitized)
        self.assertNotIn('pairs', sanitized)
        self.assertEqual(sanitized['strategies'], report['strategies'])
        self.assertNotIn('mock-task', repr(sanitized))
        self.assertNotIn('rationale', repr(sanitized))
        self.assertNotIn('keeper_mapping', repr(sanitized))

    def test_actual_owner_model_object_and_failure_receipts_keep_verified_usage_identity(self):
        self.assertEqual(self.plan['model']['model_id'], 'deepseek-flash')
        cell = self.results['mock-cell-0-evidence']
        raw = cell['response']['raw_usage']
        cell.update(status='contract_failed', answer=None, error_code='model_response_invalid')
        cell['response'] = {'state': 'unknown', 'model': None, 'system_fingerprint': None,
            'billing_evidence': {'schema': 'macr.provider.billing.v1', 'provider': 'deepseek',
                'model': 'deepseek-flash', 'request_sha256': cell['request_sha256'],
                'max_output_tokens': 8192, 'raw_usage': raw, 'latency_ms': 1000},
            'identity_evidence': {'schema': 'macr.provider.identity.v1', 'provider': 'deepseek',
                'request_sha256': cell['request_sha256'], 'requested_model': 'deepseek-flash',
                'model': 'deepseek-flash', 'model_state': 'validated',
                'system_fingerprint': 'mock-fingerprint', 'fingerprint_state': 'validated'}}
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertTrue(report['selection']['readiness']['identity_continuous'])
        self.assertTrue(report['selection']['readiness']['metering_complete'])
        cell['response']['billing_evidence']['request_sha256'] = 'c' * 64
        refresh(self.run, self.plan, self.results)
        self.assertFalse(self.report()['selection']['readiness']['metering_complete'])

    def test_equal_claimed_hash_does_not_hide_actual_source_drift(self):
        self.results['mock-cell-0-evidence']['source'][0]['body'] = 'different actual body'
        refresh(self.run, self.plan, self.results)
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertFalse(report['selection']['readiness']['shared_source'])
        self.assertEqual(report['pair_counts']['not_comparable'], 1)

    def test_unknown_reservation_without_dispatch_is_reportable(self):
        cell = self.results['mock-cell-0-direct']
        cell.update(status='unknown', answer=None, response=None, model_calls_live=0)
        cell['accounting'].update(known_accrued_usd='0.000000', unknown_held_usd='0.330000')
        refresh(self.run, self.plan, self.results)
        report = self.report()
        self.assertEqual(report['strategies']['direct']['actual']['model_calls_live'], 8)
        self.assertEqual(report['selection']['decision'], 'inconclusive')

    def test_preparation_allocates_capsule_only_to_evidence_and_reports_measured_extra_tokens(self):
        for cell in self.results.values():
            cell['preparation'].update(common_seconds=.1, capture_common_seconds=.05,
                evidence_seconds=.2, packet_bytes=30)
            cell['standalone_elapsed_seconds'] = 1.3 if cell['arm'] == 'evidence' else 1.1
        refresh(self.run, self.plan, self.results)
        report = self.report()
        self.assertEqual(report['strategies']['direct']['actual']['preparation']['evidence_seconds'], 0)
        self.assertEqual(report['strategies']['direct']['actual']['preparation']['packet_bytes'], 0)
        self.assertAlmostEqual(report['strategies']['evidence']['actual']['preparation']['evidence_seconds'], 1.8)
        self.assertEqual(report['additional_input_tokens'], 0)

    def test_fixture_receipts_never_support_real_adoption(self):
        self.run['provenance'] = 'approved_live_fake'
        for cell in self.results.values():
            cell.update(provenance='approved_live_fake', model_calls_live=0, fixture_model_calls=1)
        refresh(self.run, self.plan, self.results)
        flags = {f'mock-cell-{n}-direct': {'full_solution': False, 'partial': True} for n in (0, 1)}
        bundle, mapping = locked(self.plan, self.run, self.results, self.quals, flags)
        report = self.report(score_bundle=bundle, keeper_mapping=mapping, qualifications=self.quals)
        self.assertEqual(report['pair_counts']['win'], 2)
        self.assertEqual(report['selection']['decision'], 'inconclusive')
        self.assertFalse(report['selection']['readiness']['live_evidence'])

    def test_sanitizer_drops_unexpected_nested_private_fields(self):
        report = self.report()
        report['strategies']['direct']['keeper_mapping'] = {'mock-task-secret': 'private'}
        report['strategies']['direct']['actual']['answer'] = {'rationale': 'mock-task-secret'}
        report['selection']['rationale'] = 'mock-task-secret'
        sanitized = sanitize_report(report)
        self.assertNotIn('mock-task-secret', repr(sanitized))

    def test_settled_operational_failures_cannot_advance_evidence_research(self):
        for status in ('failed', 'unknown'):
            plan, run, results, qualifications = fixture()
            results['mock-cell-2-evidence'].update(status=status, answer=None,
                error_code='static_stage_infrastructure_failure')
            refresh(run, plan, results)
            flags = {f'mock-cell-{n}-direct': {'full_solution': False, 'partial': True}
                for n in (0, 1)}
            bundle, mapping = locked(plan, run, results, qualifications, flags)
            report = build_report(plan, run, results, score_bundle=bundle,
                keeper_mapping=mapping, qualifications=qualifications)
            with self.subTest(status=status):
                self.assertEqual(report['selection']['decision'], 'inconclusive')
                self.assertFalse(report['selection']['readiness']['no_operational_failure'])

    def test_stage_accounting_includes_s0_cost_and_sanitizes_private_nested_fields(self):
        self.run.update(actual_requests=24, model_calls_live=24, fixture_model_calls=0,
            known_accrued_usd='0.030000', new_unknown_held_usd='0.000000')
        for phase, count, cost in (('S0', 6, '0.010000'), ('S1', 18, '0.020000')):
            self.run['phases'][phase]['accounting'] = {'request_count': count,
                'known_accrued_usd': cost, 'unknown_held_usd': '0.000000',
                'model_calls_live': None, 'keeper_mapping': {'private-case': 'secret'}}
        report = self.report()
        self.assertIn('stage_accounting', report)
        accounting = report['stage_accounting']
        self.assertEqual(accounting['actual_requests'], 24)
        self.assertEqual(accounting['known_accrued_usd'], '0.030000')
        self.assertEqual(accounting['phases']['S0']['known_accrued_usd'], '0.010000')
        self.assertEqual(report['planned_cells'], 18)
        self.assertIsNone(accounting['phases']['S0']['model_calls_live'])
        self.assertNotIn('private-case', repr(accounting))
        accounting['rationale'] = 'private-case'
        accounting['phases']['S0']['rationale'] = 'private-case'
        public = sanitize_report(report)
        self.assertEqual(public['stage_accounting']['phases']['S0']['request_count'], 6)
        self.assertNotIn('private-case', repr(public))
        self.run.pop('actual_requests')
        self.run['phases']['S0']['accounting'].pop('known_accrued_usd')
        missing = self.report()['stage_accounting']
        self.assertIsNone(missing['actual_requests'])
        self.assertIsNone(missing['phases']['S0']['known_accrued_usd'])


if __name__ == '__main__':
    unittest.main()
