"""Synthetic transcripts only; never start a runtime or borrow a real proof."""
import importlib.util
import copy
import unittest

VERSION='version: 0.6.0-dev (build 11521, commit b42b7e6d3)\n'
HELP='\n'.join([' - 0: generic output',' - 1: error',' - 2: warning',' - 3: info',' - 4: trace (more info)',' - 5: debug'])
ARGV=['/fixture/llama-server','--log-verbosity','5','--log-colors','off','--no-log-prefix','--no-log-timestamps','--no-log-jsonl',
    '--offline','--cache-ram','0','--no-cache-prompt','--no-context-shift','--no-webui','--no-agent',
    '--host','127.0.0.1','--port','18080','--device','none','--n-gpu-layers','0','--no-kv-offload',
    '--threads','2','--threads-batch','2','--threads-http','2','--parallel','1','--ctx-size','2048','--predict','384','--chat-template','chatml',
    '--model','/fixture/model.gguf','--alias','fixture-model','--batch-size','128','--ubatch-size','128','--temp','0','--top-p','1','--top-k','0','--min-p','0','--seed','0','--no-cont-batching','--no-warmup']
LOG=(b'res  add_waiting_: add task 7 to waiting list. current waiting = 0 (before add)\n'
    b'slot      release: id  0 | task 7 | stop processing: n_tokens = 12, truncated = 0\n'
    b'res  remove_waiti: remove task 7 from waiting list. current waiting = 1 (before remove)\n'
    b'srv  update_slots: all slots are idle\n')

class B11521EvidenceTests(unittest.TestCase):
    def adapter(self):
        name='macr.providers.b11521_evidence'
        self.assertIsNotNone(importlib.util.find_spec(name),'version-specific evidence adapter is missing')
        return __import__(name,fromlist=['unused'])

    def test_wrong_log_level_blocks_before_any_live_dispatch(self):
        a=self.adapter()
        for value in ['0','1','2','3','4','6']:
            args=ARGV.copy();args[args.index('--log-verbosity')+1]=value
            with self.assertRaises(ValueError):a.validate_preflight(VERSION,HELP,args)
        self.assertEqual(a.validate_preflight(VERSION,HELP,ARGV)['required_level'],5)

    def test_unknown_version_help_and_contradictory_logging_are_rejected(self):
        a=self.adapter()
        for version,helptext,args in [(VERSION.replace('11521','11522'),HELP,ARGV),(VERSION,HELP.replace('5: debug','5: info'),ARGV),
            (VERSION,HELP,ARGV+['--log-disable']),(VERSION,HELP,ARGV+['--verbosity','1']),
            (VERSION,HELP,ARGV+['--log-verbosity','1']),(VERSION,HELP,ARGV+['--log-jsonl']),
            (VERSION,HELP,ARGV+['--log-verbosity=1']),(VERSION,HELP,ARGV+['--host=0.0.0.0'])]:
            with self.assertRaises(ValueError):a.validate_preflight(version,helptext,args)

    def test_cpu_context_threads_and_offline_limits_are_not_relaxed(self):
        a=self.adapter()
        for flag,value in [('--host','0.0.0.0'),('--n-gpu-layers','1'),('--cache-ram','8192'),('--threads-http','3'),('--parallel','2'),('--ctx-size','4096'),('--predict','512'),('--chat-template','other'),('--temp','0.2'),('--seed','1')]:
            args=ARGV.copy();args[args.index(flag)+1]=value
            with self.assertRaises(ValueError):a.validate_preflight(VERSION,HELP,args)
        with self.assertRaises(ValueError):a.validate_preflight(VERSION,HELP,[s for s in ARGV if s!='--offline'])

    def test_complete_proof_needs_three_native_events_not_removed_http_logger(self):
        proof=self.adapter().idle_proof(LOG,elapsed_seconds=.5)
        self.assertTrue(proof['qualified']);self.assertEqual(proof['task_id'],7)
        self.assertEqual((proof['release_line'],proof['remove_waiting_line'],proof['idle_line']),(2,3,4))

    def test_missing_duplicate_and_other_task_events_cannot_pass(self):
        a=self.adapter()
        lines=LOG.splitlines(keepends=True)
        for log in [b'',*(b''.join(lines[:i]+lines[i+1:]) for i in range(4)),LOG+lines[0],LOG.replace(b'task 7 |',b'task 8 |'),LOG.replace(b'waiting = 0',b'waiting = 1')]:
            self.assertFalse(a.idle_proof(log,elapsed_seconds=.5)['qualified'])

    def test_source_threads_allow_remove_before_release_but_not_idle_before_release(self):
        a=self.adapter();lines=LOG.splitlines(keepends=True)
        self.assertTrue(a.idle_proof(b''.join([lines[0],lines[2],lines[1],lines[3]]),elapsed_seconds=.5)['qualified'])
        self.assertFalse(a.idle_proof(b''.join([lines[0],lines[3],lines[1],lines[2]]),elapsed_seconds=.5)['qualified'])

    def test_unknown_format_prefix_truncation_and_timeout_are_not_proof(self):
        a=self.adapter()
        for log in [LOG.replace(b'add_waiting_',b'push'),LOG.replace(b'current waiting',b'count'),b'I '+LOG,LOG.replace(b'\n',b'\nI ',1),LOG[:-1],LOG+b'\xff\n',LOG.replace(b'truncated = 0',b'truncated = 1')]:
            self.assertFalse(a.idle_proof(log,elapsed_seconds=.5)['qualified'])
        for seconds in [10,11,-1,float('nan')]:
            self.assertFalse(a.idle_proof(LOG,elapsed_seconds=seconds)['qualified'])

    def test_native_identity_is_separate_from_template_and_usage(self):
        a=self.adapter();props={'total_slots':1,'model_path':'/fixture/model.gguf','default_generation_settings':{'n_ctx':2048}}
        models={'data':[{'id':'fixture-model'}]}
        a.validate_model(props,models,weight_path='/fixture/model.gguf',model_id='fixture-model')
        for field,value in [('total_slots',2),('model_path','/other.gguf')]:
            changed=props|{field:value}
            with self.assertRaises(ValueError):a.validate_model(changed,models,weight_path='/fixture/model.gguf',model_id='fixture-model')
        with self.assertRaises(ValueError):a.validate_model(props,{'data':[{'id':'other'}]},weight_path='/fixture/model.gguf',model_id='fixture-model')

    def test_prompt_template_and_token_ceiling_checked_before_generation(self):
        a=self.adapter();messages=[{'role':'system','content':'fixture'},{'role':'user','content':'fixture-data'}]
        prompt='<|im_start|>system\nfixture<|im_end|>\n<|im_start|>user\nfixture-data<|im_end|>\n<|im_start|>assistant\n'
        ids=[151644,1,151645,151644,2,151645,151644]
        self.assertEqual(a.validate_prompt(messages,{'prompt':prompt},{'tokens':ids}),7)
        for template,tokens in [({'prompt':prompt+'<think>'},{'tokens':ids}),({'prompt':prompt},{'tokens':ids+[1]*1536}),({'prompt':prompt},{'tokens':[True]})]:
            with self.assertRaises(ValueError):a.validate_prompt(messages,template,tokens)

    def test_native_response_usage_fingerprint_and_outer_contract_are_independent(self):
        a=self.adapter();raw={'model':'fixture-model','system_fingerprint':'b11521-b42b7e6d3','usage':{'prompt_tokens':7,'completion_tokens':2,'total_tokens':9,'prompt_tokens_details':{'cached_tokens':0}},'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{}'}}]}
        self.assertEqual(a.validate_response(raw,http_status=200,model_id='fixture-model',input_tokens=7)['output_tokens'],2)
        length=copy.deepcopy(raw);length['choices'][0]['finish_reason']='length'
        self.assertEqual(a.validate_response(length,http_status=200,model_id='fixture-model',input_tokens=7)['finish_reason'],'length')
        for status in [503,200.0,True]:
            with self.assertRaises(ValueError):a.validate_response(raw,http_status=status,model_id='fixture-model',input_tokens=7)
        for changed in [raw|{'system_fingerprint':'other'},raw|{'model':'other'},raw|{'usage':raw['usage']|{'prompt_tokens':8}},raw|{'usage':raw['usage']|{'prompt_tokens_details':{'cached_tokens':1}}},raw|{'choices':[]}]:
            with self.assertRaises(ValueError):a.validate_response(changed,http_status=200,model_id='fixture-model',input_tokens=7)

if __name__=='__main__':unittest.main()
