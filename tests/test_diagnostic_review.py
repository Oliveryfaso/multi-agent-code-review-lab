import copy
import json
import pickle
import unittest
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import FakeClock
from macr.evals import diagnostic_review
import test_diagnostic_compare as fixtures


class DiagnosticReviewTests(unittest.TestCase):
    prepare = fixtures.DiagnosticCompareTests.prepare
    response = fixtures.DiagnosticCompareTests.response
    proof = fixtures.DiagnosticCompareTests.proof
    record = fixtures.DiagnosticCompareTests.record
    review = fixtures.DiagnosticCompareTests.review

    def setUp(self):
        fixtures.DiagnosticCompareTests.setUp(self)
        self.prepare()
        row = self.record()
        self.reviews = {'model-a/defect': self.review(row)}
        self.clock = FakeClock()
        clock_patch = patch.object(diagnostic_review, 'monotonic', self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(diagnostic_review._ISSUED.clear)

    def begin(self):
        return diagnostic_review.begin_finish_review(self.root)

    def save(self, phase=None, **kwargs):
        result = diagnostic_review.save_finish_review(self.root, reviews=self.reviews,
                                                     phase=phase, **kwargs)
        report = json.loads(Path(result['comparison']['json']).read_text())
        timing = json.loads(Path(result['timing']).read_text())
        return result, report, timing

    def assert_pending(self, report, timing, reason):
        self.assertEqual(timing['status'], 'pending')
        self.assertEqual(timing['pending_reason'], reason)
        self.assertEqual(report['models'][0]['diagnostic_passes'], 0)
        self.assertIsNone(report['models'][0]['diagnostic_rate'])
        self.assertNotIn('review', report['cases'][0])
        self.assertEqual(timing['whole_run_compliance'], 'not_assessed')
        self.assertIsNone(timing['timing_record_persistence_within_budget'])

    def assert_durable_pending(self):
        claim = json.loads((self.root / diagnostic_review.CONSUME_RECORD).read_text())
        self.assertEqual((claim['default_authority'], claim['review_acceptance']), ('pending', 'not_accepted'))
        self.assertFalse((self.root / diagnostic_review.AUTHORITY_RECORD).exists())
        with self.assertRaisesRegex(ValueError, 'already_consumed'):
            self.begin()

    def test_same_process_review_saved_before_deadline_only_certifies_checkpoints(self):
        phase = self.begin()
        self.clock.advance(299.999)
        result, report, timing = self.save(phase)
        self.assertEqual(result['finish_status'], 'reviews_accepted_at_checkpoints')
        self.assertTrue(report['cases'][0]['diagnostic_passed'])
        self.assertEqual(timing['status'], 'candidate_not_accepted')
        self.assertEqual(timing['origin'], 'patched_clock_fixture')
        authority = json.loads(Path(result['authority']).read_text())
        claim = json.loads((self.root / diagnostic_review.CONSUME_RECORD).read_text())
        self.assertEqual(authority['status'], 'accepted_at_checkpoints')
        self.assertEqual(authority['claim_nonce'], claim['nonce'])
        self.assertEqual(authority['comparison'], result['comparison'])
        self.assertEqual([c['elapsed_seconds'] for c in authority['checkpoints']], [299.999] * 4)
        self.assertEqual(authority['whole_run_compliance'], 'not_assessed')
        self.assertIsNone(authority['authority_write_within_budget'])

    def test_deadline_equality_and_later_save_are_pending_without_reset(self):
        phase = self.begin()
        self.clock.advance(300)
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_deadline_reached')
        self.assert_durable_pending()
        phase.used = False
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_claim_already_consumed')

    def test_missing_or_serialized_origin_is_pending_and_cannot_restart(self):
        _, report, timing = self.save({'start_monotonic_seconds': 0, 'fully_compliant': True})
        self.assert_pending(report, timing, 'finish_token_missing_or_consumed')
        self.assert_durable_pending()

    def test_marker_cannot_reconstruct_token_or_recover_registered_origin(self):
        phase = self.begin()
        forged = diagnostic_review._FinishReview()
        forged.record = json.loads((self.root / diagnostic_review.START_RECORD).read_text())
        forged.clock, forged.used = lambda: 0, False
        _, report, timing = self.save(forged)
        self.assert_pending(report, timing, 'finish_token_missing_or_consumed')
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_claim_already_consumed')
        self.assert_durable_pending()

    def test_token_copy_or_pickle_cannot_create_a_registered_identity(self):
        phase = self.begin()
        with self.assertRaises(TypeError):
            pickle.dumps(phase)
        with self.assertRaises(TypeError):
            copy.copy(phase)
        copied = object.__new__(diagnostic_review._FinishReview)
        copied.__dict__.update(phase.__dict__)
        _, report, timing = self.save(copied)
        self.assert_pending(report, timing, 'finish_token_missing_or_consumed')
        self.assert_durable_pending()

    def test_mutable_token_attributes_cannot_override_real_deadline(self):
        phase = self.begin()
        phase.record = {'start_monotonic_seconds': 300}
        phase.clock, phase.last, phase.used = lambda: 0, 0, False
        self.clock.advance(300)
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_deadline_reached')
        self.assert_durable_pending()

    def test_consumed_token_cannot_accept_more_reviews_even_after_used_reset(self):
        phase = self.begin()
        first, _, _ = self.save(phase)
        phase.used = False
        result, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_claim_already_consumed')
        self.assertNotEqual(first['comparison'], result['comparison'])
        self.assertEqual(json.loads(Path(first['authority']).read_text())['comparison'], first['comparison'])

    def test_new_process_without_registry_cannot_restore_same_token(self):
        phase = self.begin()
        diagnostic_review._ISSUED.clear()
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_token_missing_or_consumed')
        self.assert_durable_pending()

    def test_cross_process_pid_observation_is_pending(self):
        phase = self.begin()
        pid = diagnostic_review.os.getpid()
        with patch.object(diagnostic_review.os, 'getpid', return_value=pid + 1):
            _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_origin_different_process_or_comparison')
        self.assert_durable_pending()

    def test_invalid_monotonic_never_awards_a_pass(self):
        with patch.object(diagnostic_review, 'monotonic', return_value=float('nan')):
            phase = self.begin()
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'monotonic_unavailable')
        self.assert_durable_pending()

    def test_clock_rollback_is_pending(self):
        self.clock.advance(100)
        phase = self.begin()
        self.clock.now = 99
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'monotonic_went_backwards')
        self.assert_durable_pending()

    def test_clock_failure_after_start_is_pending(self):
        phase = self.begin()
        with patch.object(diagnostic_review, 'monotonic', side_effect=OSError('synthetic clock failure')):
            _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'monotonic_unavailable')
        self.assert_durable_pending()

    def test_review_finishes_late_and_is_discarded_before_comparison_save(self):
        phase = self.begin()
        compare = self.compare.compare_results

        def slow_compare(*args, **kwargs):
            value = compare(*args, **kwargs)
            self.clock.advance(300)
            return value

        with patch.object(self.compare, 'compare_results', side_effect=slow_compare):
            _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_deadline_reached')
        self.assertEqual(timing['superseded_not_accepted'], [])
        self.assert_durable_pending()

    def test_save_crosses_deadline_preserves_late_evidence_but_returns_pending(self):
        phase = self.begin()
        save = self.compare.save_comparison

        def slow_save(*args, **kwargs):
            value = save(*args, **kwargs)
            self.clock.advance(300)
            return value

        with patch.object(self.compare, 'save_comparison', side_effect=slow_save):
            result, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_deadline_reached')
        self.assertEqual(len(timing['superseded_not_accepted']), 1)
        late = timing['superseded_not_accepted'][0]
        self.assertNotEqual(late, result['comparison'])
        self.assertTrue(json.loads(Path(late['json']).read_text())['cases'][0]['diagnostic_passed'])
        sidecar = json.loads((Path(late['json']).parent / 'finish-phase.json').read_text())
        self.assertEqual(sidecar['status'], 'superseded_not_accepted')
        self.assertEqual(sidecar['consume_claim'], result['authority'])
        self.assert_durable_pending()

    def test_second_pending_save_failure_still_has_durable_pending_authority(self):
        phase = self.begin()
        save = self.compare.save_comparison
        calls = []

        def fail_second(*args, **kwargs):
            if calls:
                raise OSError('synthetic second save failure')
            paths = save(*args, **kwargs)
            calls.append(paths)
            self.clock.advance(300)
            return paths

        with patch.object(self.compare, 'save_comparison', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.save(phase)
        self.assert_durable_pending()
        late = json.loads((Path(calls[0]['json']).parent / 'finish-phase.json').read_text())
        self.assertEqual(late['status'], 'superseded_not_accepted')
        self.assertTrue(json.loads(Path(calls[0]['json']).read_text())['cases'][0]['diagnostic_passed'])

    def test_sidecar_write_failure_never_seals_a_reviewed_score(self):
        phase = self.begin()
        write = self.compare._write

        def fail_sidecar(out, name, value):
            if name.endswith('/finish-phase.json'):
                raise OSError('synthetic sidecar failure')
            return write(out, name, value)

        with patch.object(self.compare, '_write', side_effect=fail_sidecar):
            with self.assertRaises(OSError):
                self.save(phase)
        self.assert_durable_pending()
        reports = list(self.root.glob('report-*/comparison.json'))
        self.assertEqual(len(reports), 1)
        self.assertTrue(json.loads(reports[0].read_text())['cases'][0]['diagnostic_passed'])

    def test_sidecar_crossing_deadline_supersedes_candidate_and_saves_pending(self):
        phase = self.begin()
        write = self.compare._write
        crossed = []

        def slow_sidecar(out, name, value):
            write(out, name, value)
            if name.endswith('/finish-phase.json') and not crossed:
                crossed.append(name)
                self.clock.advance(300)

        with patch.object(self.compare, '_write', side_effect=slow_sidecar):
            _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_deadline_reached')
        self.assertEqual(len(timing['superseded_not_accepted']), 1)
        self.assert_durable_pending()

    def test_authority_write_failure_leaves_pending_claim_and_consumes_registry(self):
        phase = self.begin()
        write = self.compare._write

        def fail_authority(out, name, value):
            if name == diagnostic_review.AUTHORITY_RECORD:
                raise OSError('synthetic authority failure')
            return write(out, name, value)

        with patch.object(self.compare, '_write', side_effect=fail_authority):
            with self.assertRaises(OSError):
                self.save(phase)
        self.assert_durable_pending()
        phase.used = False
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_claim_already_consumed')

    def test_authority_fsync_failure_leaves_only_unpublished_temp_and_pending_claim(self):
        phase = self.begin()
        atomic_write = self.compare.atomic_write

        def fail_authority_fsync(path, data):
            if path.name == diagnostic_review.AUTHORITY_RECORD:
                with patch('macr.memory.atomic_file.os.fsync', side_effect=OSError('synthetic authority fsync failure')):
                    return atomic_write(path, data)
            return atomic_write(path, data)

        with patch.object(self.compare, 'atomic_write', side_effect=fail_authority_fsync):
            with self.assertRaisesRegex(OSError, 'fsync failure'):
                self.save(phase)
        self.assert_durable_pending()
        temps = list(self.root.glob(diagnostic_review.AUTHORITY_RECORD + '-*.tmp'))
        self.assertEqual(len(temps), 1)
        self.assertEqual(json.loads(temps[0].read_text())['status'], 'accepted_at_checkpoints')

    def test_save_failure_consumes_claim_and_origin_only_pending_can_follow(self):
        phase = self.begin()
        with patch.object(self.compare, 'save_comparison', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                self.save(phase)
        self.assert_durable_pending()
        phase.used = False
        _, report, timing = self.save(phase)
        self.assert_pending(report, timing, 'finish_claim_already_consumed')

    def test_public_clock_or_compliance_keywords_are_rejected(self):
        with self.assertRaises(TypeError):
            diagnostic_review.begin_finish_review(self.root, clock=self.clock)
        with self.assertRaises(TypeError):
            self.save(clock=self.clock)
        with self.assertRaises(TypeError):
            self.save(fully_compliant=True)
        self.assertFalse((self.root / diagnostic_review.START_RECORD).exists())
        self.assertFalse((self.root / diagnostic_review.CONSUME_RECORD).exists())

    def test_invalid_review_consumes_claim_before_old_contract_validation(self):
        phase = self.begin()
        self.reviews['model-a/defect']['record_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'comparison_review_invalid'):
            self.save(phase)
        self.assert_durable_pending()
