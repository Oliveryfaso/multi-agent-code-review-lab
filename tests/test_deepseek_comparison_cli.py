"""CLI boundaries with literal development JSON and no real transport or Key."""
import contextlib
import getpass
import importlib
import io
import json
import unittest
import warnings
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_study_profile import StudyError

try:
    cli=importlib.import_module('run_deepseek_comparison_pilot')
except ModuleNotFoundError:
    cli=None


class TTY(io.StringIO):
    def isatty(self):
        return True


class ComparisonCliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cli,'the explicit comparison CLI is not implemented')
        self.root=fixture_root()

    def file(self,name,value):
        path=self.root/name;path.write_text(json.dumps(value))
        return path

    def invoke(self,args,*,stdin=None,stderr=None):
        out=io.StringIO();err=stderr or io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err),patch.object(
                cli.sys,'stdin',stdin or io.StringIO()):
            code=cli.main([str(value) for value in args])
        return code,out.getvalue(),err.getvalue()

    def test_execute_disabled_before_preflight_credentials_or_dispatch(self):
        approval=self.file('approval.json',{'authorized':True})
        with patch.object(cli,'preflight_approved_pilot') as preflight,patch.object(
                cli,'execute_approved_pilot') as execute,patch.object(cli,'_read_key_without_echo') as key:
            code,out,err=self.invoke(['execute','--control',self.root,'--approval',approval])
        self.assertEqual((code,out,err),(2,'','pilot_cli_live_disabled\n'))
        preflight.assert_not_called();execute.assert_not_called();key.assert_not_called()

    def test_unapproved_preflight_stops_before_credentials_and_execute(self):
        approval=self.file('approval.json',{'authorized':False})
        with (patch.object(cli,'preflight_approved_pilot',side_effect=StudyError('pilot_approval_missing')),
             patch.object(cli,'execute_approved_pilot') as execute,
             patch.object(cli,'_read_key_without_echo') as key):
            code,out,err=self.invoke(['execute','--control',self.root,'--approval',approval,'--enable-live'])
        self.assertEqual((code,out,err),(2,'','pilot_approval_missing\n'))
        execute.assert_not_called();key.assert_not_called()

    def test_noninteractive_execute_stops_before_credentials(self):
        approval=self.file('approval.json',{'authorized':True})
        with patch.object(cli,'preflight_approved_pilot',return_value={'phase':'R'}),patch.object(
                cli,'execute_approved_pilot') as execute,patch.object(cli,'_read_key_without_echo') as key:
            code,out,err=self.invoke(['execute','--control',self.root,'--approval',approval,'--enable-live'])
        self.assertEqual((code,out,err),(2,'','pilot_cli_interactive_tty_required\n'))
        execute.assert_not_called();key.assert_not_called()

    def test_preflight_outputs_no_execution_and_never_reads_key(self):
        approval=self.file('approval.json',{'authorized':True})
        with patch.object(cli,'preflight_approved_pilot',return_value={
                'phase':'R','plan_sha256':'a'*64}) as preflight,patch.object(
                cli,'execute_approved_pilot') as execute,patch.object(cli,'_read_key_without_echo') as key:
            code,out,err=self.invoke(['preflight','--control',self.root,'--approval',approval])
        self.assertEqual(code,0);self.assertEqual(err,'')
        self.assertEqual(json.loads(out),{'phase':'R','plan_sha256':'a'*64,
            'preflight':'passed','execution_enabled':False,'model_calls_live':0})
        preflight.assert_called_once_with(self.root,{'authorized':True})
        execute.assert_not_called();key.assert_not_called()

    def test_proposal_is_plain_json_without_key_or_execution(self):
        result={'phase':'R','authorized':False,'max_requests':21,'max_usd':'1.000000'}
        with patch.object(cli,'propose_pilot_approval',return_value=result),patch.object(
                cli,'execute_approved_pilot') as execute,patch.object(cli,'_read_key_without_echo') as key:
            code,out,err=self.invoke(['proposal','--control',self.root])
        self.assertEqual((code,json.loads(out),err),(0,result,''))
        execute.assert_not_called();key.assert_not_called()

    def test_literal_script_arrays_are_passed_to_zero_call_dryrun(self):
        scripts=self.file('scripts.json',{'cell-1':[{'literal':'fixture'},'timeout']})
        with patch.object(cli,'run_pilot_dryrun',return_value={
                'model_calls_live':0,'batch_halted':False}) as dryrun,patch.object(
                cli,'execute_approved_pilot') as execute,patch.object(cli,'_read_key_without_echo') as key:
            code,out,err=self.invoke(['dry-run','--control',self.root,'--scripts',scripts])
        self.assertEqual((code,json.loads(out),err),(0,{'model_calls_live':0,'batch_halted':False},''))
        dryrun.assert_called_once_with(self.root,scripted_by_cell={
            'cell-1':({'literal':'fixture'},'timeout')})
        execute.assert_not_called();key.assert_not_called()

    def test_invalid_script_shape_is_rejected_without_dispatch(self):
        scripts=self.file('scripts.json',{'cell-1':'timeout'})
        with patch.object(cli,'run_pilot_dryrun') as dryrun:
            code,out,err=self.invoke(['dry-run','--control',self.root,'--scripts',scripts])
        self.assertEqual((code,out,err),(2,'','pilot_cli_script_invalid\n'))
        dryrun.assert_not_called()

    def test_prepare_requires_phase_specific_metadata_before_backend(self):
        custodian=self.file('custodian.json',{'schema':'synthetic-hash-record'})
        with patch.object(cli,'prepare_pilot') as prepare:
            for phase,error in (('R','pilot_cli_selection_missing'),('U','pilot_cli_regression_control_missing')):
                code,out,err=self.invoke(['prepare','--phase',phase,'--pack',self.root,
                    '--custodian',custodian,'--out',self.root/'control'])
                self.assertEqual((code,out,err),(2,'',error+'\n'))
        prepare.assert_not_called()

    def test_prepare_reads_hash_only_json_and_forwards_exact_scope(self):
        custodian=self.file('custodian.json',{'schema':'synthetic-hash-record'})
        selection=self.file('selection.json',{'cells':[]})
        with (patch.object(cli,'prepare_pilot',return_value={'phase':'R','plan_sha256':'b'*64}) as prepare,
             patch.object(cli,'_read_key_without_echo') as key):
            code,out,err=self.invoke(['prepare','--phase','R','--pack',self.root,
                '--custodian',custodian,'--selection',selection,'--out',self.root/'control'])
        self.assertEqual((code,json.loads(out),err),(0,{'phase':'R','plan_sha256':'b'*64},''))
        prepare.assert_called_once_with(self.root,custodian={'schema':'synthetic-hash-record'},
            out=self.root/'control',phase='R',project_root=cli.PROJECT,
            regression_selection={'cells':[]},regression_control=None,seed=20261030)
        key.assert_not_called()

    def test_duplicate_json_fields_are_not_forwarded(self):
        custodian=self.root/'custodian.json';custodian.write_text('{"schema":1,"schema":2}')
        selection=self.file('selection.json',{'cells':[]})
        with patch.object(cli,'prepare_pilot') as prepare:
            code,out,err=self.invoke(['prepare','--phase','R','--pack',self.root,
                '--custodian',custodian,'--selection',selection,'--out',self.root/'control'])
        self.assertEqual((code,out,err),(2,'','study_json_invalid\n'))
        prepare.assert_not_called()

    def test_getpass_fallback_warning_never_consumes_input_or_exposes_details(self):
        approval=self.file('approval.json',{'authorized':True})
        stdin=TTY('DUMMY_NOT_A_CREDENTIAL\n');stderr=TTY()
        def warning_then_input(*args,**kwargs):
            warnings.warn('synthetic terminal detail',getpass.GetPassWarning)
            return input()
        def consume_callback(*args,**kwargs):
            kwargs['key_reader']()
            self.fail('no-echo failure must abort before any dispatch')
        with patch.object(cli,'preflight_approved_pilot',return_value={'phase':'R'}),patch.object(
                cli,'execute_approved_pilot',side_effect=consume_callback),patch.object(
                getpass,'getpass',side_effect=warning_then_input):
            code,out,err=self.invoke(['execute','--control',self.root,'--approval',approval,'--enable-live'],
                stdin=stdin,stderr=stderr)
        self.assertEqual(code,2);self.assertEqual(stdin.tell(),0)
        self.assertEqual((out,err),('','study_live_no_echo_input_unavailable\n'))

    def test_interrupt_has_fixed_status_and_does_not_retry(self):
        with patch.object(cli,'propose_pilot_approval',side_effect=KeyboardInterrupt) as proposal:
            code,out,err=self.invoke(['proposal','--control',self.root])
        self.assertEqual((code,out,err),(130,'','pilot_cli_interrupted_no_retry\n'))
        self.assertEqual(proposal.call_count,1)

    def test_real_proposal_and_json_dryrun_keep_credentials_and_network_unused(self):
        pilot=importlib.import_module('macr.evals.deepseek_comparison_pilot')
        from test_deepseek_comparison_pilot import prepare_regression,scripted
        _,control,plan=prepare_regression(pilot)
        scripts=self.file('scripts.json',scripted(plan))
        with (patch.object(cli,'_read_key_without_echo',side_effect=AssertionError('unexpected credential input')) as key,
             patch('urllib.request.build_opener',side_effect=AssertionError('unexpected network')) as network):
            code,out,err=self.invoke(['proposal','--control',control])
            self.assertEqual((code,err),(0,''))
            proposal=json.loads(out)
            self.assertFalse(proposal['authorized'])
            self.assertEqual((proposal['max_requests'],proposal['max_usd']),(21,'1.000000'))
            self.assertEqual(proposal['plan_sha256'],plan['plan_sha256'])
            self.assertFalse((control/'execution.claim.json').exists())
            approval=self.file('unapproved.json',proposal)
            code,out,err=self.invoke(['preflight','--control',control,'--approval',approval])
            self.assertEqual((code,out,err),(2,'','pilot_approval_missing\n'))
            self.assertFalse((control/'execution.claim.json').exists())
            code,out,err=self.invoke(['dry-run','--control',control,'--scripts',scripts])
        self.assertEqual((code,err),(0,''))
        summary=json.loads(out)
        self.assertEqual(summary['model_calls_live'],0)
        self.assertFalse(summary['batch_halted'])
        self.assertEqual(len(summary['rows']),7)
        self.assertTrue(summary['promotion_gate']['eligible'])
        key.assert_not_called();network.assert_not_called()

    def test_freeze_unseen_emits_only_prefreeze_scope_and_never_reads_key(self):
        custodian=self.file('custodian.json',{'schema':'synthetic-hash-record'})
        scope={'schema':'macr.deepseek.pilot.unseen-scope.v1','scope_sha256':'c'*64,
            'cells':[],'data':{},'config':{},'execution_prerequisite':'eligible-real-R'}
        with (patch('macr.evals.deepseek_comparison_pilot.freeze_unseen_inputs',
                create=True,return_value=scope) as freeze,
              patch.object(cli,'execute_approved_pilot') as execute,
              patch.object(cli,'_read_key_without_echo') as key):
            try:
                code,out,err=self.invoke(['freeze-unseen','--pack',self.root,
                    '--custodian',custodian,'--out',self.root/'unseen'])
            except SystemExit:
                self.fail('freeze-unseen must be an available offline command')
        self.assertEqual((code,json.loads(out),err),(0,scope,''))
        self.assertNotIn('plan_sha256',json.loads(out))
        freeze.assert_called_once_with(self.root,custodian={'schema':'synthetic-hash-record'},
            out=self.root/'unseen',project_root=cli.PROJECT,seed=20261030)
        execute.assert_not_called();key.assert_not_called()

    def test_finalize_unseen_propagates_gate_failure_without_execution_or_key(self):
        with (patch('macr.evals.deepseek_comparison_pilot.finalize_unseen_pilot',create=True,
                side_effect=StudyError('pilot_regression_not_qualified')) as finalize,
              patch.object(cli,'execute_approved_pilot') as execute,
              patch.object(cli,'_read_key_without_echo') as key):
            try:
                code,out,err=self.invoke(['finalize-unseen','--control',self.root/'unseen',
                    '--regression-control',self.root/'regression'])
            except SystemExit:
                self.fail('finalize-unseen must be an available offline command')
        self.assertEqual((code,out,err),(2,'','pilot_regression_not_qualified\n'))
        finalize.assert_called_once_with(self.root/'unseen',regression_control=self.root/'regression')
        execute.assert_not_called();key.assert_not_called()

    def test_freeze_unseen_actual_scope_does_not_create_an_execution_plan(self):
        pilot=importlib.import_module('macr.evals.deepseek_comparison_pilot')
        self.assertTrue(callable(getattr(pilot,'freeze_unseen_inputs',None)),
            'the pre-freeze scope producer is not implemented')
        from test_deepseek_study_profile import COMMITMENT,development_pack
        pack=fixture_root();development_pack(pack,9)
        custodian=self.file('custodian.json',COMMITMENT)
        control=pack.parent/(pack.name+'-unseen-scope')
        with (patch.object(cli,'_read_key_without_echo',side_effect=AssertionError('unexpected credential input')) as key,
              patch('urllib.request.build_opener',side_effect=AssertionError('unexpected network')) as network):
            code,out,err=self.invoke(['freeze-unseen','--pack',pack,
                '--custodian',custodian,'--out',control])
        self.assertEqual((code,err),(0,''))
        scope=json.loads(out)
        self.assertEqual(scope['schema'],'macr.deepseek.pilot.unseen-scope.v1')
        self.assertEqual(len(scope['scope_sha256']),64)
        self.assertNotIn('plan_sha256',scope)
        self.assertIn('execution_prerequisite',scope)
        self.assertFalse((control/'pilot.json').exists())
        self.assertFalse((control/'execution.claim.json').exists())
        key.assert_not_called();network.assert_not_called()


if __name__=='__main__':
    unittest.main()
