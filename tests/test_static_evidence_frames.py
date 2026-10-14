"""Generic frame contract regressions; synthetic source is data, never imported."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_workspace import EvidenceWorkspace
from macr.investigation.evidence_report import stable_result
from macr.investigation import static_evidence

SOURCE = 'def process():\n    value = 7\n    return value\ndef other():\n    return 9\n'


class FrameContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='macr-frame-v2-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        (self.repo / 'actions.py').write_text(SOURCE)
        self.counter = 0

    def frame(self, line=2, name='process', file='actions.py'):
        return f'  File "{file}", line {line}' + (f', in {name}' if name is not None else '') + '\n'

    def prepare(self, trace, entries=(), overrides=None):
        self.counter += 1
        out = self.base / f'out-{self.counter}'
        profile = EvidenceProfile.select(overrides={'min_free_bytes': 1, **(overrides or {})})
        request = EvidenceRequest(str(self.repo), str(out), 'Show captured source.', list(entries), [], trace, profile)
        ws = EvidenceWorkspace(self.repo, out, profile)
        report = static_evidence.prepare_evidence(request, ws)
        self.assertFalse((out / 'COMMITTED').exists())
        self.assertEqual(report.usage['model_calls'], 0)
        self.assertEqual(report.capabilities['diagnosis_status'], 'not_performed')
        self.assertIsNone(report.capabilities['cause'])
        self.assertFalse(report.capabilities['data_flow_proven'])
        self.assertEqual(report.request.traceback, trace)
        return report

    def statuses(self, report):
        return [frame['status'] for frame in report.selection['reported_frames']]

    def shared(self, report, count=1):
        self.assertEqual(len(report.selection['resolved']), count)
        self.assertEqual(report.coverage['selected_entry_count'], count)
        ids = {item['evidence_id'] for item in report.evidence}
        self.assertEqual(len(ids), len(report.evidence))
        for row in report.selection['resolved']:
            self.assertTrue(row['evidence_id'] is None or row['evidence_id'] in ids)
        for item in report.evidence:
            self.assertEqual(item['content_id'], report.source_version['content_id'])
            self.assertEqual(item['snapshot_id'], report.source_version['snapshot_ref']['snapshot_id'])
            self.assertEqual(item['file'], 'actions.py')
            self.assertEqual(item['text'], ''.join(SOURCE.splitlines(keepends=True)[item['requested_span']['start']-1:item['requested_span']['end']])[:len(item['text'])])

    def test_explicit_selection_is_not_frame_repetition(self):
        report = self.prepare(self.frame(), ['actions.py:process'])
        self.assertEqual(self.statuses(report), ['valid'])
        self.shared(report)
        self.assertEqual(report.selection['resolved'][0]['origin'], 'explicit_entry')
        self.assertEqual(report.evidence[0]['requested_span'], {'start': 1, 'end': 3})
        self.assertEqual(report.evidence[0]['actual_span'], {'start': 1, 'end': 3})
        self.assertTrue(report.evidence[0]['complete_symbol'])

    def test_distinct_physical_lines_share_source_but_remain_valid(self):
        report = self.prepare(self.frame(2) + self.frame(3))
        self.assertEqual(self.statuses(report), ['valid', 'valid'])
        self.assertEqual([f['line'] for f in report.selection['reported_frames']], [2, 3])
        self.assertEqual(report.selection['resolved'][0]['entry'], 'actions.py:3')
        self.shared(report)

    def test_exact_repeat_uses_deepest_representative(self):
        report = self.prepare(self.frame() * 3)
        self.assertEqual(self.statuses(report), ['duplicate', 'duplicate', 'valid'])
        self.shared(report)
        self.assertEqual(report.status, 'completed')

    def test_explicit_plus_repeats_keeps_deepest_valid(self):
        report = self.prepare(self.frame() * 2, ['actions.py:process'])
        self.assertEqual(self.statuses(report), ['duplicate', 'valid'])
        self.shared(report)

    def test_normalized_paths_names_and_blank_identity(self):
        report = self.prepare(self.frame(file=str(self.repo / 'actions.py')) +
                              self.frame(name=None) + self.frame(name='process   '))
        self.assertEqual(self.statuses(report), ['duplicate', 'valid', 'valid'])
        self.assertEqual([f['file'] for f in report.selection['reported_frames']], ['actions.py'] * 3)
        self.assertEqual([f['name'] for f in report.selection['reported_frames']], ['process', '', 'process'])
        self.shared(report)

    def test_mismatch_is_validated_despite_explicit_selection(self):
        report = self.prepare(self.frame(name='wrong') * 2 + self.frame(), ['actions.py:process'])
        self.assertEqual(self.statuses(report), ['mismatch', 'mismatch', 'valid'])
        self.shared(report)
        self.assertEqual(report.status, 'partial')
        self.assertEqual(len([g for g in report.gaps if g['code'] == 'reported_frame_mismatch']), 2)

    def test_full_entry_cap_allows_admitted_reuse(self):
        report = self.prepare(self.frame(5, 'other') + self.frame(), ['actions.py:process'], {'max_entries': 1})
        self.assertEqual(self.statuses(report), ['limited', 'valid'])
        self.shared(report)
        self.assertEqual(report.status, 'partial')
        self.assertTrue(any(g['code'] == 'entry_limit' and g['required'] for g in report.gaps))

    def test_trace_only_entry_cap_preserves_deepest_priority(self):
        report = self.prepare(self.frame() + self.frame(5, 'other'), overrides={'max_entries': 1})
        self.assertEqual(self.statuses(report), ['limited', 'valid'])
        self.shared(report)
        self.assertEqual(report.selection['resolved'][0]['entry'], 'actions.py:5')
        self.assertEqual(report.evidence[0]['text'], 'def other():\n    return 9\n')
        self.assertEqual(report.status, 'partial')

    def test_repeated_denied_target_stays_limited(self):
        report = self.prepare(self.frame(5, 'other') * 3, ['actions.py:process'], {'max_entries': 1})
        self.assertEqual(self.statuses(report), ['limited'] * 3)
        self.shared(report)
        self.assertEqual(report.status, 'partial')
        self.assertTrue(all(row['entry'] == 'actions.py:process' for row in report.selection['resolved']))
        limits = [r for r in report.limits['exceeded'] if r['field'] == 'max_entries']
        self.assertTrue(limits)
        self.assertTrue(all(r['observed'] > r['effective'] == 1 for r in limits))

    def test_unresolved_slot_denial_records_actual_over_cap_occupancy(self):
        report = self.prepare(self.frame(), ['missing.py'], {'max_entries': 1})
        self.assertEqual(self.statuses(report), ['limited'])
        self.assertEqual(report.selection['resolved'], [])
        self.assertEqual(report.evidence, [])
        reason = next(r for r in report.limits['exceeded'] if r['field'] == 'max_entries')
        self.assertEqual(reason['effective'], 1)
        self.assertEqual(reason['observed'], 2)
        self.assertTrue(any(g['code'] == 'entry_unavailable' and g['required'] for g in report.gaps))

    def test_body_shortage_keeps_input_status_and_partial_source(self):
        report = self.prepare(self.frame() * 2, ['actions.py:process'], {'max_body_bytes': 8})
        self.assertEqual(self.statuses(report), ['duplicate', 'valid'])
        self.shared(report)
        self.assertFalse(report.evidence[0]['complete_symbol'])
        self.assertFalse(report.evidence[0]['complete_range'])
        self.assertEqual(report.evidence[0]['text'], 'def proc')
        self.assertEqual(report.evidence[0]['actual_span'], {'start': 1, 'end': 1})
        self.assertEqual(report.observations['facts'], [])
        self.assertEqual(report.status, 'partial')
        self.assertTrue(any(g['code'] == 'body_truncated' and g['required'] for g in report.gaps))

    def test_ref_shortage_admits_identity_without_fabricating_reference(self):
        report = self.prepare(self.frame() * 2, ['actions.py:other', 'actions.py:process'], {'max_refs': 1})
        self.assertEqual(self.statuses(report), ['duplicate', 'valid'])
        self.shared(report, 2)
        self.assertIsNone(report.selection['resolved'][1]['evidence_id'])
        self.assertEqual(len(report.evidence), 1)
        self.assertEqual(report.status, 'partial')
        self.assertTrue(any(g['code'] == 'ref_limit' and g['required'] for g in report.gaps))

    def test_early_missing_and_out_of_range_are_terminal(self):
        for trace, reason in ((self.frame(file='missing.py'), 'entry_unavailable'),
                              (self.frame(99), 'line_unavailable')):
            with self.subTest(reason=reason):
                report = self.prepare(trace * 2)
                self.assertEqual(self.statuses(report), ['invalid', 'invalid'])
                self.assertEqual(report.status, 'partial')
                self.assertTrue(any(g['code'] == reason and g['required'] for g in report.gaps))

    def test_early_ambiguity_and_declaration_failure_are_terminal(self):
        (self.repo / 'actions.py').write_text('def process():\n    pass\ndef process():\n    pass\n')
        # Inject ambiguity at the existing declaration-resolution seam, not an invalid request.
        with patch.object(static_evidence, '_declaration', return_value=(1, 4)):
            report = self.prepare(self.frame() * 2)
        self.assertEqual(self.statuses(report), ['invalid', 'invalid'])
        self.assertTrue(report.selection['ambiguous'])
        self.assertEqual(len(report.selection['ambiguous'][0]['candidates']), 2)
        self.assertEqual(report.selection['ambiguous'][0]['omitted_count'], 0)
        self.assertTrue(any(g['code'] == 'entry_ambiguous' and g['required'] for g in report.gaps))
        (self.repo / 'actions.py').write_text('@unknown\ndef process():\n    return 7\n')
        with patch.object(static_evidence, '_decorator_spans', return_value={}):
            report = self.prepare(self.frame(3) * 2)
        self.assertEqual(self.statuses(report), ['invalid', 'invalid'])
        self.assertTrue(any(g['code'] == 'declaration_unavailable' and g['required'] for g in report.gaps))

    def test_unsuccessful_input_is_never_duplicate(self):
        report = self.prepare(self.frame(file='/outside/actions.py') * 2 +
                              self.frame(file='.env') * 2 + '  File malformed\n' * 2)
        self.assertEqual(self.statuses(report), ['external', 'external', 'forbidden', 'forbidden', 'invalid', 'invalid'])
        self.assertEqual(report.evidence, [])

    def test_stable_result_preserves_v2_and_semantic_status(self):
        report = self.prepare(self.frame() * 2)
        self.assertEqual(report.run['selection_version'], 'exact-qualified-physical-span-v2')
        stable = stable_result(report)
        self.assertEqual(stable['run']['selection_version'], report.run['selection_version'])
        self.assertEqual(stable, stable_result(self.prepare(self.frame() * 2)))
        historical = copy.deepcopy(report)
        historical.run['selection_version'] = 'exact-qualified-physical-span-v1'
        self.assertNotEqual(stable, stable_result(historical))
        altered = copy.deepcopy(report)
        altered.selection['reported_frames'][0]['status'] = 'valid'
        self.assertNotEqual(stable, stable_result(altered))

    def test_global_budget_failure_does_not_synthesize_frame_success(self):
        report = self.prepare(self.frame() * 2, overrides={'max_io_bytes': 512})
        self.assertIsNone(report.source_version)
        self.assertEqual(report.evidence, [])
        self.assertFalse(report.selection.get('reported_frames'))
        self.assertTrue(any(limit['field'] == 'max_io_bytes' for limit in report.limits['exceeded']))

    def test_partial_excerpt_retains_verified_symbol_without_derived_facts(self):
            (self.repo / 'actions.py').write_text('def process():\n' + '    # neutral padding line\n' * 120 + '    return hidden_tail()\n')
            report = self.prepare('', ['actions.py:process'], {'max_body_bytes':1024})
            self.assertEqual(report.status, 'partial')
            self.assertTrue(any(g['code']=='body_truncated' and g['required'] for g in report.gaps))
            evidence = report.evidence[0]
            self.assertFalse(evidence['complete_symbol'])
            symbols = {s['symbol_id']:s for s in report.observations['symbols']}
            self.assertIn(evidence['symbol_id'], symbols)
            symbol = symbols[evidence['symbol_id']]
            self.assertEqual((symbol['file'],symbol['qualified_name'],symbol['start'],symbol['end']), ('actions.py','process',1,122))
            self.assertEqual(report.observations['facts'], [])
            self.assertEqual(report.observations['bindings'], [])
            self.assertEqual(report.observations['relations'], [])
            self.assertNotIn('hidden_tail', evidence['text'])
