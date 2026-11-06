"""Real durable/official adapters, with only authored HTTPS responses and dummy Key."""
from copy import deepcopy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from macr.api.evidence_workbench import Workbench
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.base import ProviderError
from macr.providers.repair_binding import development_config
from test_repair_entry import InterceptedOfficialOpener
import test_workbench_repair as fixtures


class WorkbenchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.h=fixtures.WorkbenchRepairTests('test_default_real_entry_retains_honest_blocked_results_and_reopens_without_target')
        self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.config=development_config();self.config['limits']['max_requests']=1
        self.h.config_path.write_text(json.dumps(self.config))

    def runtime(self, strategy='direct_api', cells=('one',)):
        from macr.api.repair_runtime import RepairRuntime
        plan={'schema':'macr.repair.spend-plan.v1','run_id':'workbench-authored',
            'ledger_root':str(self.h.base/'ledger'),'configuration_sha256':loop.digest(self.config),
            'output_contract_sha256':loop.digest(loop.output_contract('v6')),
            'max_requests':2,'max_usd':'1.000000','max_total_input_tokens':49152,
            'max_total_output_tokens':6144,'expires_at':int(time.time())+600,
            'cells':[{'cell_id':cell,'binding_sha256':entry.spend_binding_sha256(
                self.h.task,strategy,self.config,proposal_contract='v6')} for cell in cells]}
        path=self.h.base/'spend-plan.json';path.write_text(json.dumps(plan))
        reader=unittest.mock.Mock(return_value='DUMMY-NOT-A-REAL-KEY')
        runtime=RepairRuntime(path,cells[0],credential_reader=reader)
        self.addCleanup(runtime.close)
        return runtime,reader,plan,path

    def test_explicit_runtime_uses_official_adapter_durable_cell_and_pure_history(self):
        runtime,reader,plan,_=self.runtime();runtime.prepare()
        owner=self.h.owner(repair_runtime=runtime)
        opener=InterceptedOfficialOpener([self.h.candidate()])
        with patch('urllib.request.build_opener',return_value=opener):
            state=self.h.finish(owner,self.h.payload('direct_api'))
        self.assertEqual((state['state'],state['repair_status']),('completed','candidate_unverified'))
        result=owner.repair_result(state['id'])
        self.assertTrue(result['summary']['remote_enabled']);self.assertFalse(result['summary']['simulation'])
        self.assertEqual(result['summary']['global_spend']['requests'],1)
        self.assertEqual(result['report']['provider_accounting']['spend_commitment']['cell_id'],'one')
        self.assertFalse(result['report']['repaired']);self.assertEqual(len(opener.requests),1)
        self.assertEqual(reader.call_count,1)
        owner.shutdown();self.h.repo.rename(self.h.base/'moved');self.h.task_path.unlink();self.h.config_path.unlink()
        reopened=self.h.owner()
        with patch('urllib.request.build_opener',side_effect=AssertionError('history cannot call provider')):
            self.assertEqual(reopened.repair_result(state['id'])['summary'],result['summary'])
        self.assertNotIn('DUMMY-NOT-A-REAL-KEY',(owner.root/'repair-registry.json').read_text())

    def test_missing_verifier_never_reads_credential_or_claims_live_cell(self):
        runtime,reader,plan,_=self.runtime('api_debugger')
        owner=self.h.owner(repair_runtime=runtime)
        state=self.h.finish(owner,self.h.payload())
        self.assertEqual((state['state'],state['repair_status']),('blocked','verification_unavailable'))
        self.assertFalse(Path(plan['ledger_root']).exists());reader.assert_not_called()
        self.assertEqual(owner.repair_result(state['id'])['summary']['provider_requests'],0)

    def test_scope_and_plan_changes_precede_model_or_ledger_access(self):
        runtime,reader,plan,path=self.runtime()
        owner=self.h.owner(repair_runtime=runtime)
        with self.assertRaises(ContractError):owner.start_repair(self.h.payload('api_debugger'))
        plan['max_requests']=1;path.write_text(json.dumps(plan))
        with self.assertRaises(ContractError):owner.start_repair(self.h.payload('direct_api'))
        reader.assert_not_called();self.assertFalse(Path(plan['ledger_root']).exists())

    def test_explicit_registered_cell_cannot_be_replayed_in_another_output(self):
        runtime,reader,plan,_=self.runtime(cells=('one','two'));runtime.prepare()
        owner=self.h.owner(repair_runtime=runtime);payload=self.h.payload('direct_api')
        opener=InterceptedOfficialOpener([self.h.candidate()])
        with patch('urllib.request.build_opener',return_value=opener):
            first=self.h.finish(owner,payload)
            self.assertEqual(first['repair_status'],'candidate_unverified',first)
            duplicate=owner.start_repair(payload)
            self.assertEqual(first['id'],duplicate['id'])
            again=self.h.finish(owner,self.h.payload('direct_api'))
        self.assertEqual(len(opener.requests),1)
        self.assertNotEqual(again['state'],'completed')
        self.assertFalse(again['result_available'])

    def test_unknown_provider_keeps_reserve_and_quarantine_after_reopen(self):
        runtime,reader,plan,_=self.runtime();runtime.prepare();owner=self.h.owner(repair_runtime=runtime)
        opener=InterceptedOfficialOpener([])
        with patch('urllib.request.build_opener',return_value=opener):
            with patch.object(opener,'open',side_effect=TimeoutError()):
                state=self.h.finish(owner,self.h.payload('direct_api'))
        self.assertEqual(state['state'],'unknown')
        self.assertNotEqual(state['summary']['unknown_reserved_usd'],'0.000000')
        self.assertEqual(state['summary']['provider_requests'],1)
        owner.shutdown();reopened=self.h.owner()
        with self.assertRaises(ContractError):reopened.start_repair(self.h.payload('direct_api'))

    def test_credential_is_session_only_and_closing_cannot_reload_it(self):
        runtime,reader,_,_=self.runtime();reader.assert_not_called()
        runtime.prepare();runtime.prepare();self.assertEqual(reader.call_count,1)
        self.assertEqual(runtime.credential(),'DUMMY-NOT-A-REAL-KEY')
        runtime.close()
        with self.assertRaises(ProviderError):runtime.credential()
        self.assertNotIn('DUMMY-NOT-A-REAL-KEY',repr(runtime))

    def test_changed_global_cost_snapshot_is_withheld_without_reopening_ledger(self):
        runtime,reader,_,_=self.runtime();runtime.prepare();owner=self.h.owner(repair_runtime=runtime)
        with patch('urllib.request.build_opener',return_value=InterceptedOfficialOpener([self.h.candidate()])):
            state=self.h.finish(owner,self.h.payload('direct_api'))
        owner.shutdown();path=owner.root/'repair-registry.json';saved=json.loads(path.read_bytes())
        saved['runs'][state['id']]['summary']['global_spend']['known_usd']='9.000000'
        path.write_text(json.dumps(saved));reopened=self.h.owner()
        with patch('macr.providers.repair_spend.RepairSpendLedger.__enter__',side_effect=AssertionError('history cannot open writer')):
            with self.assertRaises(ContractError):reopened.repair_result(state['id'])
