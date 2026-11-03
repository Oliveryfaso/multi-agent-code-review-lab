"""Trusted control-flow tests; fixture Python is never imported or executed."""
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES, OLD, NEW
from macr.investigation import repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.base import ProviderError


class RepairLoopTests(TestCase):
    def three_profile_loop(self, replies, observations, *, strategy='api_test_feedback', max_rounds=3):
        self.out = fixture_root()/'three-profile-run'
        self.provider = loop.ScriptedPatchProvider(replies)
        self.verifier = None if observations is None else loop.ReplayVerifier(observations)
        return loop._run_loop(self.repo,'Discount applies only to items.',
            editable_paths=['app.py','builder.py','records.py'],test_paths=['checks.py'],
            provider=self.provider,verifier=self.verifier,out=self.out,max_rounds=max_rounds,
            max_total_input_tokens=24576,max_total_output_tokens=3072,
            strategy=strategy,proposal_contract='v3')

    def test_v3_feedback_baseline_keeps_last_candidate_and_model_finalize(self):
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        replies=[self.proposal(),self.proposal(old=NEW,new=OLD),
                 {'status':'finalize_current','summary':'Retain latest candidate.','edits':[]}]
        observations=[self.observation(self.files,self.tests),self.observation(fixed),
                      self.observation(self.files,self.tests)]
        result=self.three_profile_loop(replies,observations)
        self.assertEqual((result['status'],result['proposal_requests']),('candidate_finalized',3))
        self.assertEqual(self.verifier.calls,3)
        self.assertEqual(result['final_source_id'],loop.source_id(self.files))
        self.assertNotEqual(result['final_source_id'],result['rounds'][0]['candidate_source_id'])
        self.assertEqual(result,loop.verify_loop(self.out))
        requests=deepcopy(self.provider.requests)
        debugger=self.three_profile_loop(replies,observations,strategy='api_debugger')
        self.assertEqual((debugger['status'],debugger['proposal_requests']),('simulation_passed',1))
        self.assertEqual(self.verifier.calls,2)
        self.assertEqual(json.loads(requests[0]['messages'][1]['content'])['feedback'],
                         json.loads(self.provider.requests[0]['messages'][1]['content'])['feedback'])

    def test_v3_feedback_cap_keeps_candidate_but_never_claims_pass(self):
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        result=self.three_profile_loop([self.proposal()],[self.observation(self.files,self.tests),
            self.observation(fixed)],max_rounds=1)
        self.assertEqual(result['status'],'candidate_unverified')
        self.assertEqual(result['proposal_requests'],1)
        self.assertFalse(result['repaired']);self.assertFalse(result['target_code_executed'])
        self.assertEqual(result,loop.verify_loop(self.out))

    def test_v3_feedback_requires_verifier_and_preserves_explicit_withdrawal(self):
        result=self.three_profile_loop([self.proposal()],None)
        self.assertEqual((result['status'],result['proposal_requests']),('verification_unavailable',0))
        for terminal in ('no_candidate','insufficient_evidence'):
            result=self.three_profile_loop([{'status':terminal,'summary':'Withdraw.','edits':[]}],
                [self.observation(self.files,self.tests)])
            self.assertEqual(result['status'],terminal)
        with self.assertRaises(ContractError):
            self.versioned_loop([self.proposal()],strategy='api_test_feedback',proposal_contract='v2')

    def test_v3_feedback_invalid_termination_and_verification_stop_do_not_continue(self):
        result=self.three_profile_loop([{'status':'finalize_current','summary':'No prior candidate.','edits':[]}],
            [self.observation(self.files,self.tests)])
        self.assertEqual((result['status'],result['proposal_requests']),('proposal_invalid',1))
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        result=self.three_profile_loop([self.proposal(),self.proposal(old=NEW,new=OLD)],
            [self.observation(self.files,self.tests),self.observation(fixed,status='timeout')])
        self.assertEqual((result['status'],result['proposal_requests']),('verification_timeout',1))
        self.assertEqual(len(self.provider.replies),1)
        self.assertEqual(result,loop.verify_loop(self.out))
        claim=loop._read(self.out,'claim.json');claim['config']['proposal_contract']='v2'
        loop._write(self.out,'claim.json',claim)
        with self.assertRaises(ContractError) as raised:loop.verify_loop(self.out)
        self.assertEqual(raised.exception.field,'feedback_strategy_contract')

    def test_v3_public_projection_excludes_raw_check_paths_and_extra_details(self):
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        self.three_profile_loop([self.proposal(),{'status':'finalize_current','summary':'Keep.','edits':[]}],
            [self.observation(self.files,self.tests),self.observation(fixed)])
        feedback=json.loads(self.provider.requests[1]['messages'][1]['content'])['feedback']
        self.assertEqual(set(feedback),{'source_id','origin','execution_status','observed_status',
                                        'outcomes','test_counts'})
        self.assertEqual(feedback['source_id'],loop.source_id(fixed))
        self.assertEqual(feedback['test_counts'],{'passed':len(self.tests),'failed':0,
            'executed':len(self.tests),'collected':len(self.tests)})
        check=loop._read(self.out,'checks/1/receipt.json')
        check['result'].update(stdout_ref='/private/HOST-SECRET',details='HIDDEN-EXTRA')
        check['observation']['hidden']='HIDDEN-EXTRA'
        request=loop._request(fixed,'Public issue',['builder.py'],['checks.py'],check,2,
            strategy='api_test_feedback',bound=True,proposal_contract='v3')
        encoded=json.dumps(request.messages)
        self.assertNotIn('HOST-SECRET',encoded);self.assertNotIn('HIDDEN-EXTRA',encoded)
        self.assertNotIn(str(self.out),encoded)
        self.assertEqual(json.loads(request.messages[1]['content'])['feedback'],feedback)
        check['source_id']='0'*64
        with self.assertRaises(ContractError):
            loop._request(fixed,'Issue',['builder.py'],['checks.py'],check,2,
                strategy='api_test_feedback',proposal_contract='v3')

    def test_v2_request_bytes_remain_unchanged_with_v3_available(self):
        from dataclasses import asdict
        expected={'direct_api:False':'580f5882a37c02b4cefffb8a54bd6754d18efaf43eaf0c828aa99cb6d829d27a',
            'direct_api:True':'56f99e066ee722eb6e1ee39a5c686d8e72b959c42bb92b928aee575cdb5ceb54',
            'api_debugger:False':'48ec648c563c2a2d6a684382ff17778f8671bc473b39c74cfa8f2dff78f27f7e',
            'api_debugger:True':'5a1a281ef4e93c05a6c18f37fe2ed4a5c70c483ad19fc61d88ebcc74302142e3'}
        self.assertEqual(loop.digest(loop.output_contract('v2')),
                         '4cbfe4afe745df551a61b1a45dd009528e7a7497ba68be344dac8651ee2cbbcb')
        for strategy in ('direct_api','api_debugger'):
            for bound in (False,True):
                request=loop._request(self.files,'Legacy byte-compatibility fixture.',
                    ['builder.py'],['checks.py'],{},1,strategy=strategy,bound=bound,proposal_contract='v2')
                self.assertEqual(loop.digest(asdict(request)),expected[strategy+':'+str(bound)])

    def test_legacy_record_cannot_claim_configured_http_provenance(self):
        self.run_loop([], [self.observation(self.files)])
        original=loop._read(self.out,'report.json')
        changed=deepcopy(original);changed['simulation']=False
        changed['candidate_origin']='official_https_output_unattested'
        loop._write(self.out,'report.json',changed)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)
        claim=loop._read(self.out,'claim.json')
        claim['config']['provider_runtime']={'spend_commitment':{'plan_sha256':'0'*64,'cell_id':'fake'},
                                            'remote_enabled':True}
        loop._write(self.out,'claim.json',claim)
        changed['config_sha256']=loop.digest(claim['config'])
        changed['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        loop._write(self.out,'report.json',changed)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)

    def versioned_loop(self, replies, *, strategy='api_debugger', proposal_contract='v2'):
        fixed = dict(self.files)
        fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(), NEW.encode())
        self.out = fixture_root()/'versioned-run'
        self.provider = loop.ScriptedPatchProvider(replies)
        self.verifier = loop.ReplayVerifier([self.observation(self.files,self.tests),
                                            self.observation(fixed,self.tests)])
        try:
            return loop._run_loop(self.repo,'Discount applies only to items.',
                editable_paths=['app.py','builder.py','records.py'],test_paths=['checks.py'],
                provider=self.provider,verifier=self.verifier,out=self.out,max_rounds=3,
                max_total_input_tokens=24576,max_total_output_tokens=3072,
                strategy=strategy,proposal_contract=proposal_contract)
        except TypeError as error:
            self.fail('The explicit versioned repair interface failed: '+str(error))

    def test_v2_finalize_current_preserves_candidate_without_extra_validation(self):
        finalize = {'status':'finalize_current','summary':'Retain the current unverified candidate.','edits':[]}
        for strategy in ('direct_api','api_debugger'):
            with self.subTest(strategy=strategy):
                result = self.versioned_loop([self.proposal(),finalize,self.proposal()],strategy=strategy)
                self.assertEqual(result['status'],'candidate_finalized')
                self.assertEqual(result['proposal_requests'],2)
                self.assertEqual(len(self.provider.replies),1)
                self.assertEqual(len(result['checks']),2)
                self.assertEqual(self.verifier.calls,0 if strategy=='direct_api' else 2)
                self.assertEqual(result['final_source_id'],result['rounds'][0]['candidate_source_id'])
                self.assertFalse((self.out/'sources/2').exists())
                self.assertEqual(result,loop.verify_loop(self.out))
                claim = loop._read(self.out,'claim.json')['config']
                self.assertEqual(claim['proposal_contract'],'v2')
                first = json.loads(self.provider.requests[0]['messages'][1]['content'])
                final = json.loads(self.provider.requests[1]['messages'][1]['content'])
                self.assertEqual(final['base_content_id'],result['final_source_id'])
                self.assertNotEqual(first['base_content_id'],final['base_content_id'])
                self.assertEqual(final['files']['checks.py'],SOURCES['checks.py'])
                self.assertFalse(result['repaired']);self.assertFalse(result['target_code_executed'])

    def test_finalize_rejects_first_round_legacy_and_invalid_bindings(self):
        finalize = {'status':'finalize_current','summary':'Keep candidate.','edits':[]}
        for version, replies in (
            ('v2',[finalize]), ('v1',[self.proposal(),finalize]),
            ('v2',[self.proposal(),dict(finalize,base_content_id='0'*64)]),
            ('v2',[self.proposal(),dict(finalize,hypothesis_id='0'*64)]),
            ('v2',[self.proposal(),dict(finalize,evidence_ids=['not-supplied'])]),
            ('v2',[self.proposal(),dict(finalize,edits=self.proposal()['edits'])]),
        ):
            with self.subTest(version=version,replies=len(replies)):
                result = self.versioned_loop(replies,proposal_contract=version)
                self.assertEqual(result['status'],'proposal_invalid')
                self.assertEqual(result['proposal_requests'],len(replies))
                self.assertFalse(result['repaired'])

    def test_v2_abstention_still_discards_answer_instead_of_finalizing(self):
        for status in ('no_candidate','insufficient_evidence'):
            with self.subTest(status=status):
                result = self.versioned_loop([self.proposal(),{
                    'status':status,'summary':'Withdraw the candidate.','edits':[]}])
                self.assertEqual(result['status'],status)
                self.assertEqual(result['proposal_requests'],2)
                self.assertEqual(len(result['checks']),2)
                self.assertFalse(result['repaired'])

    def test_finalize_record_cannot_dispatch_again_or_change_contract_version(self):
        finalize = {'status':'finalize_current','summary':'Keep candidate.','edits':[]}
        self.versioned_loop([self.proposal(),finalize],strategy='direct_api')
        original = loop._read(self.out,'report.json')
        bad = deepcopy(original);bad['rounds'].append(deepcopy(bad['rounds'][-1]))
        bad['proposal_requests'] = 3
        loop._write(self.out,'report.json',bad)
        with self.assertRaises(ContractError) as raised:loop.verify_loop(self.out)
        self.assertEqual(raised.exception.field,'dispatch_after_stop')
        claim = loop._read(self.out,'claim.json')
        claim['config']['proposal_contract'] = 'v404'
        loop._write(self.out,'claim.json',claim)
        original['config_sha256'] = loop.digest(claim['config'])
        original['artifact_hashes'] = loop._file_hashes(self.out,skip=('report.json',))
        loop._write(self.out,'report.json',original)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)

    def test_v1_contract_and_request_bytes_remain_unchanged(self):
        from dataclasses import asdict
        expected = {'direct_api:False':'950767c9ab09377c7322886038364a9cb6e319a6ca9daa907d999dee93f920e7',
            'direct_api:True':'a75a20294fc26aeccd0aab492eba0d4a1eaa24e9ae43e453c6c4fae488a3438f',
            'api_debugger:False':'54d9446604f93beddd77cdfe117d15063e5e7cbc457cfd36ca7257b20081328e',
            'api_debugger:True':'e11c310a8e715c922d5f5e01a5e5cee947ee399b83ae185fc4d46fa0bf6d73c9'}
        self.assertEqual(loop.digest(loop.output_contract()),
                         '858d7f0cf904759fcb6dc1659f1a4107ea2a09b8ad478df785b6890398f5f5c6')
        for strategy in ('direct_api','api_debugger'):
            for bound in (False,True):
                request = loop._request(self.files,'Legacy byte-compatibility fixture.',
                    ['builder.py'],['checks.py'],{},1,strategy=strategy,bound=bound)
                self.assertEqual(loop.digest(asdict(request)),expected[strategy+':'+str(bound)])
        contract = loop.output_contract();contract['statuses'].append('forged')
        self.assertEqual(loop.output_contract(),loop.OUTPUT_CONTRACT)
        result = self.versioned_loop([{'status':'no_candidate','summary':'No candidate.','edits':[]}],
                                     strategy='direct_api',proposal_contract='v1')
        self.assertNotIn('proposal_contract',loop._read(self.out,'claim.json')['config'])
        self.assertNotIn('proposal_contract',self.provider.requests[0]['metadata'])
        self.assertEqual(result,loop.verify_loop(self.out))

    def test_v2_contract_is_serialized_once_and_request_bound(self):
        for strategy in ('direct_api','api_debugger'):
            request = loop._request(self.files,'Public issue',['builder.py'],['checks.py'],{},2,
                strategy=strategy,bound=True,proposal_contract='v2')
            self.assertIn('status (candidate/no_candidate/insufficient_evidence/finalize_current)',request.messages[0]['content'])
            self.assertNotIn('status (candidate/no_candidate/insufficient_evidence),',request.messages[0]['content'])
            payload = json.loads(request.messages[1]['content'])
            self.assertEqual(payload['output_contract'],loop.output_contract('v2'))
            self.assertEqual(request.metadata['proposal_contract'],'v2')
            reply = {key:payload[key] for key in ('base_content_id','hypothesis_id','evidence_ids')}
            reply.update(status='finalize_current',summary='Keep candidate.',edits=[])
            from macr.providers.base import ModelResponse
            response = ModelResponse(content=json.dumps(reply))
            self.assertIsNone(loop._proposal(response,request,self.files,['builder.py'])[1])
            payload['proposal_contract'] = 'v1';request.messages[1]['content'] = json.dumps(payload)
            with self.assertRaises(ContractError):loop._proposal(response,request,self.files,['builder.py'])

    def test_invalid_proposal_version_rejects_before_output_or_dispatch(self):
        for version in ('v404',None,1,True):
            with self.subTest(version=version):
                with self.assertRaises(ContractError):
                    loop.run_simulated_loop(self.repo,'Issue',editable_paths=['builder.py'],
                        test_paths=['checks.py'],provider=None,verifier=None,out=self.out,
                        proposal_contract=version)
                self.assertFalse(self.out.exists())
                with self.assertRaises(ContractError):
                    loop.parse_proposal_content('{}',proposal_contract=version)

    def test_utf8_proposal_round_trip_uses_published_byte_contract(self):
        original = SOURCES['builder.py'] + '#' + '文'*5300 + '\n'
        changed = original.replace(OLD, NEW)
        self.files['builder.py'] = original.encode('utf-8')
        (self.repo/'builder.py').write_bytes(self.files['builder.py'])
        fixed = dict(self.files, **{'builder.py':changed.encode('utf-8')})
        reply = self.proposal(old=original, new=changed)
        reply['summary'] = '说明'*340
        result = self.run_loop([reply], [self.observation(self.files, self.tests[:1]),
                                        self.observation(fixed)])
        self.assertEqual(result['status'], 'simulation_passed')
        content = json.loads((self.out/'rounds/1/response.json').read_text())['content']
        self.assertLessEqual(len(content.encode('utf-8')), 65536)
        self.assertGreater(len(json.dumps(json.loads(content), ensure_ascii=True).encode()), 65536)
        request = json.loads((self.out/'rounds/1/intent.json').read_text())['request']
        self.assertEqual(request, self.provider.requests[0])
        self.assertEqual(json.loads(request['messages'][1]['content'])['files']['builder.py'], original)
        self.assertIn('65536 UTF-8 bytes', request['messages'][0]['content'])
        self.assertEqual(result, loop.verify_loop(self.out))
        self.assertEqual((self.out/'sources/1/builder.py').read_bytes(), fixed['builder.py'])
        self.assertEqual((self.repo/'builder.py').read_bytes(), self.files['builder.py'])
        self.assertFalse(result['repaired']); self.assertFalse(result['target_code_executed'])

    def test_proposal_parser_keeps_wire_edit_and_structure_limits(self):
        from macr.providers.base import ModelResponse
        request = loop._request(self.files, 'Public issue', ['builder.py'], ['checks.py'], {}, 1)
        binding = json.loads(request.messages[1]['content'])
        base = {key:binding[key] for key in ('base_content_id','hypothesis_id','evidence_ids')}
        base.update(status='no_candidate', summary='No supported edit.', edits=[])
        raw = json.dumps(base, ensure_ascii=False, separators=(',', ':'))
        boundary = raw + ' '*(65536-len(raw.encode('utf-8')))
        response = ModelResponse(content=boundary)
        proposal, changed = loop._proposal(response, request, self.files, ['builder.py'])
        self.assertEqual(proposal, base); self.assertIsNone(changed)
        self.assertEqual(response.content, boundary)
        too_large_edit = dict(base, status='candidate', edits=[{
            'file':'builder.py', 'old':OLD, 'new':'x'*16385}])
        deep = dict(base, summary=[[[[[[[[['too deep']]]]]]]]])
        invalid = [boundary+' ', raw[:-1]+',"status":"candidate"}',
            json.dumps(too_large_edit), json.dumps(deep),
            json.dumps(dict(base, summary='\ud800')),
            json.dumps(dict(base, status='candidate', edits=[{
                'file':'checks.py', 'old':'import unittest', 'new':'pass'}]))]
        for content in invalid:
            with self.subTest(kind=len(content)), self.assertRaises(ContractError):
                loop._proposal(ModelResponse(content=content), request, self.files, ['builder.py'])

    def setUp(self):
        self.files = {n: s.encode() for n, s in SOURCES.items()}
        self.repo = write_files(fixture_root(), SOURCES)
        self.out = fixture_root() / 'session'
        self.tests = loop.test_manifest(self.files, ['checks.py'])

    def proposal(self, old=OLD, new=NEW):
        return {'status': 'candidate', 'summary': 'scripted development edit',
                'edits': [{'file': 'builder.py', 'old': old, 'new': new}]}

    def observation(self, files, failed=(), status='completed'):
        return {'source_id': loop.source_id(files), 'execution_status': status,
                'outcomes': {n: ('failed' if n in failed else 'passed') for n in self.tests}
                    if status == 'completed' else {}}

    def run_loop(self, replies, observations=None, **kwargs):
        self.provider = loop.ScriptedPatchProvider(replies)
        self.verifier = None if observations is None else loop.ReplayVerifier(observations)
        return loop.run_simulated_loop(self.repo, 'Discount applies only to items.',
            editable_paths=['app.py', 'builder.py', 'records.py'], test_paths=['checks.py'],
            provider=self.provider, verifier=self.verifier, out=self.out, **kwargs)

    def test_failed_candidate_feedback_is_in_next_serialized_request(self):
        wrong = deepcopy(self.files); wrong['builder.py'] = wrong['builder.py'].replace(OLD.encode(), b'return 2000')
        fixed = deepcopy(wrong); fixed['builder.py'] = fixed['builder.py'].replace(b'return 2000', NEW.encode())
        fail = self.tests[0]
        result = self.run_loop([self.proposal(new='return 2000'), self.proposal(old='return 2000')],
            [self.observation(self.files, [fail]), self.observation(wrong, self.tests[1:]),
             self.observation(fixed)])
        self.assertEqual(result['status'], 'simulation_passed')
        self.assertEqual(result['proposal_requests'], 2)
        self.assertFalse(result['repaired']); self.assertFalse(result['target_code_executed'])
        self.assertEqual(result['candidate_origin'], 'scripted_provider')
        self.assertEqual(result['verification_origin'], 'synthetic_replay')
        payload = json.loads(self.provider.requests[1]['messages'][1]['content'])
        self.assertIn('return 2000', payload['files']['builder.py'])
        self.assertEqual(payload['feedback']['outcomes'][self.tests[1]], 'failed')
        self.assertEqual(payload['feedback']['source_id'], loop.source_id(wrong))
        self.assertEqual(result, loop.verify_loop(self.out))
        self.assertEqual((self.repo/'builder.py').read_bytes(), self.files['builder.py'])
        for n in (0, 1, 2):
            self.assertEqual((self.out/f'sources/{n}/checks.py').read_bytes(), self.files['checks.py'])

    def test_default_unavailable_validation_makes_no_proposal_or_repair(self):
        r = self.run_loop([self.proposal()])
        self.assertEqual(r['status'], 'verification_unavailable')
        self.assertEqual(self.provider.requests, [])
        self.assertFalse(r['repaired'])

    def test_invalid_test_edit_is_retained_and_never_validated(self):
        p = self.proposal(); p['edits'][0]['file'] = 'checks.py'
        r = self.run_loop([p], [self.observation(self.files, [self.tests[0]])])
        self.assertEqual(r['status'], 'proposal_invalid')
        self.assertEqual(self.verifier.calls, 1)
        self.assertTrue((self.out/'rounds/1/response.json').is_file())

    def test_unknown_provider_is_not_retried_or_relaunchable(self):
        r = self.run_loop([ProviderError('model_transport'), self.proposal()],
                          [self.observation(self.files, [self.tests[0]])])
        self.assertEqual(r['status'], 'provider_outcome_unknown')
        self.assertEqual(r['proposal_requests'], 1)
        with self.assertRaises(ContractError):
            self.run_loop([self.proposal()], [])

    def test_unknown_verification_stops_before_next_model_request(self):
        fixed = deepcopy(self.files); fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(), NEW.encode())
        r = self.run_loop([self.proposal(), self.proposal()],
            [self.observation(self.files, [self.tests[0]]), self.observation(fixed, status='outcome_unknown')])
        self.assertEqual(r['status'], 'verification_outcome_unknown')
        self.assertEqual(r['proposal_requests'], 1)

    def test_wrong_source_or_incomplete_replay_cannot_pass(self):
        for field in ('source_id', 'outcomes'):
            with self.subTest(field=field):
                self.out = fixture_root()/'session'
                o = self.observation(self.files, [self.tests[0]])
                o[field] = '0'*64 if field == 'source_id' else {}
                r = self.run_loop([self.proposal()], [o])
                self.assertEqual(r['status'], 'verification_invalid')
                self.assertEqual(r['proposal_requests'], 0)

    def test_tamper_with_saved_response_or_claimed_success_is_rejected(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; raw = json.loads(p.read_text()); raw['repaired'] = True
        p.write_text(json.dumps(raw))
        with self.assertRaises(ContractError): loop.verify_loop(self.out)

    def test_terminal_status_usage_and_reservation_are_rederived(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; original = json.loads(p.read_text())
        for field, value in (('status', 'simulation_passed'), ('input_tokens', 9),
                             ('unknown_request_reservation', True)):
            with self.subTest(field=field):
                bad = deepcopy(original); bad[field] = value; p.write_text(json.dumps(bad))
                with self.assertRaises(ContractError): loop.verify_loop(self.out)
        p.write_text(json.dumps(original))

    def test_feedback_cannot_contradict_normalized_observation_even_if_rehashed(self):
        self.run_loop([], [self.observation(self.files)])
        p = self.out/'report.json'; report = json.loads(p.read_text())
        receipt = report['checks'][0]; receipt['outcomes'][self.tests[0]] = 'failed'
        (self.out/'checks/0/receipt.json').write_text(json.dumps(receipt))
        report['artifact_hashes'] = loop._file_hashes(self.out, skip=('report.json',))
        p.write_text(json.dumps(report))
        with self.assertRaises(ContractError): loop.verify_loop(self.out)

    def test_three_failed_proposals_end_at_round_limit_and_no_fourth(self):
        files = self.files; observations = [self.observation(files, self.tests)]
        replies = []
        previous = OLD
        for value in ('return 2000', 'return 2001', 'return 2002'):
            replies.append(self.proposal(old=previous, new=value))
            files = deepcopy(files); files['builder.py'] = files['builder.py'].replace(previous.encode(), value.encode())
            observations.append(self.observation(files, self.tests)); previous = value
        r = self.run_loop(replies, observations)
        self.assertEqual(r['status'], 'round_limit'); self.assertEqual(r['proposal_requests'], 3)

    def test_no_candidate_is_preserved_as_abstention_without_validation(self):
        r = self.run_loop([{'status':'insufficient_evidence', 'summary':'Need a real trace.', 'edits':[]}],
                         [self.observation(self.files, self.tests)])
        self.assertEqual(r['status'], 'insufficient_evidence')
        self.assertEqual(self.verifier.calls, 1)

    def test_wrong_proposal_source_binding_stops(self):
        p = self.proposal(); p['base_content_id'] = '0'*64
        r = self.run_loop([p], [self.observation(self.files, self.tests)])
        self.assertEqual(r['status'], 'proposal_invalid')

    def test_real_cli_demo_is_scripted_and_cannot_repeat(self):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_repair_loop_demo.py'))
        with (patch('subprocess.run', side_effect=AssertionError('no target process')),
              patch('socket.create_connection', side_effect=AssertionError('no network'))):
            for action, code in (('simulate', 0), ('verify', 0), ('simulate', 2)):
                output = io.StringIO()
                with redirect_stdout(output):
                    result = namespace['main']([action, '--out', str(self.out)])
                self.assertEqual(result, code)
                report = json.loads(output.getvalue()); self.assertFalse(report['target_code_executed'])
            saved = self.out/'report.json'; report = json.loads(saved.read_text()); del report['repaired']
            saved.write_text(json.dumps(report))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(namespace['main'](['verify', '--out', str(self.out)]), 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'rejected')

    def test_input_token_reservation_prevents_overbudget_dispatch(self):
        r = self.run_loop([self.proposal()], [self.observation(self.files, [self.tests[0]])],
                          max_total_input_tokens=1)
        self.assertEqual(r['status'], 'budget_exhausted')
        self.assertEqual(r['proposal_requests'], 0)

    def test_non_scripted_provider_rejected_before_output_or_dispatch(self):
        with self.assertRaises(ContractError):
            loop.run_simulated_loop(self.repo, 'issue', editable_paths=['builder.py'],
                test_paths=['checks.py'], provider=object(), verifier=None, out=self.out)
        self.assertFalse(self.out.exists())
