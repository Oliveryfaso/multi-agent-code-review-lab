"""Trusted product tests: fixture bytes are parsed as data, never executed."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_workspace import EvidenceWorkspace
from macr.investigation.validation import ContractError


class StaticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='macr-task3-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.counter = 0

    def prepare(self, files, entries=None, symbols=None, trace='', overrides=None):
        self.assertIsNotNone(importlib.util.find_spec('macr.investigation.static_evidence'),
                             'missing pure static preparation')
        from macr.investigation.static_evidence import prepare_evidence
        for name, data in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        self.counter += 1
        profile = EvidenceProfile.select(overrides={'min_free_bytes': 1, **(overrides or {})})
        out = self.base / ('out' + str(self.counter))
        request = EvidenceRequest(str(self.repo), str(out), 'Show the source evidence.',
                                  entries or [], symbols or [], trace, profile)
        ws = EvidenceWorkspace(self.repo, out, profile)
        result = prepare_evidence(request, ws)
        self.assertFalse((out / 'COMMITTED').exists())
        return result, ws

    def test_exact_symbol_one_hop_crossfile_argument_order(self):
        report, _ = self.prepare({'entry.py': 'from lib import take as use\ndef start():\n    return use(right=True, left="é")\n',
                                 'lib.py': 'def take(left, *, right):\n    return left\n'}, ['entry.py:start'])
        self.assertEqual(report.status, 'completed')
        binding = report.observations['bindings'][0]
        self.assertEqual([(a['parameter'], a['expression'], a['type']) for a in binding['arguments']],
                         [('left', '"é"', 'str'), ('right', 'True', 'bool')])
        ids = {e['evidence_id'] for e in report.evidence}
        self.assertTrue(set(binding['evidence_ids']) <= ids)
        self.assertEqual(len(binding['evidence_ids']), 2)
        self.assertEqual(report.capabilities['diagnosis_status'], 'not_performed')
        self.assertIsNone(report.capabilities['cause'])
        self.assertEqual(report.usage['model_calls'], 0)
        self.assertIsNone(report.usage['tokens'])

    def test_line_selects_innermost_nested_function(self):
        report, _ = self.prepare({'a.py': 'def outer():\n    def inner():\n        return 4\n    return inner()\n'}, ['a.py:3'])
        resolved = report.selection['resolved'][0]
        self.assertTrue(resolved['symbol_id'].endswith(':outer.inner:2'))
        excerpt = next(e for e in report.evidence if e['evidence_id'] == resolved['evidence_id'])
        self.assertEqual(excerpt['text'], '    def inner():\n        return 4\n')
        self.assertTrue(excerpt['complete_symbol'])
        self.assertTrue(any(u['reason'] == 'ordinary_function_required' for u in report.observations['unknowns']))
        self.assertEqual(report.status, 'completed')

    def test_duplicate_and_crossfile_homonyms_are_ambiguous_with_omission(self):
        report, _ = self.prepare({'a.py': 'def same():\n    return 1\ndef same():\n    return 2\n',
                                 'b.py': 'def same():\n    return 3\n'}, symbols=['same'], overrides={'max_candidates': 1})
        self.assertEqual(report.status, 'partial')
        ambiguity = report.selection['ambiguous'][0]
        self.assertEqual(len(ambiguity['candidates']), 1)
        self.assertEqual(ambiguity['omitted_count'], 2)
        self.assertEqual(report.evidence, [])
        self.assertTrue(any(g['required'] for g in report.gaps))

    def test_file_symbol_does_not_choose_duplicate_definition(self):
        report, _ = self.prepare({'a.py': 'def same():\n    pass\ndef same():\n    pass\n'}, ['a.py:same'])
        self.assertEqual(len(report.selection['ambiguous'][0]['candidates']), 2)
        self.assertEqual(report.status, 'partial')

    def test_explicit_file_module_and_faithful_range(self):
        report, _ = self.prepare({'a.py': 'def a():\r\n    return "x\u2028y"\r# tail'}, ['a.py', 'a.py:2-3'])
        self.assertEqual(report.status, 'completed')
        ranges = [e for e in report.evidence if e['origin'] == 'explicit_range']
        self.assertEqual(ranges[0]['text'], '    return "x\u2028y"\r# tail')
        self.assertEqual(ranges[0]['requested_span'], {'start': 2, 'end': 3})
        self.assertEqual(ranges[0]['actual_span'], {'start': 2, 'end': 3})
        self.assertFalse(ranges[0]['complete_symbol'])
        self.assertEqual(report.coverage['parsed_python_count'], 1)

    def test_bad_python_and_uppercase_py_are_readable_without_ast_facts(self):
        report, _ = self.prepare({'bad.py': 'def broken(\n', 'notes.PY': 'def text():\n    return 1\n'}, ['bad.py', 'notes.PY'])
        self.assertEqual(report.status, 'completed')
        self.assertEqual({e['parse_status'] for e in report.evidence}, {'parse_unavailable', 'text_only'})
        self.assertEqual(report.coverage['eligible_text_count'], 2)
        self.assertEqual(report.coverage['parsed_python_count'], 0)
        self.assertEqual(report.observations['facts'], [])
        self.assertEqual(report.observations['bindings'], [])

    def test_empty_file_has_zero_lines_and_manifest_bound_metadata_no_excerpt(self):
        report, _ = self.prepare({'empty.py': b''}, ['empty.py'])
        self.assertEqual(report.status, 'completed')
        self.assertEqual(report.evidence, [])
        entry = report.selection['resolved'][0]
        self.assertEqual(entry['empty_source']['physical_line_count'], 0)
        self.assertEqual(entry['empty_source']['size_bytes'], 0)
        self.assertEqual(entry['empty_source']['content_id'], report.source_version['content_id'])
        self.assertIsNone(entry['requested_span'])
        self.assertEqual(report.coverage['full_entry_count'], 1)

    def test_decorated_declaration_includes_decorator_without_rewriting_symbol_id(self):
        report, _ = self.prepare({'a.py': '@unknown\ndef decorated():\n    return 4\n'}, ['a.py:decorated'])
        excerpt = report.evidence[0]
        self.assertEqual(excerpt['requested_span'], {'start': 1, 'end': 3})
        self.assertTrue(excerpt['symbol_id'].endswith(':decorated:2'))
        self.assertEqual(excerpt['text'], '@unknown\ndef decorated():\n    return 4\n')
        self.assertTrue(excerpt['complete_symbol'])
        self.assertEqual(report.observations['bindings'], [])

    def test_function_body_budget_is_separate_from_collector_budget(self):
        source = 'def large():\n' + '    pass\n' * 30 + '    return 99\n'
        report, _ = self.prepare({'a.py': source}, ['a.py:large'], overrides={'max_collector_bytes': 16})
        self.assertEqual(report.status, 'completed')
        self.assertEqual(report.evidence[0]['text'], source)
        self.assertTrue(report.evidence[0]['complete_symbol'])
        self.assertEqual(report.observations['facts'], [])
        self.assertTrue(any(u['reason'] == 'context_truncated' for u in report.observations['unknowns']))

    def test_truncated_last_line_is_honest_partial_without_full_symbol_facts(self):
        report, _ = self.prepare({'a.py': 'def large():\n    return "long tail"\n'}, ['a.py:large'], overrides={'max_body_bytes': 20})
        self.assertEqual(report.status, 'partial')
        excerpt = report.evidence[0]
        self.assertEqual(excerpt['text'], 'def large():\n    ret')
        self.assertEqual(excerpt['actual_span'], {'start': 1, 'end': 2})
        self.assertFalse(excerpt['complete_range'])
        self.assertFalse(excerpt['complete_symbol'])
        self.assertEqual(report.observations['facts'], [])
        reason = next(r for r in report.limits['exceeded'] if r['field'] == 'max_body_bytes')
        self.assertEqual(reason['effective'], 20)
        self.assertEqual(reason['stage'], 'source_excerpt')

    def test_trace_is_reported_root_contained_deepest_first_after_explicit(self):
        trace = f'  File "{self.repo}/a.py", line 4, in last\n  File "/outside/a.py", line 2, in external\n  File "a.py", line 2, in first\n'
        report, _ = self.prepare({'a.py': 'def first():\n    return 1\ndef last():\n    return 2\n'}, ['a.py:last'], trace=trace)
        self.assertEqual(report.selection['resolved'][0]['entry'], 'a.py:last')
        self.assertEqual(report.selection['resolved'][1]['entry'], 'a.py:2')
        self.assertEqual(report.selection['reported_frames'][1]['status'], 'external')
        self.assertEqual(report.selection['reported_frames'][0]['origin'], 'reported_traceback')
        self.assertTrue(any(g['code'] == 'reported_trace_not_verified' and not g['required'] for g in report.gaps))

    def test_trace_frame_name_validated_before_dedup(self):
        trace = '  File "a.py", line 2, in right\n  File "a.py", line 2, in wrong\n'
        report, _ = self.prepare({'a.py': 'def right():\n    return 1\n'}, trace=trace)
        self.assertEqual(report.status, 'partial')
        self.assertTrue(any(f['status'] == 'mismatch' for f in report.selection['reported_frames']))
        self.assertTrue(report.selection['unresolved'])

    def test_more_than_default_functions_and_facts_are_configurable(self):
        definitions = ''.join(f'def f{i}():\n    return {i}\n' for i in range(8))
        report, _ = self.prepare({'a.py': definitions}, symbols=[f'f{i}' for i in range(8)],
                                 overrides={'max_entries': 8, 'max_functions': 8, 'max_facts': 64})
        self.assertEqual(len(report.selection['resolved']), 8)
        self.assertEqual(len(report.observations['symbols']), 8)
        self.assertEqual(len(report.observations['facts']), 16)
        self.assertEqual(report.status, 'completed')

    def test_normal_unsupported_control_flow_is_completed_with_no_diagnosis(self):
        report, _ = self.prepare({'a.py': 'def choose(flag):\n    if flag:\n        return 2\n    return 4\n'}, ['a.py:choose'])
        self.assertEqual(report.status, 'completed')
        self.assertTrue(any(u['reason'] == 'control_flow_unsupported' for u in report.observations['unknowns']))
        self.assertFalse(report.capabilities['data_flow_proven'])
        self.assertIsNone(report.capabilities['cause'])

    def test_async_and_method_source_are_readable_but_binding_unsupported(self):
        report, _ = self.prepare({'a.py': 'async def task():\n    return 1\nclass C:\n    def method(self):\n        return 2\n'}, ['a.py:task', 'a.py:C.method'])
        self.assertEqual(report.status, 'completed')
        self.assertEqual(len(report.evidence), 2)
        self.assertTrue(all(e['complete_symbol'] for e in report.evidence))
        self.assertEqual(report.observations['bindings'], [])
        self.assertEqual(len([u for u in report.observations['unknowns'] if u['reason'] == 'ordinary_function_required']), 2)

    def test_stable_result_ignores_uuid_root_time_but_not_limits_or_gaps(self):
        self.assertIsNotNone(importlib.util.find_spec("macr.investigation.evidence_report"), "missing stable result")
        from macr.investigation.evidence_report import stable_result
        files = {'a.py': 'def a():\n    return 4\n'}
        first, _ = self.prepare(files, ['a.py:a'])
        second, _ = self.prepare(files, ['a.py:a'])
        self.assertEqual(stable_result(first), stable_result(second))
        second.gaps.append({'code': 'new_limitation', 'required': False})
        self.assertNotEqual(stable_result(first), stable_result(second))

    def test_final_validator_rejects_wrong_same_content_symbol_and_span(self):
        report, ws = self.prepare({'a.py': 'def a():\n    return 1\ndef b():\n    return 2\n'}, ['a.py:a'])
        from macr.investigation.static_evidence import validate_report_sources
        bad = copy.deepcopy(report)
        bad.evidence[0]['symbol_id'] = report.source_version['content_id'] + ':a.py:b:1'
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)
        bad = copy.deepcopy(report)
        bad.evidence[0]['requested_span']['end'] = 1
        bad.evidence[0]['actual_span']['end'] = 1
        bad.evidence[0]['text'] = 'def a():\n'
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)

    def test_final_validator_rejects_forged_fact_and_binding_alias(self):
        report, ws = self.prepare({'a.py': 'def take(x):\n    return x\ndef start():\n    return take(True)\n'}, ['a.py:start'])
        from macr.investigation.static_evidence import validate_report_sources
        bad = copy.deepcopy(report)
        bad.observations['facts'][0]['type'] = 'complex'
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)
        bad = copy.deepcopy(report)
        bad.observations['bindings'][0]['arguments'][0]['expression'] = 'False'
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)
        bad = copy.deepcopy(report)
        bad.observations['bindings'][0]['callee_id'] = bad.observations['bindings'][0]['caller_id']
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)

    def test_final_saved_source_drift_clears_effective_evidence(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.investigation.static_evidence'), 'missing pure core')
        from macr.investigation import static_evidence
        original = static_evidence.validate_report_sources
        def corrupt_then_validate(report, ws):
            ref = report.source_version['snapshot_ref']
            (Path(ref['root_ref']) / 'a.py').write_bytes(b'def a():\n    return 9\n')
            return original(report, ws)
        with patch.object(static_evidence, 'validate_report_sources', corrupt_then_validate):
            report, _ = self.prepare({'a.py': 'def a():\n    return 1\n'}, ['a.py:a'])
        self.assertEqual(report.status, 'rejected')
        self.assertEqual(report.evidence, [])
        self.assertIsNone(report.source_version)
        self.assertEqual(report.observations.get('facts', []), [])

    def test_final_validator_reads_are_charged_and_no_source_limit_is_partial(self):
        report, ws = self.prepare({'a.py': 'def a():\n    return 1\n'}, ['a.py:a'])
        from macr.investigation.static_evidence import validate_report_sources
        before = ws.budget.usage['max_io_bytes']
        before_nodes = ws.budget.usage['max_ast_nodes']
        validate_report_sources(report, ws)
        self.assertGreater(ws.budget.usage['max_io_bytes'], before)
        self.assertGreater(ws.budget.usage['max_ast_nodes'], before_nodes)
        partial, _ = self.prepare({'a.py': 'def a():\n    return 1\n'}, ['a.py:a'], overrides={'max_io_bytes': 512})
        self.assertEqual(partial.status, 'partial')
        self.assertEqual(partial.evidence, [])
        self.assertIsNone(partial.source_version)
        self.assertEqual(partial.limits['exceeded'][0]['field'], 'max_io_bytes')

    def test_protected_explicit_entry_rejects_without_effective_evidence(self):
        report, _ = self.prepare({'a.py': 'def a():\n    pass\n', '.env': 'never read'}, ['.env'])
        self.assertEqual(report.status, 'rejected')
        self.assertEqual(report.evidence, [])
        self.assertIsNone(report.source_version)

    def test_decorator_line_selects_full_declaration(self):
        report, _ = self.prepare({'a.py': '@unknown\ndef work():\n    return 3\n'}, ['a.py:1'])
        self.assertTrue(report.evidence[0]['complete_symbol'])
        self.assertEqual(report.evidence[0]['text'], '@unknown\ndef work():\n    return 3\n')

    def test_file_entry_binds_the_parsed_module_identity(self):
        report, _ = self.prepare({'a.py': 'VALUE = 3\n'}, ['a.py'])
        self.assertTrue((report.evidence[0]['symbol_id'] or '').endswith(':<module>:1'))
        self.assertTrue(report.evidence[0]['complete_symbol'])

    def test_collectors_report_actual_requested_bytes_not_cap_plus_one(self):
        source = 'def a():\n' + '    pass\n' * 20 + '    return 3\n'
        report, _ = self.prepare({'a.py': source}, ['a.py:a'], overrides={'max_collector_bytes': 12})
        reason = next(item for item in report.limits['exceeded'] if item['field'] == 'max_collector_bytes')
        self.assertEqual(reason['observed'], len(source.encode('utf-8')))

    def test_function_cap_does_not_apply_main_entry_cap_to_one_hop_sources(self):
        report, _ = self.prepare({'a.py': 'def take(x):\n    return x\ndef start():\n    return take(True)\n'},
                                 ['a.py:start'], overrides={'max_entries': 1})
        self.assertEqual(report.status, 'completed')
        self.assertEqual(len(report.observations['bindings']), 1)
        self.assertEqual(len(report.evidence), 2)

    def test_malformed_trace_frames_are_bounded_and_visible(self):
        trace = '  File malformed\n' * 4
        report, _ = self.prepare({'a.py': 'VALUE = 1\n'}, ['a.py'], trace=trace,
                                 overrides={'max_traceback_frames': 2})
        self.assertEqual(len(report.selection['reported_frames']), 2)
        self.assertEqual(report.selection['reported_frames_omitted_count'], 2)
        self.assertEqual(report.status, 'partial')
        reason = next(item for item in report.limits['exceeded'] if item['field'] == 'max_traceback_frames')
        self.assertEqual(reason['observed'], 4)

    def test_bounded_binding_cap_preserves_verified_subset_and_actual_attempt_count(self):
        report, _ = self.prepare({'a.py': 'def take(x):\n    return x\ndef start():\n    take(True)\n    take(False)\n    return take(2)\n'},
                                 ['a.py:start'], overrides={'max_bindings': 1})
        self.assertEqual(report.status, 'completed')
        self.assertEqual(len(report.observations['bindings']), 1)
        reason = next(item for item in report.limits['exceeded'] if item['field'] == 'max_bindings')
        self.assertEqual(reason['observed'], 3)

    def test_raised_collector_budget_reads_tail_beyond_legacy_ceiling(self):
        source = 'def a():\n' + '    pass\n' * 4500 + '    return 7\n'
        report, _ = self.prepare({'a.py': source}, ['a.py:a'], overrides={'max_collector_bytes': 50000})
        self.assertEqual(report.status, 'completed')
        self.assertTrue(any(item.get('kind') == 'return_type' and item.get('type') == 'int'
                            for item in report.observations['facts']))

    def test_final_validator_rejects_forged_relation_resolution_and_missing_callee_ref(self):
        report, ws = self.prepare({'a.py': 'def take(x):\n    return x\ndef start():\n    return take(True)\n'}, ['a.py:start'])
        from macr.investigation.static_evidence import validate_report_sources
        bad = copy.deepcopy(report)
        bad.observations['relations'][0]['resolution'] = 'resolved_import'
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)
        bad = copy.deepcopy(report)
        bad.evidence = [item for item in bad.evidence if item['symbol_id'].endswith(':start:3')]
        with self.assertRaises(ContractError):
            validate_report_sources(bad, ws)

    def test_nested_directories_preserve_canonical_results(self):
        report, _ = self.prepare({'z/a.py': 'def a():\n    return 1\n', 'a/b.py': 'def b():\n    return 2\n'}, ['z/a.py:a'])
        self.assertEqual(report.status, 'completed')
        self.assertEqual(report.coverage['parsed_python_count'], 2)

    def test_no_target_import_side_effect_and_no_provider_import(self):
        files = {'a.py': 'raise RuntimeError("must never execute")\ndef a():\n    return 1\n',
                 'sitecustomize.py': 'raise RuntimeError("must never execute")\n'}
        before = {name for name in __import__('sys').modules if name.startswith(('macr.providers', 'macr.execution'))}
        report, _ = self.prepare(files, ['a.py:a'])
        self.assertEqual(report.status, 'completed')
        after = {name for name in __import__('sys').modules if name.startswith(('macr.providers', 'macr.execution'))}
        self.assertEqual(before, after)

    def test_function_limit_reports_all_requested_functions_without_losing_full_main_source(self):
        files = {'a.py': ''.join(f'def f{i}():\n    return {i}\n' for i in range(4))}
        report, _ = self.prepare(files, symbols=['f0', 'f1', 'f2', 'f3'], overrides={'max_functions': 2})
        self.assertEqual(report.status, 'completed')
        self.assertEqual(len(report.evidence), 4)
        reason = next(item for item in report.limits['exceeded'] if item['field'] == 'max_functions')
        self.assertEqual(reason['observed'], 4)

    def test_ref_limit_retains_independently_verified_first_main_evidence(self):
        report, _ = self.prepare({'a.py': 'def a():\n    return 1\ndef b():\n    return 2\n'},
                                 ['a.py:a', 'a.py:b'], overrides={'max_refs': 1})
        self.assertEqual(report.status, 'partial')
        self.assertEqual(len(report.evidence), 1)
        self.assertEqual(report.evidence[0]['text'], 'def a():\n    return 1\n')
        self.assertIsNotNone(report.source_version)
        ids = {item['evidence_id'] for item in report.evidence}
        self.assertTrue(all(not row['evidence_id'] or row['evidence_id'] in ids for row in report.selection['resolved']))
        reason = next(item for item in report.limits['exceeded'] if item['field'] == 'max_refs')
        self.assertEqual(reason['observed'], 2)

    def test_candidate_limit_names_the_actual_seed_count(self):
        report, _ = self.prepare({'a.py': 'def a():\n    return 1\ndef b():\n    return 2\n'},
                                 ['a.py:a', 'a.py:b'], overrides={'max_candidates': 1})
        self.assertEqual(report.status, 'completed')
        reason = next((item for item in report.limits['exceeded'] if item['field'] == 'max_candidates'), None)
        self.assertIsNotNone(reason)
        self.assertEqual(reason['observed'], 2)

    def test_module_and_frame_names_remain_exact_clues(self):
        trace = '  File "a.py", line 1, in wrong\n'
        report, _ = self.prepare({'a.py': 'VALUE = 1\n'}, trace=trace)
        self.assertEqual(report.status, 'partial')
        self.assertEqual(report.selection['reported_frames'][0]['status'], 'mismatch')

    def test_saved_unselected_file_drift_is_part_of_mandatory_final_verification(self):
        report, ws = self.prepare({'a.py': 'def a():\n    return 1\n', 'other.txt': 'saved\n'}, ['a.py:a'])
        from macr.investigation.static_evidence import validate_report_sources
        (Path(report.source_version['snapshot_ref']['root_ref']) / 'other.txt').write_bytes(b'altered\n')
        with self.assertRaises(ContractError):
            validate_report_sources(report, ws)

    def test_unicode_physical_separators_in_binding_expression_are_source_faithful(self):
        report, _ = self.prepare({'a.py': 'def take(x, y):\n    return x\ndef start():\n    return take("x\u2028y", y=True)\n'}, ['a.py:start'])
        self.assertEqual(report.status, 'completed')
        arguments = report.observations['bindings'][0]['arguments']
        self.assertEqual(arguments[0]['expression'], '"x\u2028y"')
        self.assertEqual(arguments[1]['expression'], 'True')
        self.assertEqual(arguments[1]['argument_location']['start'], 4)

    def test_parenthesized_decorator_start_is_the_saved_at_marker(self):
        for source in (b'@(\n    deco\n)\ndef work():\n    return 3\n',
                       b'@(\r    deco\r)\rdef work():\r    return 3\r'):
            with self.subTest(source=source):
                report, _ = self.prepare({'a.py': source}, ['a.py:work'])
                self.assertEqual(report.status, 'completed')
                excerpt = report.evidence[0]
                self.assertEqual(excerpt['text'], source.decode('utf-8'))
                self.assertEqual(excerpt['requested_span'], {'start': 1, 'end': 5})
                self.assertTrue(excerpt['symbol_id'].endswith(':work:4'))
                self.assertTrue(excerpt['complete_symbol'])

    def test_parenthesized_decorator_marker_line_selects_containing_function(self):
        report, _ = self.prepare({'a.py': b'@(\n    deco\n)\ndef work():\n    return 3\n'}, ['a.py:1'])
        self.assertEqual(report.evidence[0]['text'], '@(\n    deco\n)\ndef work():\n    return 3\n')
        self.assertTrue(report.evidence[0]['complete_symbol'])
        self.assertTrue((report.evidence[0]['symbol_id'] or '').endswith(':work:4'))

    def test_parenthesized_decorator_body_cap_cannot_certify_missing_marker(self):
        source = b'@(\n    deco\n)\ndef work():\n    return 3\n'
        report, _ = self.prepare({'a.py': source}, ['a.py:work'], overrides={'max_body_bytes': 36})
        self.assertEqual(report.status, 'partial')
        self.assertEqual(report.evidence[0]['text'], '@(\n    deco\n)\ndef work():\n    return')
        self.assertEqual(report.evidence[0]['requested_span'], {'start': 1, 'end': 5})
        self.assertFalse(report.evidence[0]['complete_symbol'])
        self.assertFalse(report.evidence[0]['complete_range'])
        self.assertTrue(any(item['required'] for item in report.gaps))

    def test_unproved_decorator_marker_leaves_required_gap(self):
        from macr.investigation import static_evidence
        with patch.object(static_evidence, '_decorator_spans', return_value={}, create=True):
            report, _ = self.prepare({'a.py': b'@(\n    deco\n)\ndef work():\n    return 3\n'}, ['a.py:work'])
        self.assertEqual(report.status, 'partial')
        self.assertEqual(report.evidence, [])
        self.assertTrue(any(item['code'] == 'declaration_unavailable' and item['required'] for item in report.gaps))


if __name__ == '__main__':
    unittest.main()
