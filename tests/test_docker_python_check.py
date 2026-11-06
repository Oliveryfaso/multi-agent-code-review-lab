"""Owned fixed bytes/fake frameworks only; never import or execute target files."""
from copy import deepcopy
from hashlib import sha256
import errno
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from macr.execution import python_check as check
except (ImportError, RuntimeError):
    check=None


class Case:
    def __init__(self,name,state='passed'):self.name,self.state=name,state
    def id(self):return self.name


class Suite(list):
    def run(self,result):
        for case in self:
            result.startTest(case)
            if case.state=='passed':result.addSuccess(case)
            elif case.state=='failed':result.addFailure(case,(AssertionError,None,None))
            elif case.state=='error':result.addError(case,(ValueError,None,None))
            elif case.state=='suberror':result.addSubTest(case,None,(ValueError,None,None))
            elif case.state=='skipped':result.addSkip(case,'DUMMY-PRIVATE-REASON')
            elif case.state=='xfail':result.addExpectedFailure(case,(AssertionError,None,None))
            elif case.state=='xpass':result.addUnexpectedSuccess(case)
            elif case.state=='noise':print('DUMMY-PRIVATE-OUTPUT'*2048)
            result.stopTest(case)
        return result


class FakeUnittest:
    TestResult=unittest.TestResult
    def __init__(self,cases):self.cases,self.loads=cases,[]
    def TestLoader(self):
        def load(names):self.loads.append(names);return Suite(self.cases)
        return SimpleNamespace(loadTestsFromNames=load)


class FakePytest:
    class UsageError(Exception):pass
    def __init__(self,names,states=None,*,collected=None,exit_code=None):
        self.names=names;self.states=states or {};self.collected=names if collected is None else collected
        self.exit_code=exit_code;self.args=None
    def main(self,args,plugins):
        self.args=args;p=plugins[0]
        try:p.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid=n) for n in self.collected]))
        except self.UsageError:return 4
        for name in self.names:
            state=self.states.get(name,'passed')
            for phase in ('setup','call','teardown'):
                p.pytest_runtest_logreport(SimpleNamespace(nodeid=name,when=phase,
                    outcome=state if phase=='call' else 'passed',wasxfail='authored' if state=='xfail' else None))
        return self.exit_code if self.exit_code is not None else int(any(x=='failed' for x in self.states.values()))


class DockerPythonCheckTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(check,'trusted container collector is missing')
        self.temp=tempfile.TemporaryDirectory(prefix='macr-collector-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=b'raise RuntimeError("source bytes must never execute in these tests")\n'
        (self.root/'checks.py').write_bytes(self.source)
        files={'checks.py':sha256(self.source).hexdigest()}
        self.job={'schema':'macr.docker.public-job.v1','nonce':'a'*32,
            'source_id':sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'argv_profile':'unittest','selectors':['checks'],
            'test_names':['checks.py::Checks.test_first','checks.py::Checks.test_second'],
            'files':files,'limits':{'cpu':1,'memory_bytes':512*1024*1024,'pids':32,'wall_seconds':60,'output_bytes':4096}}

    def test_job_source_commitment_and_nonexecution(self):
        parsed=check.parse_job(json.dumps(self.job).encode())
        self.assertEqual(parsed,self.job);check.verify_sources(parsed,self.root)
        self.assertEqual((self.root/'checks.py').read_bytes(),self.source)
        (self.root/'checks.py').write_bytes(b'changed')
        with self.assertRaises(check.ContractFailure):check.verify_sources(parsed,self.root)

    def test_strict_json_and_source_paths(self):
        bad=[self.job|{'extra':True},self.job|{'source_id':'0'*64},self.job|{'files':{'../private.py':'0'*64}},
             self.job|{'files':{'._macr_runner.py':'0'*64}},self.job|{'test_names':self.job['test_names']*2},
             self.job|{'limits':self.job['limits']|{'pids':True}},self.job|{'selectors':['-c']},
             self.job|{'argv_profile':'shell'}]
        for job in bad:
            with self.subTest(job=job),self.assertRaises(check.ContractFailure):check.parse_job(json.dumps(job).encode())
        with self.assertRaises(check.ContractFailure):check.parse_job(b'{"schema":"x","schema":"y"}')
        with self.assertRaises(check.ContractFailure):check.parse_job(b' '*131073)
        longnames=sorted('checks.py::Checks.test_'+('x'*1800)+str(i) for i in range(32))
        with self.assertRaises(check.ContractFailure):check.parse_job(json.dumps(self.job|{'test_names':longnames}).encode())

    def test_symlink_and_extra_source_are_not_accepted(self):
        (self.root/'checks.py').unlink();(self.root/'other.py').write_bytes(self.source)
        (self.root/'checks.py').symlink_to('other.py')
        with self.assertRaises(check.ContractFailure):check.verify_sources(self.job,self.root)

    def test_unittest_exact_ids_pass_fail_and_exception_counts(self):
        for state in ('error','suberror'):
            with self.subTest(state=state):
                framework=FakeUnittest([Case('checks.Checks.test_first'),Case('checks.Checks.test_second',state)])
                result=check.collect_unittest(self.job,framework=framework,root=self.root)
                self.assertEqual(result['outcomes'],{self.job['test_names'][0]:'passed',self.job['test_names'][1]:'failed'})
                self.assertEqual(result['status'],'completed');self.assertEqual(result['framework_errors'],1)
                self.assertEqual(framework.loads,[['checks']])

    def test_unittest_collection_missing_duplicate_skip_and_xfail_cannot_pass(self):
        for cases in ([Case('checks.Checks.test_first')],
                      [Case('checks.Checks.test_first'),Case('checks.Checks.test_first')],
                      [Case('checks.Checks.test_first'),Case('checks.Checks.test_second','skipped')],
                      [Case('checks.Checks.test_first'),Case('checks.Checks.test_second','xfail')],
                      [Case('checks.Checks.test_first'),Case('checks.Checks.test_second','xpass')]):
            with self.subTest(states=[c.state for c in cases]),self.assertRaises(check.ContractFailure):
                check.collect_unittest(self.job,framework=FakeUnittest(cases),root=self.root)

    def test_output_is_bounded_discarded_and_not_truncated_into_pass(self):
        framework=FakeUnittest([Case('checks.Checks.test_first'),Case('checks.Checks.test_second','noise')])
        with self.assertRaises(check.OutputLimit):check.collect_unittest(self.job,framework=framework,root=self.root)
        sink=check.BoundedSink(3);self.assertEqual(sink.write('abc'),3)
        with self.assertRaises(check.OutputLimit):sink.write('d')
        self.assertFalse(hasattr(sink,'getvalue'))

    def pytest_job(self):
        names=['checks.py::test_first[fixed::id]','checks.py::test_second']
        return self.job|{'argv_profile':'pytest','selectors':names,'test_names':names}

    def test_pytest_exact_nodeids_and_fixed_options(self):
        job=self.pytest_job();job['test_names'][0]='checks.py::test_first[dir//./case]'
        framework=FakePytest(job['test_names'],{job['test_names'][1]:'failed'})
        value=check.collect_pytest(job,framework=framework,root=self.root)
        self.assertEqual(value['status'],'completed');self.assertEqual(value['outcomes'][job['test_names'][1]],'failed')
        args=framework.args
        self.assertIn('/dev/null',args);self.assertIn('addopts=',args);self.assertIn('no:cacheprovider',args)
        self.assertTrue(all(str(self.root)+'/'+n in args for n in job['selectors']))

    def test_pytest_collection_skip_xfail_and_bad_exit_are_rejected(self):
        job=self.pytest_job()
        for framework in (FakePytest(job['test_names'],collected=job['test_names'][:1]),
                          FakePytest(job['test_names'],{job['test_names'][0]:'skipped'}),
                          FakePytest(job['test_names'],{job['test_names'][0]:'xfail'}),
                          FakePytest(job['test_names'],exit_code=2)):
            with self.subTest(framework=framework),self.assertRaises(check.ContractFailure):
                check.collect_pytest(job,framework=framework,root=self.root)

    def test_probe_failure_precedes_any_collector_and_is_categorical(self):
        calls=[]
        def probe(job):calls.append('probe');raise check.ContractFailure('isolation_probe_failed')
        def collect(job):calls.append('target');raise AssertionError('must not run')
        value=check.execute(self.job,probe=probe,collector=collect)
        self.assertEqual(calls,['probe']);self.assertEqual(value['status'],'error')
        self.assertEqual(value['outcomes'],{});self.assertEqual(value['probes'],{'passed':False})
        self.assertEqual(value['reason'],'isolation_probe_failed')

    def test_success_and_errors_keep_envelope_and_do_not_echo_exception(self):
        observed=[]
        def collect(job):observed.append(job);return {'status':'completed','outcomes':dict.fromkeys(job['test_names'],'passed'),'framework_errors':0}
        result=check.execute(self.job,probe=lambda job:True,collector=collect)
        self.assertEqual(result['probes'],{'passed':True});self.assertEqual(result['nonce'],'a'*32)
        self.assertEqual(result['source_id'],self.job['source_id']);self.assertEqual(len(observed),1)
        def fail(job):raise ValueError('DUMMY-SECRET-MUST-NOT-ESCAPE')
        result=check.execute(self.job,probe=lambda job:True,collector=fail)
        self.assertEqual(result['status'],'error');self.assertNotIn('DUMMY-SECRET',json.dumps(result))
        def limit(job):raise check.OutputLimit()
        self.assertEqual(check.execute(self.job,probe=lambda job:True,collector=limit)['status'],'resource_limit')

    def test_probe_metadata_validates_effective_limits_without_host_queries(self):
        reads={'/proc/self/status':'Uid:\t65534\t65534\t65534\t65534\nNoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n',
            '/sys/fs/cgroup/memory.max':str(512*1024*1024),'/sys/fs/cgroup/pids.max':'32','/sys/fs/cgroup/cpu.max':'100000 100000'}
        options={'read_text':lambda p:reads[p],'uid':lambda:65534,'readonly':lambda p:True,
                 'writable':lambda p:True,'net_interfaces':lambda:['lo']}
        self.assertTrue(check.probe_isolation(self.job,**options))
        for bad in ({'/proc/self/status':reads['/proc/self/status'].replace('Seccomp:\t2','Seccomp:\t0')},
                    {'/sys/fs/cgroup/memory.max':'max'},{'/sys/fs/cgroup/pids.max':'33'},
                    {'/sys/fs/cgroup/cpu.max':'200000 100000'}):
            with self.subTest(bad=bad),self.assertRaises(check.ContractFailure):
                check.probe_isolation(self.job,**(options|{'read_text':lambda p:(bad|reads)[p] if p not in bad else bad[p]}))
        for override in ({'uid':lambda:0},{'readonly':lambda p:False},{'writable':lambda p:False},
                         {'net_interfaces':lambda:['lo','eth0']}):
            with self.subTest(override=override),self.assertRaises(check.ContractFailure):check.probe_isolation(self.job,**(options|override))

    def test_main_emits_single_bounded_json_using_only_injected_probe_and_collector(self):
        path=self.root/'._macr_job.json';path.write_text(json.dumps(self.job))
        output=io.StringIO()
        collector=lambda job:{'status':'completed','outcomes':dict.fromkeys(job['test_names'],'passed'),'framework_errors':0}
        code=check.main([str(path)],output=output,probe=lambda job:True,collector=collector,input_root=self.root)
        self.assertEqual(code,0);result=json.loads(output.getvalue())
        self.assertEqual(set(result),{'schema','nonce','source_id','argv_profile','test_names','outcomes','status','probes','framework_errors','reason'})
        self.assertNotIn('source bytes',output.getvalue());self.assertLess(len(output.getvalue().encode()),4096)

    def test_result_parser_binds_every_field_and_rejects_false_passes(self):
        value=check.execute(self.job,probe=lambda job:True,
            collector=lambda job:{'status':'completed','outcomes':dict.fromkeys(job['test_names'],'passed'),'framework_errors':0})
        self.assertEqual(check.parse_result(json.dumps(value).encode(),self.job),value)
        for change in ({'nonce':'b'*32},{'source_id':'b'*64},{'argv_profile':'pytest'},
                       {'test_names':list(reversed(value['test_names']))},{'probes':{'passed':False}},
                       {'outcomes':{}},{'outcomes':value['outcomes']|{'private.py::Test.test_no':'passed'}},
                       {'framework_errors':1},{'framework_errors':True},{'reason':'DUMMY-PRIVATE-REASON'},
                       {'status':'error'},{'extra':True}):
            with self.subTest(change=change),self.assertRaises(check.ContractFailure):
                check.parse_result(json.dumps(value|change).encode(),self.job)
        with self.assertRaises(check.ContractFailure):check.parse_result(b'{"schema":"x","schema":"y"}',self.job)
        with self.assertRaises(check.ContractFailure):check.parse_result(b' '*(self.job['limits']['output_bytes']+1),self.job)
        failure=check.execute(self.job,probe=lambda job:False)
        self.assertEqual(check.parse_result(json.dumps(failure).encode(),self.job)['status'],'error')

    def test_readonly_probe_needs_kernel_mount_flag_and_rejected_write(self):
        def denied(*args):raise OSError(errno.EACCES,'authored denial')
        self.assertTrue(check._readonly('/authored',statvfs=lambda p:SimpleNamespace(f_flag=os.ST_RDONLY),open_file=denied))
        self.assertFalse(check._readonly('/authored',statvfs=lambda p:SimpleNamespace(f_flag=0),open_file=denied))
        def readonly(*args):raise OSError(errno.EROFS,'authored readonly')
        self.assertTrue(check._readonly('/authored',statvfs=lambda p:SimpleNamespace(f_flag=os.ST_RDONLY),open_file=readonly))
        for category in (errno.EACCES,errno.EPERM,errno.EROFS):
            def unavailable(path):raise OSError(category,'authored unavailable metadata')
            with self.subTest(category=category):self.assertFalse(check._readonly('/authored',statvfs=unavailable,open_file=denied))
