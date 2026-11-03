"""Normal CLI proposal/report flow; no provider or target execution."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr import cli
from macr.evals import repair_comparison as comparison


class RepairComparisonEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        example = Path(__file__).resolve().parents[1] / 'examples/repair_comparison_development.json'
        self.spec = json.loads(example.read_text())
        self.spec_path = self.root / 'neutral-spec.json'
        self.spec_path.write_text(json.dumps(self.spec))

    def invoke(self, *args):
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['agent-review', 'repair-comparison', *map(str, args)]), redirect_stdout(stdout):
            try:
                cli.main()
            except SystemExit as error:
                code = error.code
            else:
                code = 0
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else {}

    def test_normal_entry_compiles_and_reports_without_dispatch_or_scoring_leak(self):
        out = self.root / 'compiled'
        code, summary = self.invoke('compile', '--spec', self.spec_path, '--out', out)
        self.assertEqual(code, 0)
        plan = json.loads((out / 'plan.json').read_text())
        self.assertEqual(plan, comparison.compile_comparison(self.spec))
        self.assertEqual(summary['planned_cells'], 2)
        self.assertEqual(summary['model_calls'], 0)
        capsule = json.loads((out / 'generation' / 'self-authored-development.json').read_text())
        self.assertEqual(capsule, comparison.generation_view(plan, 'self-authored-development'))
        for value in self.spec['scoring'].values():
            if isinstance(value, str) and len(value) == 64:
                self.assertNotIn(value, json.dumps(capsule))
        rows = self.root / 'rows.json'; rows.write_text('{}')
        report_out = self.root / 'reported'
        code, summary = self.invoke('report', '--plan', out / 'plan.json', '--results', rows, '--out', report_out)
        self.assertEqual(code, 0)
        report = json.loads((report_out / 'report.json').read_text())
        self.assertEqual(report, comparison.build_report(plan, {}))
        self.assertEqual(report['pairs']['not_comparable'], 1)
        self.assertFalse(summary['execution_authorized'])

    def test_invalid_or_duplicate_input_rejects_before_output_creation(self):
        for text in ('{"schema":1,"schema":2}', json.dumps(dict(self.spec, hidden_tests_path='DO-NOT-READ'))):
            self.spec_path.write_text(text)
            out = self.root / 'invalid'
            code, result = self.invoke('compile', '--spec', self.spec_path, '--out', out)
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'rejected')
            self.assertNotIn('DO-NOT-READ', json.dumps(result))
            self.assertFalse(out.exists())

    def test_three_policy_normal_entry_retains_three_missing_cells_without_ties(self):
        example = Path(__file__).resolve().parents[1] / 'examples/repair_comparison_three_arm_development.json'
        self.spec_path.write_text(example.read_text())
        out = self.root / 'three-plan'
        code, summary = self.invoke('compile', '--spec', self.spec_path, '--out', out)
        self.assertEqual(code, 0)
        self.assertEqual(summary['planned_cells'], 3)
        plan = json.loads((out / 'plan.json').read_text())
        self.assertEqual({cell['arm'] for cell in plan['cells']},
                         {'direct_api', 'api_test_feedback', 'api_debugger'})
        rows = self.root / 'empty.json'; rows.write_text('{}')
        report_out = self.root / 'three-report'
        self.assertEqual(self.invoke('report', '--plan', out / 'plan.json', '--results', rows,
                                     '--out', report_out)[0], 0)
        report = json.loads((report_out / 'report.json').read_text())
        self.assertEqual(report, comparison.build_report(plan, {}))
        self.assertEqual(report['planned_cells'], 3)
        for arm in report['arms'].values():
            self.assertEqual(arm['statuses']['not_attempted'], 1)
            self.assertEqual(arm['scored'], 0)

    def test_wrong_json_field_types_return_structured_rejection_without_output(self):
        for bad_type in ([], {}):
            bad = deepcopy(self.spec); bad['purpose'] = bad_type
            self.spec_path.write_text(json.dumps(bad))
            out = self.root / 'bad-type'
            code, result = self.invoke('compile', '--spec', self.spec_path, '--out', out)
            self.assertEqual(code, 2)
            self.assertEqual(result['status'], 'rejected')
            self.assertFalse(out.exists())
        plan = comparison.compile_comparison(self.spec)
        path = self.root / 'valid-plan.json'; path.write_text(json.dumps(plan))
        row = comparison._empty(self.spec['tasks'][0], self.spec)
        row['candidate_origin'] = []
        rows = self.root / 'wrong-origin.json'
        rows.write_text(json.dumps({plan['cells'][0]['cell_id']: row}))
        out = self.root / 'bad-origin'
        code, result = self.invoke('report', '--plan', path, '--results', rows, '--out', out)
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(out.exists())

    def test_existing_output_and_symlink_inputs_cannot_overwrite_or_escape(self):
        existing = self.root / 'existing'; existing.mkdir()
        marker = existing / 'keep.txt'; marker.write_text('unchanged')
        code, _ = self.invoke('compile', '--spec', self.spec_path, '--out', existing)
        self.assertEqual(code, 2)
        self.assertEqual(marker.read_text(), 'unchanged')
        linked = self.root / 'linked.json'; linked.symlink_to(self.spec_path)
        out = self.root / 'not-created'
        try:
            code, _ = self.invoke('compile', '--spec', linked, '--out', out)
        finally:
            linked.unlink()
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())

    def test_plan_and_result_tampering_remains_rejected_on_real_entry(self):
        plan = comparison.compile_comparison(self.spec)
        path = self.root / 'plan.json'
        rows = self.root / 'rows.json'; rows.write_text('{}')
        changed = deepcopy(plan); changed['remote_enabled'] = True
        path.write_text(json.dumps(changed))
        out = self.root / 'rejected-plan'
        self.assertEqual(self.invoke('report', '--plan', path, '--results', rows, '--out', out)[0], 2)
        self.assertFalse(out.exists())
        path.write_text(json.dumps(plan)); rows.write_text('{"not-a-planned-cell":{}}')
        self.assertEqual(self.invoke('report', '--plan', path, '--results', rows, '--out', out)[0], 2)
        self.assertFalse(out.exists())

    def test_handoff_reloads_actual_disabled_repair_record_without_inventing_result(self):
        from repair_fixtures import SOURCES
        from macr.investigation import repair_entry as entry, repair_loop as loop
        from macr.providers.repair_binding import RepairProviderBinding, development_config
        repo = write_files(self.root / 'source', SOURCES)
        files = {p: text.encode() for p, text in SOURCES.items()}
        model = development_config()
        record = self.root / 'repair-record'
        loop.run_repair_loop(repo, 'Development invoice issue', editable_paths=['builder.py'],
            test_paths=['checks.py'], provider=RepairProviderBinding(model), out=record,
            task_id='self-authored-development', strategy='direct_api')
        self.spec['tasks'] = [{'task_id': 'self-authored-development',
            'snapshot_sha256': loop.source_id(files), 'issue_sha256': loop.digest('Development invoice issue'),
            'visible_tests_sha256': loop.source_id({'checks.py': files['checks.py']}),
            'initial_context_sha256': entry.initial_context_id(files, 'Development invoice issue')}]
        self.spec['model']['configuration_sha256'] = loop.digest(model)
        self.spec['model']['output_contract_sha256'] = loop.digest(loop.output_contract())
        limits = model['limits']
        self.spec['limits'] = {'max_requests': limits['max_requests'],
            'input_tokens': limits['max_total_input_tokens'], 'output_tokens': limits['max_total_output_tokens'],
            'max_usd': limits['max_usd']}
        plan = comparison.compile_comparison(self.spec)
        path = self.root / 'plan.json'; path.write_text(json.dumps(plan))
        cell = next(c for c in plan['cells'] if c['arm'] == 'direct_api')
        out = self.root / 'handoff'
        code, result = self.invoke('handoff', '--plan', path, '--record', record,
                                  '--cell', cell['cell_id'], '--out', out)
        self.assertEqual(code, 0)
        packet = json.loads((out / 'packet.json').read_text())
        self.assertEqual(packet, entry.score_handoff(record, plan, cell['cell_id']))
        self.assertIsNone(packet['result']['review'])
        self.assertEqual(packet['result']['requests'], 0)
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(json.loads((out / 'results.json').read_text()),
                         {cell['cell_id']: packet['result']})
        before = loop.verify_loop(record)
        child = record / 'forbidden-handoff'
        code, _ = self.invoke('handoff', '--plan', path, '--record', record,
                              '--cell', cell['cell_id'], '--out', child)
        self.assertEqual(code, 2)
        self.assertFalse(child.exists())
        self.assertEqual(loop.verify_loop(record), before)
