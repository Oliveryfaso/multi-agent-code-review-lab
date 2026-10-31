"""Actual frozen static preparation and literal provider envelopes, never live."""
import copy
import importlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.evals.deepseek_study_profile import digest

try:
    stage = importlib.import_module('macr.evals.static_diagnostic_stage')
except ModuleNotFoundError:
    stage = None

PROJECT = Path(__file__).resolve().parents[1]
CUSTODIAN = {'schema': 'macr.deepseek.study.custodian.v1', 'version': 'offline-fixture-only',
    'gold_sha256': 'a' * 64, 'rubric_sha256': 'b' * 64,
    'qualification_statement': 'Synthetic plumbing fixture; not independently authored quality evidence.'}


def quality_root():
    tasks, files = [], {}
    for n in range(9):
        task = {'id': f'n{n}', 'project_id': f'project-{n // 3}',
            'snapshot_root': f'snapshots/n{n}', 'question': 'What does this captured source establish?',
            'scenario': 'Consider ordinary values and unchanged Python bindings.', 'assumptions': [],
            'authorized_files': ['entry.py', 'helper.py'], 'initial_evidence_files': ['entry.py', 'helper.py'],
            'entrypoint': {'file': 'entry.py', 'symbol': 'start'}, 'entries': ['entry.py:start'],
            'license': {'spdx': 'MIT', 'provenance': 'Self-authored owner test only; not a blind task.'}}
        tasks.append(task)
        files[f'snapshots/n{n}/entry.py'] = 'from helper import allow\ndef start():\n    return allow(staff=True, paid=False)\n'
        files[f'snapshots/n{n}/helper.py'] = 'def allow(*, staff, paid):\n    return staff or paid\n'
    files['pack.json'] = json.dumps({'schema': 'macr.static-diagnostic.pack.v1',
        'pack_id': 'owner-plumbing-fixture-v1', 'tasks': tasks})
    return write_files(fixture_root(), files)


def envelope(content, *, fingerprint='fp_stage_fixture', prompt=100, completion=40):
    return {'model': 'deepseek-flash', 'system_fingerprint': fingerprint,
        'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content,
            'reasoning_content': 'Literal fixture, no model inference.'}}],
        'usage': {'prompt_tokens': prompt, 'completion_tokens': completion,
            'total_tokens': prompt + completion, 'prompt_cache_hit_tokens': 0,
            'prompt_cache_miss_tokens': prompt,
            'completion_tokens_details': {'reasoning_tokens': 20}}}


class StaticDiagnosticStageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(stage, 'static diagnostic stage owner is not implemented')
        for name in ('subprocess.Popen', 'socket.create_connection', 'urllib.request.build_opener'):
            guard = patch(name, side_effect=AssertionError('offline owner cannot contact services or execute code'))
            guard.start(); self.addCleanup(guard.stop)

    def prepare(self):
        quality = quality_root()
        out = fixture_root() / 'control'
        plan = stage.prepare_stage(PROJECT / 'examples/static-diagnostic-development', quality,
            custodian=CUSTODIAN, out=out, project_root=PROJECT, stage_id='owner-fixture-v1')
        return out, plan, quality

    def scripts(self, root, plan):
        result = {}
        for phase in plan['phases'].values():
            for cell in phase['cells']:
                prepared = json.loads((root / cell['prepared_path']).read_text())
                scope = prepared['scope']
                answer = {'verdict': 'unknown', 'cause': None,
                    'when': None, 'refs': [],
                    'unknowns': ['Independent semantic adjudication.'], 'scope': scope['scope_sha256'][:16]}
                result[cell['cell_id']] = envelope(json.dumps(answer))
        return result

    def approved(self, root):
        return stage.propose_approval(root) | {'authorized': True, 'confirmation_ref': 'offline-test-consent'}

    def test_freeze_actual_v3_pairs_and_complete_stage_scope(self):
        root, plan, _ = self.prepare()
        self.assertEqual(plan['schema'], 'macr.static-diagnostic.stage.v1')
        self.assertEqual(plan['profile'], 'static-diagnostic-v1')
        self.assertEqual([len(p['cells']) for p in plan['phases'].values()], [6, 18])
        self.assertEqual(plan['budget'], {'max_requests': 24, 'max_usd': '1.500000'})
        self.assertEqual(stage.load_stage(root), plan)
        for phase in plan['phases'].values():
            for task in phase['tasks']:
                prepared = json.loads((root / task['prepared_path']).read_text())
                self.assertFalse(prepared['packet']['selection_gaps'])
                self.assertEqual(prepared['operations']['index_build_count'], 1)
                self.assertEqual(prepared['requests']['direct']['messages'][-1],
                    prepared['requests']['evidence']['messages'][-1])
            for cell in phase['cells']:
                self.assertLessEqual(cell['wire_bytes'], 65536)
                self.assertFalse(Path(cell['prepared_path']).is_absolute())

    def test_default_off_exact_approval_and_claim_before_key(self):
        root, plan, _ = self.prepare(); called = []
        reader = lambda: called.append(True)
        with self.assertRaises(ValueError): stage.execute_stage(root, self.approved(root), key_reader=reader)
        with self.assertRaises(ValueError): stage.preflight_stage(root, stage.propose_approval(root))
        wrong = self.approved(root); wrong['budget']['max_requests'] = 25
        with self.assertRaises(ValueError): stage.execute_stage(root, wrong, enabled=True, key_reader=reader)
        self.assertEqual(called, [])
        scripts = self.scripts(root, plan)
        result = stage.execute_stage(root, self.approved(root), enabled=True,
            key_reader=reader, scripted_by_cell=scripts)
        self.assertEqual((result['actual_requests'], result['model_calls_live']), (24, 0))
        self.assertEqual(called, [])
        self.assertTrue((root / 'execution.claim.json').exists())
        with self.assertRaises(ValueError): stage.execute_stage(root, self.approved(root),
            enabled=True, key_reader=reader, scripted_by_cell=scripts)

    def test_dryrun_complete_delivery_is_pending_semantics_and_no_live_permission(self):
        root, plan, _ = self.prepare()
        summary = stage.run_dryrun(root, scripted_by_cell=self.scripts(root, plan))
        self.assertEqual(summary['provenance'], 'scripted_offline_fixture')
        self.assertTrue(summary['s0_qualified']); self.assertFalse(summary['stage_halted'])
        self.assertEqual(summary['planned_cells'], 24)
        self.assertEqual({r['status'] for r in summary['rows']}, {'semantic_pending'})
        for row in summary['rows']:
            value = json.loads((root / 'cells' / (row['cell_id'] + '.json')).read_text())
            self.assertEqual(row['result_sha256'], digest(value))
            self.assertEqual(value['request']['max_output_tokens'], 8192)
            self.assertEqual(value['request']['thinking'], {'type': 'enabled'})
            self.assertEqual(value['review_status'], 'pending')
            self.assertEqual(value['model_calls_live'], 0)
        with self.assertRaises(ValueError): stage.run_dryrun(root, scripted_by_cell=self.scripts(root, plan))

    def test_first_s0_content_failure_charged_and_s1_not_attempted(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        scripts[plan['phases']['S0']['cells'][0]['cell_id']] = envelope('invalid final JSON')
        result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 1); self.assertFalse(result['s0_qualified'])
        self.assertEqual(result['rows'][0]['status'], 'contract_failed')
        self.assertEqual([r['status'] for r in result['rows'][1:]], ['not_attempted'] * 23)
        self.assertNotEqual(result['known_accrued_usd'], '0.000000')
        self.assertEqual(result['new_unknown_held_usd'], '0.000000')

    def test_second_s1_known_content_failure_stops_without_retry(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        for cell in plan['phases']['S1']['cells'][:2]: scripts[cell['cell_id']] = envelope('bad JSON')
        result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 8)
        self.assertEqual([r['status'] for r in result['rows'][6:8]], ['contract_failed'] * 2)
        self.assertEqual([r['status'] for r in result['rows'][8:]], ['not_attempted'] * 16)
        self.assertTrue(result['stage_halted'])

    def test_unknown_holds_reservation_and_identity_drift_stops_even_when_paid(self):
        for kind in ('timeout', 'fingerprint', 'input'):
            root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
            cell = plan['phases']['S0']['cells'][1]
            if kind == 'timeout': scripts[cell['cell_id']] = 'timeout'
            elif kind == 'fingerprint': scripts[cell['cell_id']]['system_fingerprint'] = 'different-fp'
            else:
                scripts[cell['cell_id']]['usage'].update(prompt_tokens=8001, prompt_cache_miss_tokens=8001,
                    total_tokens=8041)
            result = stage.run_dryrun(root, scripted_by_cell=scripts)
            self.assertEqual(result['actual_requests'], 2)
            self.assertTrue(result['stage_halted']); self.assertFalse(result['s0_qualified'])
            self.assertEqual(result['new_unknown_held_usd'], '0.330000' if kind == 'timeout' else '0.000000')

    def test_malformed_assistant_preserves_verified_identity_usage_and_cost(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        first = plan['phases']['S0']['cells'][0]['cell_id']
        scripts[first]['choices'][0]['message']['tool_calls'] = [{'id': 'bad', 'type': 'function',
            'function': {'name': 'undeclared', 'arguments': '{}'}}]
        result = stage.run_dryrun(root, scripted_by_cell=scripts)
        row = json.loads((root/'cells'/f'{first}.json').read_text())
        self.assertEqual(row['status'], 'contract_failed')
        self.assertIsNotNone(row['response'].get('billing_evidence'))
        self.assertIsNotNone(row['response'].get('identity_evidence'))
        self.assertEqual(row['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(result['actual_requests'], 1)

    def test_original_and_frozen_source_and_copied_control_drift_refused(self):
        for kind in ('source', 'copy', 'prepared'):
            root, plan, quality = self.prepare()
            approval = self.approved(root)
            if kind == 'source': (quality/'snapshots/n0/helper.py').write_text('def allow(**kw): return True\n')
            elif kind == 'prepared':
                prepared = root / plan['phases']['S0']['tasks'][0]['prepared_path']
                prepared.write_text(prepared.read_text() + ' ')
            else:
                copied = fixture_root() / 'copy'; shutil.copytree(root, copied); root = copied
            with self.subTest(kind=kind), self.assertRaises(ValueError): stage.preflight_stage(root, approval)

    def test_missing_entry_is_not_frozen_as_qualified(self):
        quality = quality_root(); pack = json.loads((quality/'pack.json').read_text())
        pack['tasks'][0].update(entries=['entry.py:missing'], entrypoint={'file': 'entry.py', 'symbol': 'missing'})
        (quality/'pack.json').write_text(json.dumps(pack))
        with self.assertRaises(ValueError): stage.prepare_stage(PROJECT/'examples/static-diagnostic-development', quality,
            custodian=CUSTODIAN, out=fixture_root()/'control', project_root=PROJECT, stage_id='missing-entry')

    def test_interrupt_after_real_reservation_preserves_unknown_and_denominator(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        with patch.object(stage._ScriptedWire, 'post', side_effect=KeyboardInterrupt):
            result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 1)
        self.assertEqual(result['new_unknown_held_usd'], '0.330000')
        self.assertEqual(result['rows'][0]['status'], 'unknown')
        self.assertEqual(result['rows'][0]['error_code'], 'static_stage_interrupted')
        self.assertEqual([r['status'] for r in result['rows'][1:]], ['not_attempted'] * 23)
        self.assertTrue((root/'phases/S0/ledger/001.reservation.json').exists())
        self.assertFalse((root/'phases/S0/ledger/001.outcome.json').exists())
        self.assertTrue((root/'execution.claim.json').exists())

    def test_source_drift_between_cells_stops_before_second_reservation(self):
        root, plan, quality = self.prepare(); scripts = self.scripts(root, plan)
        original = stage._ScriptedWire.post
        def post(wire, *args):
            reply = original(wire, *args)
            (quality/'snapshots/n0/helper.py').write_text('def allow(**kw): return True\n')
            return reply
        with patch.object(stage._ScriptedWire, 'post', post):
            result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 1)
        self.assertEqual(result['rows'][0]['status'], 'semantic_pending')
        self.assertEqual(result['rows'][1]['status'], 'failed')
        self.assertEqual(result['rows'][1]['error_code'], 'static_stage_source_drift')
        self.assertEqual([r['status'] for r in result['rows'][2:]], ['not_attempted'] * 22)

    def test_s1_pins_s0_identity_and_truncated_known_reply_is_contract_failure(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        cell = plan['phases']['S1']['cells'][0]
        scripts[cell['cell_id']]['choices'][0]['finish_reason'] = 'length'
        result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 24)
        self.assertFalse(result['stage_halted'])
        self.assertEqual(result['rows'][6]['status'], 'contract_failed')
        self.assertEqual(result['rows'][6]['error_code'], 'model_output_incomplete')
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        cell = plan['phases']['S1']['cells'][0]
        scripts[cell['cell_id']]['system_fingerprint'] = 'different-stage-segment'
        result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 7)
        self.assertEqual(result['rows'][6]['error_code'], 'system_fingerprint_drift')
        self.assertTrue(result['stage_halted'])

    def test_local_original_artifact_failure_does_not_hide_claim_or_accounting(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        original = stage._write_exclusive
        first = plan['phases']['S0']['cells'][0]['cell_id']
        def write(path, value, limit):
            if path == root/'cells'/f'{first}.json':
                raise OSError('synthetic original artifact write failure')
            return original(path, value, limit)
        with patch.object(stage, '_write_exclusive', write), self.assertRaises(ValueError):
            stage.run_dryrun(root, scripted_by_cell=scripts)
        result = json.loads((root/'summary.json').read_text())
        self.assertEqual(result['actual_requests'], 1)
        self.assertEqual(len(result['rows']), 24)
        self.assertTrue(result['stage_halted'])
        self.assertTrue((root/'phases/S0/ledger/001.outcome.json').exists())
        self.assertTrue((root/'execution.claim.json').exists())
        self.assertFalse((root/'anonymous-packets.json').exists())

    def test_record_limit_preserves_full_summary_and_settled_ledger(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        original = stage._write_exclusive
        first = plan['phases']['S0']['cells'][0]['cell_id']
        def write(path, value, limit):
            if path == root/'cells'/f'{first}.json':
                raise stage.PolicyRunError('run_record_limit')
            return original(path, value, limit)
        with patch.object(stage, '_write_exclusive', write), self.assertRaises(stage.StudyError) as failure:
            stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(failure.exception.code, 'static_stage_result_write_failed')
        result = json.loads((root/'summary.json').read_text())
        self.assertEqual(result['actual_requests'], 1)
        self.assertEqual(len(result['rows']), 24)
        self.assertEqual([r['status'] for r in result['rows'][1:]], ['not_attempted'] * 23)
        self.assertTrue(result['stage_halted'])
        self.assertTrue((root/'phases/S0/ledger/001.outcome.json').exists())
        self.assertTrue((root/'execution.claim.json').exists())
        self.assertFalse((root/'anonymous-packets.json').exists())

    def test_credential_helper_code_is_frozen_and_drift_blocks_before_claim(self):
        root, plan, _ = self.prepare()
        approval = self.approved(root)
        helpers = ('scripts/run_deepseek_value_stage.py', 'scripts/run_deepseek_tool_name_probe.py')
        for helper in helpers:
            with self.subTest(helper=helper):
                self.assertIn(helper, plan['implementation'])
                original = stage._read
                def changed(base, relative, limit, *, helper=helper):
                    raw = original(base, relative, limit)
                    return raw + b'\n# synthetic credential-helper drift\n' if relative == helper else raw
                with patch.object(stage, '_read', changed), self.assertRaises(stage.StudyError) as failure:
                    stage.preflight_stage(root, approval)
                self.assertEqual(failure.exception.code, 'static_stage_code_drift')
                self.assertFalse((root/'execution.claim.json').exists())

    def test_admitted_but_never_posted_fixture_is_not_a_transport_call(self):
        root, plan, _ = self.prepare(); scripts = self.scripts(root, plan)
        with patch.object(stage.DeepSeekProvider, 'converse',
                side_effect=stage.ProviderError('model_request_invalid')):
            result = stage.run_dryrun(root, scripted_by_cell=scripts)
        self.assertEqual(result['actual_requests'], 1)  # A durable reservation, not a claimed POST.
        self.assertEqual(result['fixture_model_calls'], 0)
        self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(result['new_unknown_held_usd'], '0.330000')
        self.assertEqual(result['rows'][0]['status'], 'unknown')
        self.assertEqual([r['status'] for r in result['rows'][1:]], ['not_attempted'] * 23)

    def test_source_encoding_verification_preserves_raw_hash_and_decoded_source(self):
        for raw in (b'\xef\xbb\xbfdef allow(**kwargs):\n    return True\n',
                    b'# coding: latin-1\ndef allow(**kwargs):\n    """caf\xe9"""\n    return True\n'):
            quality = quality_root(); (quality/'snapshots/n0/helper.py').write_bytes(raw)
            root = fixture_root()/'control'
            plan = stage.prepare_stage(PROJECT/'examples/static-diagnostic-development', quality,
                custodian=CUSTODIAN, out=root, project_root=PROJECT, stage_id='encoding-fixture')
            self.assertEqual(stage.load_stage(root), plan)
            self.assertEqual(plan['sourcebindings']['S1']['files']['snapshots/n0/helper.py']['size_bytes'], len(raw))
