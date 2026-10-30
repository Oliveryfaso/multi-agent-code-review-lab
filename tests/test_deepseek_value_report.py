"""Synthetic locked-review fixtures for the finite two-arm value decision."""
from copy import deepcopy
import importlib
import unittest

from test_deepseek_study_report import fixture, answer, adjudicate, flags
from macr.evals.deepseek_study_report import result_digest
from macr.evals.deepseek_policy_spend import _usage_evidence, _usd

DIRECT = 'direct_initial_3'
TOOL = 'adaptive_read_3'


def data():
    study = fixture()
    study['execution_profile'] = 'study-value-v1'
    results = {cell['cell_id']: answer(cell) for cell in study['cells']
               if cell['strategy'] in {DIRECT, TOOL}}
    for row in results.values():
        row['initial_evidence_sha256'] = 'c' * 64
        response = row['requests'][0]['response']
        response.update(model='deepseek-flash', system_fingerprint='synthetic-value-fingerprint')
        row['requests'] *= 3
        row['known_accrued_usd'] = _usd(3 * _usage_evidence(response['raw_usage'], 8192)[0])
        row['accounting'] = {'state': 'open', 'request_count': 3,
            'known_accrued_usd': row['known_accrued_usd'], 'unknown_held_usd': '0.000000'}
        row['review_evidence'] = {'source': [
            {'file': 'module.py', 'start': 1, 'end': 1, 'body': 'x = 1\n', 'extra': 'DROP'}]}
    reviews, decisions = adjudicate(study, results)
    for qualification in decisions['tasks'].values():
        qualification['stratum'] = 'full_context'
    return study, results, reviews, decisions


def rebind(results, reviews, decisions, cell_id, judgment):
    reviews[cell_id].update(result_sha256=result_digest(results[cell_id]), flags=judgment)
    decisions['cells'][cell_id].update(result_sha256=result_digest(results[cell_id]),
        review_sha256=result_digest(reviews[cell_id]), flags=judgment)


def tool_wins(results, reviews, decisions):
    for index in range(2):
        key = f't{index}:{DIRECT}'
        results[key]['answer'] = {'verdict': 'unknown', 'cause': None, 'when': None,
            'refs': [], 'unknowns': ['Synthetic unnecessary gap.']}
        rebind(results, reviews, decisions, key, flags(unnecessary_abstention=True))


def determinate_records(results, reviews, decisions):
    records = {}
    for cell_id, review in reviews.items():
        row = results[cell_id]
        verdict = row['answer']['verdict'] if row.get('answer') else None
        judgment = decisions['cells'][cell_id]['flags']
        category = decisions['tasks'][row['task_id']]['category']
        error = (judgment['false_positive'] or judgment['false_release']
            or (category == 'external_gap' and verdict != 'unknown'
                and row['status'] != 'ask_user_terminated'))
        records[cell_id] = {'result_sha256': result_digest(row),
            'review_sha256': result_digest(review), 'status': 'locked',
            'determinate_error': bool(error), 'rationale': 'Synthetic mechanism review fixture.'}
    return records


def reviewed_report(api, study, results, reviews, decisions):
    return api.build_value_report(study, results, reviews=reviews, adjudications=decisions,
        determinate_reviews=determinate_records(results, reviews, decisions))


class ValueReportTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('macr.evals.deepseek_value_report')
        except ModuleNotFoundError:
            self.fail('the selected two-arm value report is not implemented')

    def test_only_eighteen_selected_cells_and_unscored_pairs_never_tie(self):
        study, results, _, _ = data()
        report = self.api.build_value_report(study, results)
        self.assertEqual(report['planned_cells'], 18)
        self.assertEqual(len(report['cells']), 18)
        self.assertEqual(len(report['pairs']), 9)
        self.assertEqual(set(report['strategies']), {DIRECT, TOOL})
        self.assertTrue(all(pair['outcome'] == 'pending' for pair in report['pairs']))
        self.assertEqual(report['selection']['decision'], 'inconclusive_default_direct')
        self.assertFalse(report['first_answers_are_additional_samples'])
        with self.assertRaisesRegex(ValueError, 'value_unselected_result'):
            extra = study['cells'][1]
            self.api.build_value_report(study, results | {extra['cell_id']: answer(extra)})
        with self.assertRaisesRegex(ValueError, 'value_profile_invalid'):
            self.api.build_value_report(study | {'execution_profile': 'study-evidence-v1'}, results)

    def test_locked_full_scores_equal_quality_prefers_direct_without_net_gain(self):
        study, results, reviews, decisions = data()
        report = reviewed_report(self.api, study, results, reviews, decisions)
        self.assertEqual(report['selection']['decision'], 'prefer_direct')
        self.assertEqual(report['pair_counts']['tie'], 9)
        for strategy in (DIRECT, TOOL):
            metrics = report['strategies'][strategy]['metrics']
            self.assertEqual(metrics['complete_solvable'], {'numerator': 6, 'denominator': 6, 'state': 'final'})
            self.assertEqual(metrics['necessary_abstention'], {'numerator': 3, 'denominator': 3, 'state': 'final'})
            self.assertEqual(report['strategies'][strategy]['actual']['requests'], 27)

    def test_two_net_benefits_and_resource_envelope_can_retain_experimental_tool(self):
        study, results, reviews, decisions = data()
        tool_wins(results, reviews, decisions)
        report = reviewed_report(self.api, study, results, reviews, decisions)
        self.assertEqual(report['pair_counts']['win'], 2)
        self.assertEqual(report['selection']['decision'], 'retain_tool_experimental')
        self.assertEqual(report['selection']['net_beneficial_pairs'], 2)
        self.assertEqual(report['strategies'][DIRECT]['metrics']['unnecessary_abstention']['numerator'], 2)

    def test_added_external_claim_or_excess_cost_or_latency_prevents_retention(self):
        for variant in ('external_claim', 'cost', 'latency'):
            with self.subTest(variant=variant):
                study, results, reviews, decisions = data()
                tool_wins(results, reviews, decisions)
                if variant == 'external_claim':
                    key = f't6:{TOOL}'
                    results[key]['answer'].update(verdict='supported_defect', cause='Synthetic unsupported claim.', when='Now.')
                    rebind(results, reviews, decisions, key, flags())
                else:
                    for cell_id, row in results.items():
                        if row['strategy'] != TOOL:
                            continue
                        if variant == 'cost':
                            for request in row['requests']:
                                request['response']['raw_usage']['completion_tokens'] = 100
                            row['known_accrued_usd'] = _usd(sum(_usage_evidence(
                                r['response']['raw_usage'], 8192)[0] for r in row['requests']))
                            row['accounting']['known_accrued_usd'] = row['known_accrued_usd']
                        else:
                            row['elapsed_seconds'] = 2.51
                        rebind(results, reviews, decisions, cell_id, reviews[cell_id]['flags'])
                report = reviewed_report(self.api, study, results, reviews, decisions)
                self.assertEqual(report['selection']['decision'], 'prefer_direct')
                self.assertFalse(report['selection']['checks'][{
                    'external_claim': 'no_added_unsupported', 'cost': 'cost_within_125_percent',
                    'latency': 'time_within_125_percent'}[variant]])

    def test_missing_unknown_pending_identity_and_bad_billing_are_inconclusive(self):
        for variant in ('missing', 'unknown', 'pending', 'identity', 'billing', 'evidence'):
            with self.subTest(variant=variant):
                study, results, reviews, decisions = data()
                tool_wins(results, reviews, decisions)
                key = f't8:{TOOL}'
                if variant in {'missing', 'unknown', 'pending'}:
                    reviews.pop(key)
                    decisions['cells'].pop(key)
                if variant == 'missing':
                    results.pop(key)
                elif variant == 'unknown':
                    results[key].update(status='remote_outcome_unknown', answer=None,
                        error_code='remote_outcome_unknown', unknown_reserved_usd='0.330000')
                    results[key]['accounting']['state'] = 'halted_unknown'
                elif variant == 'identity':
                    results[key]['requests'][0]['response']['system_fingerprint'] = 'changed-fixture'
                    rebind(results, reviews, decisions, key, reviews[key]['flags'])
                elif variant == 'billing':
                    results[key]['known_accrued_usd'] = '0.000001'
                    rebind(results, reviews, decisions, key, reviews[key]['flags'])
                elif variant == 'evidence':
                    results[key]['initial_evidence_sha256'] = 'd' * 64
                    rebind(results, reviews, decisions, key, reviews[key]['flags'])
                report = reviewed_report(self.api, study, results, reviews, decisions)
                self.assertEqual(report['selection']['decision'], 'inconclusive_default_direct')
                if variant == 'missing':
                    self.assertEqual(report['pairs'][-1]['outcome'], 'not_comparable')
                if variant == 'pending':
                    self.assertEqual(report['pairs'][-1]['outcome'], 'pending')

    def test_known_contract_failure_keeps_full_denominator_and_never_gets_semantic_credit(self):
        study, results, reviews, decisions = data()
        key = f't0:{DIRECT}'
        results[key].update(status='failed', error_code='diagnosis_json_invalid', answer=None)
        reviews.pop(key)
        decisions['cells'].pop(key)
        report = reviewed_report(self.api, study, results, reviews, decisions)
        self.assertEqual(report['pair_counts']['win'], 1)
        self.assertEqual(report['pairs'][0]['comparison_basis'], 'known_contract_failure_disposition')
        self.assertEqual(report['strategies'][DIRECT]['metrics']['complete_solvable']['denominator'], 6)
        self.assertEqual(report['strategies'][DIRECT]['metrics']['complete_solvable']['numerator'], 5)
        with self.assertRaisesRegex(ValueError, 'review_nonreviewable_cell'):
            self.api.build_value_report(study, results,
                reviews=reviews | {key: {'result_sha256': result_digest(results[key]), 'status': 'locked',
                    'rationale': 'Cannot turn a protocol failure into success.', 'flags': flags(full_solution=True)}},
                adjudications=decisions)

    def test_review_binding_and_verdict_category_contradictions_are_rejected(self):
        study, results, reviews, decisions = data()
        modified = deepcopy(results)
        modified[f't0:{TOOL}']['answer']['cause'] = 'Changed after locking.'
        with self.assertRaisesRegex(ValueError, 'review_result_binding'):
            reviewed_report(self.api, study, modified, reviews, decisions)
        key = f't3:{TOOL}'
        rebind(results, reviews, decisions, key, flags(false_positive=True))
        with self.assertRaisesRegex(ValueError, 'adjudication_answer_conflict'):
            reviewed_report(self.api, study, results, reviews, decisions)

    def test_determinate_review_is_separate_required_and_cannot_contradict_known_error(self):
        study, results, reviews, decisions = data()
        tool_wins(results, reviews, decisions)
        report = self.api.build_value_report(study, results, reviews=reviews, adjudications=decisions)
        self.assertEqual(report['selection']['decision'], 'inconclusive_default_direct')
        records = determinate_records(results, reviews, decisions)
        records[f't8:{TOOL}']['determinate_error'] = True
        with self.assertRaisesRegex(ValueError, 'value_determinate_answer_conflict'):
            self.api.build_value_report(study, results, reviews=reviews,
                adjudications=decisions, determinate_reviews=records)
        key = f't6:{TOOL}'
        results[key]['answer'].update(verdict='supported_defect', cause='Unsupported fixture.', when='Now.')
        rebind(results, reviews, decisions, key, flags())
        records = determinate_records(results, reviews, decisions)
        records[key]['determinate_error'] = False
        with self.assertRaisesRegex(ValueError, 'value_determinate_answer_conflict'):
            self.api.build_value_report(study, results, reviews=reviews,
                adjudications=decisions, determinate_reviews=records)

    def test_same_verdict_wrong_mechanism_blocks_retention_even_with_two_net_wins(self):
        study, results, reviews, decisions = data()
        tool_wins(results, reviews, decisions)
        key = f't2:{DIRECT}'
        results[key]['answer'].update(verdict='unknown', cause=None, when=None,
            unknowns=['Synthetic unnecessary gap.'])
        rebind(results, reviews, decisions, key, flags(unnecessary_abstention=True))
        key = f't3:{TOOL}'
        results[key]['answer']['cause'] = 'Synthetic incorrect mechanism with unchanged verdict.'
        rebind(results, reviews, decisions, key, flags(partial=True))
        records = determinate_records(results, reviews, decisions)
        records[key]['determinate_error'] = True
        report = self.api.build_value_report(study, results, reviews=reviews,
            adjudications=decisions, determinate_reviews=records)
        self.assertEqual(report['selection']['net_beneficial_pairs'], 2)
        self.assertFalse(report['selection']['checks']['no_added_unsupported'])
        self.assertEqual(report['selection']['decision'], 'prefer_direct')

    def test_value_rejects_partial_necessary_abstention_accepted_by_shared_flags(self):
        from macr.evals.deepseek_study_report import _review_inputs
        study, results, reviews, decisions = data()
        key = f't6:{TOOL}'
        rebind(results, reviews, decisions, key, flags(reasonable_abstention=True, partial=True))
        tasks = {task['id']: task for task in study['tasks']}
        cells = {cell['cell_id']: cell for cell in study['cells'] if cell['cell_id'] in results}
        _review_inputs(tasks, cells, results, reviews, decisions)
        with self.assertRaisesRegex(ValueError, 'value_partial_abstention_conflict'):
            reviewed_report(self.api, study, results, reviews, decisions)

    def test_export_uses_only_actual_evidence_and_keeps_mapping_separate(self):
        study, results, _, _ = data()
        selected = next(iter(results))
        bundle = self.api.export_value_packets(study, {selected: results[selected]})
        self.assertEqual(len(bundle['packets']), 1)
        packet = bundle['packets'][0]
        self.assertEqual(bundle['keeper_mapping'], {packet['review_id']: selected})
        self.assertEqual(packet['source'], [
            {'file': 'module.py', 'start': 1, 'end': 1, 'body': 'x = 1\n'}])
        self.assertFalse({'strategy', 'model', 'accounting', 'cell_id', 'task_id'} & set(packet))
        self.assertIn('reveal', bundle['blinding_limit'])


if __name__ == '__main__':
    unittest.main()
