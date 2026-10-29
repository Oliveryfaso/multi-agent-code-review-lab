"""Synthetic review/receipt fixtures; no unseen questions or gold are loaded."""
from copy import deepcopy
import importlib
import unittest

try:
    report = importlib.import_module('macr.evals.deepseek_study_report')
except ModuleNotFoundError:
    report = None


STRATEGIES = ['direct_initial_3', 'fixed_read_3', 'adaptive_read_3', 'direct_full_3']
FLAGS = ['full_solution', 'reasonable_abstention', 'partial', 'unnecessary_abstention',
         'false_positive', 'false_release', 'arm_evidence_gap']


def fixture():
    tasks, cells = [], []
    for index in range(9):
        task_id, project = f't{index}', f'p{index % 3}'
        tasks.append({'id': task_id, 'project_id': project, 'question': 'Inspect this code.',
                      'scenario': 'The allowed input is stated.', 'assumptions': [],
                      'source': [{'file': 'module.py', 'start': 1, 'end': 1, 'body': 'x = 1\n'}]})
        for strategy in STRATEGIES:
            cells.append({'cell_id': task_id + ':' + strategy, 'task_id': task_id,
                          'project_id': project, 'strategy': strategy})
    return {'profile_sha256': 'a' * 64, 'tasks': tasks, 'cells': cells}


def answer(cell):
    return {'task_id': cell['task_id'], 'strategy': cell['strategy'],
        'status': 'semantic_pending', 'review_status': 'pending',
        'answer': {'verdict': 'no_defect_supported', 'cause': 'Fixture mechanism.',
                   'when': 'For all allowed inputs.', 'refs': [], 'unknowns': []},
        'first_answer': {'verdict': 'unknown'}, 'tool_reads': 0,
        'elapsed_seconds': 2.0, 'timing_seconds': {'preparation': .2, 'provider': 1.8, 'tool': 0},
        'known_accrued_usd': '0.000012', 'unknown_reserved_usd': '0.000000',
        'requests': [{'response': {'raw_usage': {'prompt_tokens': 20, 'completion_tokens': 5,
            'prompt_cache_hit_tokens': 4, 'prompt_cache_miss_tokens': 16,
            'completion_tokens_details': {'reasoning_tokens': 2}}}}]}


def flags(**updates):
    return dict.fromkeys(FLAGS, False) | updates


def adjudicate(study, results):
    reviews, adjudications = {}, {'tasks': {}, 'cells': {}}
    for index, task in enumerate(study['tasks']):
        category = ['defect', 'healthy', 'external_gap'][index // 3]
        adjudications['tasks'][task['id']] = {'category': category,
            'stratum': 'full_context' if index % 3 == 0 else 'retrieval_required',
            'valid': True, 'reason': 'Synthetic qualification fixture.'}
    for cell_id, result in results.items():
        category = adjudications['tasks'][result['task_id']]['category']
        if category == 'external_gap':
            result['answer'] = {'verdict': 'unknown', 'cause': None, 'when': None,
                                'refs': [], 'unknowns': ['An external value is required.']}
        elif category == 'defect':
            result['answer']['verdict'] = 'supported_defect'
        judgment = flags(full_solution=category != 'external_gap',
                         reasonable_abstention=category == 'external_gap')
        review = {'result_sha256': report.result_digest(result), 'status': 'locked',
                  'rationale': 'Synthetic independent review fixture.', 'flags': judgment}
        reviews[cell_id] = review
        adjudications['cells'][cell_id] = {'result_sha256': report.result_digest(result),
            'review_sha256': report.result_digest(review), 'flags': judgment,
            'rationale': 'Synthetic custodian merge fixture.'}
    return reviews, adjudications


def safety_fixture(task_id, verdict, judgment, *, ask_user=False):
    study = fixture()
    cell_id = task_id + ':adaptive_read_3'
    cell = next(cell for cell in study['cells'] if cell['cell_id'] == cell_id)
    results = {cell_id: answer(cell)}
    reviews, adjudications = adjudicate(study, results)
    result = results[cell_id]
    if ask_user:
        result.update(status='ask_user_terminated', answer=None, gap='Synthetic external value.')
    else:
        result['answer']['verdict'] = verdict
    reviews[cell_id].update(result_sha256=report.result_digest(result), flags=judgment)
    adjudications['cells'][cell_id].update(result_sha256=report.result_digest(result),
        review_sha256=report.result_digest(reviews[cell_id]), flags=judgment)
    return study, results, reviews, adjudications


class StudyReportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(report, 'The independent study reporting adapter is not implemented.')

    def test_pending_outputs_and_missing_cells_keep_all_36_planned_slots(self):
        study = fixture()
        results = {study['cells'][0]['cell_id']: answer(study['cells'][0])}
        actual = report.build_study_report(study, results)
        self.assertEqual(actual['planned_cells'], 36)
        self.assertEqual(len(actual['cells']), 36)
        self.assertEqual(actual['strategies']['direct_initial_3']['counts']['not_attempted'], 8)
        self.assertEqual(actual['strategies']['direct_initial_3']['counts']['review_pending'], 1)
        metric = actual['strategies']['direct_initial_3']['metrics']['complete_solvable']
        self.assertEqual(metric, {'numerator': 0, 'denominator': None, 'state': 'pending'})
        self.assertEqual(actual['first_answer_observations'], 1)
        self.assertEqual(actual['task_observations'], 9)

    def test_final_review_scores_original_denominators_and_does_not_count_first_twice(self):
        study = fixture()
        results = {cell['cell_id']: answer(cell) for cell in study['cells']}
        reviews, adjudications = adjudicate(study, results)
        actual = report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)
        for strategy in STRATEGIES:
            metrics = actual['strategies'][strategy]['metrics']
            self.assertEqual(metrics['complete_solvable'], {'numerator': 6, 'denominator': 6, 'state': 'final'})
            self.assertEqual(metrics['complete_all']['numerator'], 6)
            self.assertEqual(metrics['necessary_abstention'], {'numerator': 3, 'denominator': 3, 'state': 'final'})
            self.assertEqual(metrics['disposition_all']['numerator'], 9)
            self.assertEqual(metrics['conditional_reviewed_disposition']['denominator'], 9)
        self.assertEqual(actual['planned_cells'], 36)
        self.assertEqual(actual['task_observations'], 9)
        self.assertEqual(actual['project_count'], 3)
        self.assertEqual(len(actual['pairs']['adaptive_vs_fixed']), 9)
        self.assertTrue(all(row['outcome'] == 'tie' for row in actual['pairs']['adaptive_vs_fixed']))

    def test_unattempted_unknown_and_unreviewed_pairs_are_not_observed_ties(self):
        for state in ('both_unattempted', 'one_unattempted', 'unknown', 'review_pending'):
            with self.subTest(state=state):
                study = fixture()
                results = {cell['cell_id']: answer(cell) for cell in study['cells']}
                reviews, adjudications = adjudicate(study, results)
                left, right = 't0:adaptive_read_3', 't0:fixed_read_3'
                removed = [left, right] if state == 'both_unattempted' else [right]
                for cell_id in removed:
                    reviews.pop(cell_id)
                    adjudications['cells'].pop(cell_id)
                if state == 'both_unattempted':
                    results.pop(left)
                    results.pop(right)
                elif state == 'one_unattempted':
                    results.pop(right)
                elif state == 'unknown':
                    results[right].update(status='remote_outcome_unknown', answer=None,
                        error_code='remote_outcome_unknown', unknown_reserved_usd='0.330000')
                actual = report.build_study_report(study, results, reviews=reviews,
                                                   adjudications=adjudications)
                pair = actual['pairs']['adaptive_vs_fixed'][0]
                self.assertEqual(pair['outcome'], 'pending' if state == 'review_pending' else 'not_comparable')
                self.assertFalse(pair['comparison_eligible'])
                self.assertIsNone(pair['comparison_basis'])
                counts = actual['pair_counts']['adaptive_vs_fixed']
                self.assertEqual(counts['planned_pairs'], 9)
                self.assertEqual(counts['complete_pairs'], 8)
                self.assertEqual(counts['incomplete_pairs'], 1)
                self.assertEqual(len(actual['cells']), 36)
                self.assertEqual(actual['strategies']['fixed_read_3']['metrics']['complete_solvable']['denominator'], 6)

    def test_known_contract_failure_pair_is_explicit_operational_comparison(self):
        study = fixture()
        results = {cell['cell_id']: answer(cell) for cell in study['cells']}
        reviews, adjudications = adjudicate(study, results)
        cell_id = 't0:fixed_read_3'
        results[cell_id].update(status='failed', error_code='diagnosis_json_invalid', answer=None)
        reviews.pop(cell_id)
        adjudications['cells'].pop(cell_id)
        actual = report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)
        pair = actual['pairs']['adaptive_vs_fixed'][0]
        self.assertEqual(pair['outcome'], 'win')
        self.assertTrue(pair['comparison_eligible'])
        self.assertEqual(pair['comparison_basis'], 'known_contract_failure_disposition')
        self.assertEqual(actual['pair_counts']['adaptive_vs_fixed'], {
            'planned_pairs': 9, 'complete_pairs': 9, 'incomplete_pairs': 0,
            'complete_quality_pairs': 8, 'complete_operational_pairs': 1})

    def test_safety_flags_reject_correct_verdict_marked_wrong_and_wrong_verdict_unmarked(self):
        cases = [('t3', 'no_defect_supported', flags(false_positive=True)),
                 ('t0', 'supported_defect', flags(false_release=True)),
                 ('t3', 'supported_defect', flags()),
                 ('t0', 'no_defect_supported', flags())]
        for task_id, verdict, judgment in cases:
            with self.subTest(task_id=task_id, verdict=verdict, flags=judgment):
                study, results, reviews, adjudications = safety_fixture(task_id, verdict, judgment)
                with self.assertRaisesRegex(ValueError, 'adjudication_answer_conflict'):
                    report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)

    def test_abstentions_cannot_be_marked_as_definite_false_positive_or_release(self):
        for task_id, judgment in [('t3', flags(false_positive=True)), ('t0', flags(false_release=True))]:
            for ask_user in (False, True):
                with self.subTest(task_id=task_id, ask_user=ask_user):
                    study, results, reviews, adjudications = safety_fixture(task_id, 'unknown', judgment,
                                                                          ask_user=ask_user)
                    with self.assertRaisesRegex(ValueError, 'adjudication_answer_conflict'):
                        report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)

    def test_consistent_safety_flags_are_accepted_without_automatic_semantic_pass(self):
        cases = [('t3', 'supported_defect', flags(false_positive=True), 'false_positive', 1),
                 ('t0', 'no_defect_supported', flags(false_release=True), 'false_release', 1),
                 ('t3', 'no_defect_supported', flags(), 'false_positive', 0),
                 ('t0', 'supported_defect', flags(), 'false_release', 0),
                 ('t3', 'unknown', flags(), 'false_positive', 0)]
        for task_id, verdict, judgment, metric, count in cases:
            with self.subTest(task_id=task_id, verdict=verdict):
                study, results, reviews, adjudications = safety_fixture(task_id, verdict, judgment)
                actual = report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)
                metrics = actual['strategies']['adaptive_read_3']['metrics']
                self.assertEqual(metrics[metric]['numerator'], count)
                self.assertEqual(metrics['complete_solvable']['numerator'], 0)

    def test_failures_unknown_unattempted_stay_in_main_and_pair_denominators(self):
        study = fixture()
        results = {cell['cell_id']: answer(cell) for cell in study['cells']}
        direct = 't0:direct_initial_3'
        failed = 't0:fixed_read_3'
        unknown = 't1:fixed_read_3'
        del results[direct]
        results[failed].update(status='failed', error_code='diagnosis_json_invalid', answer=None)
        results[unknown].update(status='remote_outcome_unknown', error_code='remote_outcome_unknown',
                                answer=None, unknown_reserved_usd='0.330000')
        reviewable = {key: row for key, row in results.items()
                      if row['status'] == 'semantic_pending'}
        reviews, adjudications = adjudicate(study, reviewable)
        actual = report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)
        fixed = actual['strategies']['fixed_read_3']
        self.assertEqual(fixed['counts']['contract_failed'], 1)
        self.assertEqual(fixed['counts']['unknown'], 1)
        self.assertEqual(fixed['metrics']['complete_solvable']['denominator'], 6)
        self.assertEqual(fixed['metrics']['complete_solvable']['numerator'], 4)
        self.assertEqual(fixed['metrics']['conditional_reviewed_disposition']['denominator'], 7)
        self.assertEqual(len(actual['pairs']['adaptive_vs_fixed']), 9)
        self.assertEqual(actual['pairs']['adaptive_vs_fixed'][0]['outcome'], 'win')
        self.assertEqual(actual['pairs']['adaptive_vs_fixed'][1]['outcome'], 'not_comparable')

    def test_review_must_bind_exact_result_and_merged_review(self):
        study = fixture()
        result = answer(study['cells'][0])
        results = {study['cells'][0]['cell_id']: result}
        reviews, adjudications = adjudicate(study, results)
        changed = deepcopy(results)
        changed[study['cells'][0]['cell_id']]['answer']['cause'] = 'Changed after review.'
        with self.assertRaisesRegex(ValueError, 'review_result_binding'):
            report.build_study_report(study, changed, reviews=reviews, adjudications=adjudications)
        bad = deepcopy(adjudications)
        bad['cells'][study['cells'][0]['cell_id']]['review_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'adjudication_review_binding'):
            report.build_study_report(study, results, reviews=reviews, adjudications=bad)

    def test_failed_cell_cannot_be_reviewed_into_success(self):
        study = fixture()
        result = answer(study['cells'][0])
        result.update(status='failed', error_code='eval_contract_invalid')
        results = {study['cells'][0]['cell_id']: result}
        reviews, adjudications = adjudicate(study, results)
        with self.assertRaisesRegex(ValueError, 'review_nonreviewable_cell'):
            report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)

    def test_locked_review_without_custodian_merge_is_still_pending(self):
        study = fixture()
        results = {study['cells'][0]['cell_id']: answer(study['cells'][0])}
        reviews, _ = adjudicate(study, results)
        actual = report.build_study_report(study, results, reviews=reviews)
        self.assertEqual(actual['strategies']['direct_initial_3']['counts']['review_pending'], 1)
        self.assertEqual(actual['strategies']['direct_initial_3']['metrics']['complete_all']['numerator'], 0)
        self.assertEqual(actual['strategies']['direct_initial_3']['metrics']['complete_all']['denominator'], 9)
        self.assertEqual(actual['strategies']['direct_initial_3']['metrics']['complete_all']['state'], 'pending')

    def test_review_cannot_call_an_abstention_a_definite_solution(self):
        study = fixture()
        result = answer(study['cells'][0])
        results = {study['cells'][0]['cell_id']: result}
        reviews, adjudications = adjudicate(study, results)
        result.update(status='ask_user_terminated', answer=None, gap='An external value is required.')
        review = reviews[study['cells'][0]['cell_id']]
        review['result_sha256'] = report.result_digest(result)
        decision = adjudications['cells'][study['cells'][0]['cell_id']]
        decision.update(result_sha256=report.result_digest(result), review_sha256=report.result_digest(review))
        with self.assertRaisesRegex(ValueError, 'adjudication_answer_conflict'):
            report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)

    def test_review_cannot_call_an_incorrect_verdict_a_complete_solution(self):
        study = fixture()
        results = {study['cells'][0]['cell_id']: answer(study['cells'][0])}
        reviews, adjudications = adjudicate(study, results)
        cell_id = study['cells'][0]['cell_id']
        results[cell_id]['answer']['verdict'] = 'no_defect_supported'
        reviews[cell_id]['result_sha256'] = report.result_digest(results[cell_id])
        adjudications['cells'][cell_id].update(result_sha256=report.result_digest(results[cell_id]),
                                               review_sha256=report.result_digest(reviews[cell_id]))
        with self.assertRaisesRegex(ValueError, 'adjudication_answer_conflict'):
            report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)

    def test_anonymous_export_whitelists_fields_and_keeps_mapping_separate(self):
        study = fixture()
        result = answer(study['cells'][0])
        result.update(model='private-model-name', gold='DO_NOT_EXPORT',
                      visible_source=study['tasks'][0]['source'])
        result['answer']['cause'] = 'A mechanism needs assessment.'
        results = {study['cells'][0]['cell_id']: result}
        export = report.export_review_packets(study, results)
        packet = export['packets'][0]
        self.assertEqual(packet['answer'], result['answer'])
        self.assertEqual(packet['source'], result['visible_source'])
        self.assertEqual(export['keeper_mapping'][packet['review_id']], study['cells'][0]['cell_id'])
        for key in ('model', 'strategy', 'arm', 'gold', 'known_accrued_usd', 'task_id', 'cell_id'):
            self.assertNotIn(key, packet)
        self.assertIn('may reveal', export['blinding_limit'])
        # Opaque IDs are stable for the same exact study/result, not strategy-prefixed.
        self.assertEqual(export, report.export_review_packets(study, deepcopy(results)))
        self.assertEqual(set(packet), {'review_id', 'result_sha256', 'question', 'scenario',
                                      'assumptions', 'source', 'answer', 'first_answer', 'gap'})

    def test_usage_and_costs_are_measured_separately_from_shared_caps(self):
        study = fixture()
        result = answer(study['cells'][0])
        actual = report.build_study_report(study, {study['cells'][0]['cell_id']: result})
        usage = actual['strategies']['direct_initial_3']['actual']
        self.assertEqual(usage['requests'], 1)
        self.assertEqual(usage['input_tokens'], 20)
        self.assertEqual(usage['cache_hit_tokens'], 4)
        self.assertEqual(usage['cache_miss_tokens'], 16)
        self.assertEqual(usage['output_tokens'], 5)
        self.assertEqual(usage['reasoning_tokens'], 2)
        self.assertEqual(usage['known_accrued_usd'], '0.000012')
        self.assertEqual(usage['unknown_reserved_usd'], '0.000000')
        self.assertEqual(usage['elapsed_seconds'], 2.0)
        self.assertEqual(actual['caps']['max_requests_per_cell'], 3)
        self.assertEqual(actual['caps']['input_tokens_are_planning_limits'], True)
        self.assertIn('standard_full_miss_usd', usage)

    def test_missing_usage_stays_unknown_not_zero(self):
        study = fixture()
        result = answer(study['cells'][0])
        result['requests'][0]['response'] = {'raw_usage': None}
        result.pop('known_accrued_usd')
        result.pop('unknown_reserved_usd')
        actual = report.build_study_report(study, {study['cells'][0]['cell_id']: result})
        usage = actual['strategies']['direct_initial_3']['actual']
        self.assertIsNone(usage['input_tokens'])
        self.assertIsNone(usage['known_accrued_usd'])
        self.assertIsNone(usage['unknown_reserved_usd'])
        self.assertEqual(usage['requests_with_unknown_usage'], 1)

    def test_batch_accounting_and_runner_source_are_supported_without_top_level_aliases(self):
        study = fixture()
        result = answer(study['cells'][0])
        result.pop('known_accrued_usd')
        result.pop('unknown_reserved_usd')
        result['accounting'] = {'request_count': 1, 'known_accrued_usd': '0.000012',
                                'unknown_held_usd': '0.330000',
                                'full_miss_sensitivity_usd': '0.000012'}
        result['source'] = [{'file': 'extra.py', 'start': 1, 'end': 1, 'body': 'y = 2\n'}]
        actual = report.build_study_report(study, {study['cells'][0]['cell_id']: result})
        self.assertEqual(actual['strategies']['direct_initial_3']['actual']['unknown_reserved_usd'], '0.330000')
        self.assertEqual(actual['strategies']['direct_initial_3']['actual']['known_accrued_usd'], '0.000012')
        packet = report.export_review_packets(study, {study['cells'][0]['cell_id']: result})['packets'][0]
        self.assertEqual(packet['source'], result['source'])

    def test_duplicate_missing_strategy_or_foreign_result_rejected(self):
        study = fixture()
        malformed = deepcopy(study)
        malformed['cells'].pop()
        with self.assertRaisesRegex(ValueError, 'study_grid_invalid'):
            report.build_study_report(malformed, {})
        with self.assertRaisesRegex(ValueError, 'result_cell_unknown'):
            report.build_study_report(study, {'foreign': answer(study['cells'][0])})

    def test_invalid_task_remains_in_original_denominator_and_is_explicit(self):
        study = fixture()
        results = {cell['cell_id']: answer(cell) for cell in study['cells']}
        reviews, adjudications = adjudicate(study, results)
        adjudications['tasks']['t0'].update(valid=False, reason='Synthetic invalid case fixture.')
        actual = report.build_study_report(study, results, reviews=reviews, adjudications=adjudications)
        self.assertEqual(actual['invalid_tasks'], ['t0'])
        metric = actual['strategies']['direct_initial_3']['metrics']['complete_solvable']
        self.assertEqual(metric['denominator'], 6)
        self.assertEqual(metric['numerator'], 5)
        self.assertEqual(len(actual['pairs']['adaptive_vs_fixed']), 9)
        self.assertEqual(actual['pairs']['adaptive_vs_fixed'][0]['outcome'], 'invalid')


if __name__ == '__main__':
    unittest.main()
