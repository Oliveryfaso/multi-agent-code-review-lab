"""Docker lifecycle with a stateful authored engine; NO Docker/target execution."""
from copy import deepcopy
from dataclasses import asdict
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from macr.execution.docker_backend import DockerBackend,PROFILE,VERSION,INFO,IMAGE,VOLUME,CONTAINER,DockerError
from macr.execution.docker_process import CommandResult
from macr.investigation.records import CheckSpec,Limits
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore
from macr.investigation import repair_loop as loop
from macr.investigation.repair_verification import BackendVerifier,public_request


def response(data=b'',code=0,status='completed',stderr=b''):
    return CommandResult(code,json.dumps(data).encode() if not isinstance(data,bytes) else data,stderr,status,True)


class AuthoredEngine:
    def __init__(self,config):
        self.config=config;self.calls=[];self.containers={};self.volumes={};self.counter=0
        self.failed=False;self.start_unknown=False;self.remove_failure=False;self.policy_tamper=False
        self.archive=None;self.wire=None;self.seen_target=False
        self.failure_sources=set()

    def __call__(self,args,*,input_bytes=b'',**kwargs):
        self.calls.append(deepcopy(args))
        c=self.config
        if args[0]=='version':return response({'server':c['engine_version'],'client':'29.7.2','api':'1.54'})
        if args[0]=='info':return response({'id':c['daemon_id'],'os':'linux','architecture':'aarch64',
            'security':['name=seccomp,profile=builtin'],'cgroup':'2','memory':True,'swap':True,'cpu':True,'pids':True,
            'version':c['engine_version'],'swarm_manager':False})
        if args[:2]==['image','inspect']:return response({'id':c['image_id'],'os':'linux','architecture':'arm64',
            'digests':[c['image_ref']],'volumes':None,'env_keys':['PATH','LANG','PYTHON_VERSION']})
        def labels():return dict(v.split('=',1) for i,v in enumerate(args) if i and args[i-1]=='--label')
        if args[:2]==['volume','create']:
            self.volumes.setdefault(args[-1],{'name':args[-1],'driver':'local','scope':'local','options':None,'labels':labels()})
            return response(args[-1].encode()+b'\n')
        if args[:2]==['volume','inspect']:
            if args[-1] not in self.volumes:return response(b'\n',code=1,stderr=('Error response from daemon: get '+args[-1]+': no such volume\n').encode())
            return response(self.volumes[args[-1]])
        if args[:2]==['volume','rm']:
            if self.remove_failure:return response(code=1,stderr=b'authored remove failed')
            del self.volumes[args[-1]];return response(args[-1].encode())
        if args[:2]==['container','create']:
            self.counter+=1;cid=f'{self.counter:064x}'
            value=lambda flag:args[args.index(flag)+1]
            mount=value('--mount');name=value('--name');role=labels()['macr.role']
            tmpfs=dict(v.split(':',1) for i,v in enumerate(args) if i and args[i-1]=='--tmpfs')
            self.containers[cid]={'id':cid,'image':c['image_id'],'labels':labels(),'name':name,
                'user':value('--user'),'entrypoint':[value('--entrypoint')],'cmd':args[args.index(c['image_id'])+1:],
                'workdir':value('--workdir'),'env_keys':[v.split('=',1)[0] for i,v in enumerate(args) if i and args[i-1]=='--env'],
                'state':{'Status':'created','Running':False,'Pid':0,'ExitCode':0},'restart_count':0,
                'host':{'ReadonlyRootfs':'--read-only' in args,'NetworkMode':'bridge' if self.policy_tamper and role=='target' else value('--network'),
                    'Privileged':False,'CapDrop':[value('--cap-drop')],'SecurityOpt':[v for i,v in enumerate(args) if i and args[i-1]=='--security-opt'],
                    'Memory':int(value('--memory')),'MemorySwap':int(value('--memory-swap')),'NanoCpus':int(float(value('--cpus'))*1e9),
                    'PidsLimit':int(value('--pids-limit')),'IpcMode':value('--ipc'),'PidMode':'','CgroupnsMode':value('--cgroupns'),
                    'Binds':None,'Devices':[],'DeviceRequests':None,'VolumesFrom':None,'PortBindings':{},'PublishAllPorts':False,
                    'RestartPolicy':{'Name':value('--restart')},'LogConfig':{'Type':value('--log-driver')},'Tmpfs':tmpfs},
                'mounts':[{'type':'volume','name':mount.split('src=')[1].split(',')[0],'destination':'/input','rw':role=='stager'}]}
            return response(cid.encode()+b'\n')
        if args[:2]==['container','inspect']:
            value=self.containers.get(args[-1]) or next((v for v in self.containers.values() if v['name']==args[-1]),None)
            return response(value) if value else response(b'\n',code=1,stderr=('Error response from daemon: No such container: '+args[-1]+'\n').encode())
        if args[:2]==['container','cp']:
            self.archive=input_bytes
            with tarfile.open(fileobj=io.BytesIO(input_bytes)) as archive:
                assert all(m.isfile() and not m.name.startswith('/') and '..' not in Path(m.name).parts for m in archive)
                self.wire=json.loads(archive.extractfile('._macr_job.json').read())
            return response()
        if args[:2]==['container','start']:
            self.seen_target=True;value=self.containers[args[-1]]
            failed=self.failed or self.wire['source_id'] in self.failure_sources
            value['state'].update(Status='running' if self.start_unknown else 'exited',Running=self.start_unknown,Pid=123 if self.start_unknown else 0,ExitCode=int(failed))
            if self.start_unknown:return response(status='timeout',code=None)
            wire=self.wire
            return response({'schema':'macr.docker.public-result.v1','nonce':wire['nonce'],'source_id':wire['source_id'],
                'argv_profile':wire['argv_profile'],'test_names':wire['test_names'],'outcomes':{n:'failed' if failed and i==0 else 'passed' for i,n in enumerate(wire['test_names'])},
                'status':'completed','probes':{'passed':True},'framework_errors':0,'reason':None},code=int(failed))
        if args[:2]==['container','kill']:
            self.containers[args[-1]]['state'].update(Status='exited',Running=False,Pid=0,ExitCode=137);return response()
        if args[:2]==['container','rm']:
            if self.remove_failure:return response(code=1,stderr=b'authored remove failed')
            del self.containers[args[-1]];return response()
        raise AssertionError('unexpected Docker verb: '+repr(args[:2]))


class DockerBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='macr-docker-own-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();repo=self.root/'source';repo.mkdir()
        self.files={'calc.py':b'def value():\n    return 1\n','checks.py':b'import unittest\nfrom calc import value\nclass Check(unittest.TestCase):\n    def test_public(self):\n        self.assertEqual(value(),1)\n'}
        for p,b in self.files.items():(repo/p).write_bytes(b)
        self.bundle=SnapshotStore(self.root/'capture/snapshots',allowed_root=self.root/'capture').capture(repo,RepositoryScope().inventory(repo,loop.PROFILE))
        self.config={'schema':'macr.docker.backend.v1','backend_id':'authored-engine','docker_path':'/authored/bin/docker','docker_sha256':'d'*64,
            'endpoint':'unix:///authored/docker.sock','daemon_id':'authored-daemon','engine_version':'29.7.2',
            'image_ref':'docker.io/library/python@sha256:'+'b'*64,'image_id':'sha256:'+'a'*64,'architecture':'arm64',
            'control_root':str(self.root/'control'),'snapshot_root':str(self.root/'capture'),'expires_at':int(time.time())+600,'max_jobs':8}
        self.engine=AuthoredEngine(self.config)
        self.backend=DockerBackend(self.config,enabled=True,runner=self.engine)
        self.host=patch.object(self.backend,'_host_identity',return_value=['authored-host']);self.host.start();self.addCleanup(self.host.stop)
        self.check=CheckSpec('repair-0',selectors=['checks'],execution_profile=PROFILE,limits=Limits(1,64*1024*1024,16,30,8192))

    def qualify(self):self.assertTrue(self.backend.qualify(),self.backend.last_error)

    def test_disabled_backend_has_zero_host_or_engine_actions(self):
        backend=DockerBackend(self.config,runner=self.engine)
        self.assertFalse(backend.qualify());self.assertEqual(self.engine.calls,[])
        self.assertFalse(backend.capabilities().network_disabled)
        self.assertEqual(backend.run(self.check,self.bundle.ref).execution_status,'blocked')

    def test_real_transport_accepts_both_generated_container_create_commands(self):
        from macr.execution.docker_process import _arguments
        job={'owner':'1'*32,'key':'2'*64,'volume':'macr-input','stager_name':'macr-stage',
             'target_name':'macr-test','spec':asdict(self.check)}
        for role in ('stager','target'):
            with self.subTest(role=role):self.assertTrue(_arguments(self.backend._create_args(job,role)))

    def test_absence_uses_exact_versioned_resource_error_not_a_substring(self):
        self.qualify()
        for kind,name,error in (
            ('container','a'*64,'Error response from daemon: No such container: '+'a'*64+'\n'),
            ('volume','macr-owned-input','Error response from daemon: get macr-owned-input: no such volume\n')):
            with self.subTest(kind=kind),self.backend._lock():
                self.backend.runner=lambda *a,**k:response(b'\n',code=1,stderr=error.encode())
                self.assertTrue(self.backend._absent(kind,name,self.backend.clock()+10))
                for wrong in (error+'permission denied\n',error.replace(name,'other'), 'permission denied: '+error):
                    self.backend.runner=lambda *a,e=wrong,**k:response(b'\n',code=1,stderr=e.encode())
                    self.assertFalse(self.backend._absent(kind,name,self.backend.clock()+10))

    def test_actual_lifecycle_stages_readonly_input_and_closes_every_owned_resource(self):
        self.qualify();result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual((result.execution_status,result.observed_status),('completed','passed'))
        self.assertEqual((self.engine.containers,self.engine.volumes),({},{}))
        self.assertTrue(self.engine.seen_target);self.assertIsNotNone(self.engine.archive)
        create=[a for a in self.engine.calls if a[:2]==['container','create']]
        self.assertEqual(len(create),2);self.assertIn('readonly',create[1][create[1].index('--mount')+1])
        self.assertFalse(any(a[:2] in (['container','exec'],['image','pull']) for a in self.engine.calls))

    def test_failed_test_is_completed_failure_not_unknown_or_success(self):
        self.qualify();self.engine.failed=True;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual((result.execution_status,result.observed_status),('completed','failed'))
        self.assertEqual(result.test_counts['failed'],1);self.assertEqual(self.engine.volumes,{})

    def test_unknown_start_is_not_replayed_and_remains_quarantined_after_restart(self):
        self.qualify();self.engine.start_unknown=True;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertEqual(self.engine.containers,{})
        self.assertFalse(self.backend.capabilities().network_disabled)
        restarted=DockerBackend(self.config,enabled=True,runner=self.engine)
        with patch.object(restarted,'_host_identity',return_value=['authored-host']):self.assertFalse(restarted.qualify())
        self.assertEqual(sum(a[:2]==['container','start'] for a in self.engine.calls),1)

    def test_policy_mismatch_stops_before_any_target_start(self):
        self.qualify();self.engine.policy_tamper=True;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.engine.seen_target)
        self.assertEqual((self.engine.containers,self.engine.volumes),({},{}))

    def test_failed_cleanup_withholds_pass_and_cached_result(self):
        self.qualify();self.engine.remove_failure=True;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.backend.receipts)

    def test_final_record_failure_cannot_publish_a_completed_receipt(self):
        self.qualify();record=self.backend._record
        def failing_record(job):
            if job.get('state')=='completed':raise OSError('authored final write failure')
            return record(job)
        with patch.object(self.backend,'_record',side_effect=failing_record):
            result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.backend.ready)
        self.assertFalse(self.backend.receipts)
        verifier=BackendVerifier(self.backend,self.backend.public_result,backend_id=self.config['backend_id'],execution_profile=PROFILE)
        request=public_request(verifier.identity,self.bundle.ref,self.check,loop.source_id(self.files),loop.test_manifest(self.files,['checks.py']))
        with self.assertRaises(DockerError):self.backend.public_result(request)
        self.assertEqual((self.engine.containers,self.engine.volumes),({},{}))

    def test_rejected_contender_cannot_clear_active_run_or_its_cancellation(self):
        self.qualify();entered=threading.Event();release=threading.Event();results=[];errors=[]
        def blocked_start(args,**kwargs):
            if args[:2]==['container','start']:
                entered.set()
                if not release.wait(2):raise AssertionError('authored release deadline')
            return self.engine(args,**kwargs)
        def run_first():
            try:results.append(self.backend.run(self.check,self.bundle.ref))
            except BaseException as error:errors.append(error)
        self.backend.runner=blocked_start
        first=threading.Thread(target=run_first,name='owned-docker-fixture')
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            active=self.backend.active;self.assertIsNotNone(active)
            contender=self.backend.run(self.check,self.bundle.ref)
            self.assertEqual(contender.execution_status,'blocked')
            self.assertIs(self.backend.active,active)
            self.assertEqual(self.backend.stop(self.check.check_id),'running')
            self.assertTrue(self.backend.cancel.is_set())
        finally:
            release.set();first.join(3)
        self.assertFalse(first.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(results),1)
        self.assertIsNone(self.backend.active)

    def test_admission_expiry_during_started_job_still_allows_owned_closure(self):
        self.qualify();expired=[False];now=time.time()
        def execution(args,**kwargs):
            value=self.engine(args,**kwargs)
            if args[:2]==['container','start']:expired[0]=True
            return value
        self.backend.runner=execution
        with patch('macr.execution.docker_backend.time.time',side_effect=lambda:self.config['expires_at']+1 if expired[0] else now):
            result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual((result.execution_status,result.observed_status),('completed','passed'))
        self.assertEqual((self.engine.containers,self.engine.volumes),({},{}))
        calls=len(self.engine.calls);self.backend.config['expires_at']=1
        self.assertEqual(self.backend.run(self.check,self.bundle.ref).execution_status,'blocked')
        self.assertEqual(len(self.engine.calls),calls)

    def test_public_reader_is_exact_and_never_executes_again(self):
        self.qualify();verifier=BackendVerifier(self.backend,self.backend.public_result,backend_id=self.config['backend_id'],execution_profile=PROFILE)
        names=loop.test_manifest(self.files,['checks.py'])
        request=public_request(verifier.identity,self.bundle.ref,self.check,loop.source_id(self.files),names)
        audit=verifier.observe(request,self.bundle.ref,self.check)
        self.assertIsNone(audit['failure']);self.assertEqual(audit['receipt']['observation']['outcomes'],{names[0]:'passed'})
        calls=len(self.engine.calls);self.assertEqual(self.backend.public_result(request),audit['receipt']);self.assertEqual(len(self.engine.calls),calls)
        bad=deepcopy(request);bad['source_id']='e'*64
        with self.assertRaises(DockerError):self.backend.public_result(bad)
        self.assertEqual(len(self.engine.calls),calls)

    def test_same_snapshot_and_spec_cannot_be_executed_twice(self):
        self.qualify();first=self.backend.run(self.check,self.bundle.ref);self.assertEqual(first.execution_status,'completed')
        calls=len([a for a in self.engine.calls if a[:2]==['container','start']])
        second=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(second.execution_status,'blocked');self.assertEqual(sum(a[:2]==['container','start'] for a in self.engine.calls),calls)

    def test_expiry_unknown_artifacts_and_malformed_selectors_fail_before_resources(self):
        self.qualify();self.engine.calls.clear()
        for field,value in (('input_artifacts',['/private/answer']),('selectors',['-x']),('execution_profile','none')):
            with self.subTest(field=field):
                spec=deepcopy(self.check);setattr(spec,field,value)
                self.assertEqual(self.backend.run(spec,self.bundle.ref).execution_status,'blocked')
        self.assertEqual(self.engine.calls,[])
        self.backend.config['expires_at']=1;self.assertFalse(self.backend.capabilities().network_disabled)

    def test_mismatched_engine_image_or_inherited_volumes_never_qualify(self):
        for field,value in (('id','different'),('volumes',{'/unexpected':{}}),('env_keys',['AWS_SECRET_ACCESS_KEY'])):
            with self.subTest(field=field):
                def altered(args,**kwargs):
                    result=self.engine(args,**kwargs)
                    if args[:2]==['image','inspect']:
                        body=json.loads(result.stdout);body[field]=value;return response(body)
                    return result
                self.backend.runner=altered
                self.assertFalse(self.backend.qualify());self.assertFalse(self.backend.capabilities().secrets_absent)
        self.assertFalse(any(a[:2]==['container','create'] for a in self.engine.calls))

    def test_malformed_engine_metadata_returns_unavailable_without_resources(self):
        for data in ([],{'id':self.config['daemon_id'],'version':'29.7.2','swarm_manager':False,'os':'linux',
                'architecture':'arm64','cgroup':'2','memory':True,'swap':True,'cpu':True,'pids':True,'security':1}):
            with self.subTest(data_type=type(data).__name__):
                def malformed(args,**kwargs):
                    return response(data) if args[0]=='info' else self.engine(args,**kwargs)
                self.backend.runner=malformed
                self.assertFalse(self.backend.qualify());self.assertFalse(self.backend.ready)
        self.assertFalse(any(a[:2]==['container','create'] for a in self.engine.calls))

    def test_workbench_reaches_concrete_lifecycle_and_retains_unattested_feedback(self):
        import test_workbench_repair as fixtures
        from macr.providers.repair_binding import RepairProviderBinding
        helper=fixtures.WorkbenchRepairTests('runTest');helper.setUp();self.addCleanup(helper.doCleanups)
        config=deepcopy(self.config);config.update(control_root=str(helper.base/'docker-control'),snapshot_root=str(helper.root/'repairs'))
        engine=AuthoredEngine(config);engine.failure_sources={loop.source_id(helper.files)}
        backend=DockerBackend(config,enabled=True,runner=engine)
        with patch.object(backend,'_host_identity',return_value=['authored-host']):
            self.assertTrue(backend.qualify(),backend.last_error)
            verifier=BackendVerifier(backend,backend.public_result,backend_id=config['backend_id'],execution_profile=PROFILE)
            transport=fixtures.AuthoredTransport([helper.candidate()])
            owner=helper.owner(repair_execution_enabled=True,repair_verifier_factory=lambda:verifier,
                repair_provider_factory=lambda c:RepairProviderBinding(c,transport=transport))
            state=helper.finish(owner,helper.payload());result=owner.repair_result(state['id'])
        self.assertEqual(result['report']['status'],'public_tests_reported_passed')
        self.assertEqual(result['report']['verification_origin'],'backend_reported_unattested')
        self.assertIsNone(result['report']['target_code_executed']);self.assertFalse(result['report']['repaired'])
        self.assertEqual(len(transport.requests),1);self.assertEqual(sum(a[:2]==['container','start'] for a in engine.calls),2)
        self.assertEqual((engine.containers,engine.volumes),({},{}))
        owner.shutdown();helper.repo.rename(helper.base/'moved-source');helper.task_path.unlink();helper.config_path.unlink()
        reopened=helper.owner()
        with patch.object(backend,'runner',side_effect=AssertionError('history must not execute')):
            self.assertEqual(reopened.repair_result(state['id'])['report'],result['report'])

    def test_missing_snapshot_is_read_only_and_cannot_create_a_storage_tree(self):
        self.qualify();self.engine.calls.clear();ref=deepcopy(self.bundle.ref)
        missing=self.root/'capture/unowned/snapshots'/ref.snapshot_id
        ref.root_ref=str(missing/'files');ref.content_manifest_ref=str(missing/'manifest.json')
        self.assertEqual(self.backend.run(self.check,ref).execution_status,'blocked')
        self.assertFalse((self.root/'capture/unowned').exists());self.assertEqual(self.engine.calls,[])

    def test_snapshot_reader_mode_cannot_create_a_new_capture(self):
        reader=SnapshotStore(self.root/'capture/snapshots',allowed_root=self.root/'capture',read_only=True)
        self.assertEqual(reader.read_verified(self.bundle.ref,'calc.py'),self.files['calc.py'])
        with self.assertRaises(ValueError):
            reader.capture(self.root/'source',RepositoryScope().inventory(self.root/'source',loop.PROFILE))

    def test_snapshot_reader_mode_cannot_export_or_create_destination(self):
        reader=SnapshotStore(self.root/'capture/snapshots',allowed_root=self.root/'capture',read_only=True)
        destination=self.root/'capture/unowned/export'
        with self.assertRaises(ValueError) as raised:reader.export_verified(self.bundle.ref,destination)
        self.assertEqual(raised.exception.code,'forbidden_action')
        self.assertFalse(destination.parent.exists())

    def test_create_reply_loss_is_reconciled_for_cleanup_without_recreation(self):
        self.qualify()
        def lost(args,**kwargs):
            value=self.engine(args,**kwargs)
            if args[:2]==['container','create']:return response(status='timeout',code=None)
            return value
        self.backend.runner=lost;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.engine.seen_target)
        self.assertEqual(sum(a[:2]==['container','create'] for a in self.engine.calls),1)
        self.assertEqual((self.engine.containers,self.engine.volumes),({},{}))

    def test_foreign_volume_collision_is_never_adopted_or_deleted(self):
        self.qualify()
        def collision(args,**kwargs):
            value=self.engine(args,**kwargs)
            if args[:2]==['volume','create']:self.engine.volumes[args[-1]]['labels']['macr.owner']='foreign'
            return value
        self.backend.runner=collision;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.engine.seen_target)
        self.assertFalse(any(a[:2]==['volume','rm'] for a in self.engine.calls))
        self.assertEqual(len(self.engine.volumes),1)

    def test_changed_engine_blocks_cleanup_instead_of_guessing_old_ownership(self):
        self.qualify()
        def changed(args,**kwargs):
            value=self.engine(args,**kwargs)
            if args[:2]==['container','start']:self.engine.config=deepcopy(self.config)|{'daemon_id':'other-engine'}
            return value
        self.backend.runner=changed;result=self.backend.run(self.check,self.bundle.ref)
        self.assertEqual(result.execution_status,'outcome_unknown');self.assertFalse(self.backend.ready)
        self.assertEqual(len(self.engine.containers),1);self.assertEqual(len(self.engine.volumes),1)

    def test_control_lock_refuses_another_owner_and_symlink_alias(self):
        self.qualify();calls=len(self.engine.calls)
        other=DockerBackend(self.config,enabled=True,runner=self.engine)
        with self.backend._lock(),patch.object(other,'_host_identity',return_value=['authored-host']):
            self.assertFalse(other.qualify());self.assertEqual(other.last_error,'docker_busy')
        self.assertEqual(len(self.engine.calls),calls)
        link=self.root/'control-alias';link.symlink_to(self.backend.root,target_is_directory=True)
        alias=DockerBackend(self.config|{'control_root':str(link)},enabled=True,runner=self.engine)
        with patch.object(alias,'_host_identity',return_value=['authored-host']):self.assertFalse(alias.qualify())
        self.assertEqual(len(self.engine.calls),calls)

    def test_replaced_lock_node_cannot_authorize_commands_or_a_second_owner(self):
        self.qualify();calls=len(self.engine.calls)
        with self.backend._lock():
            path=self.backend.root/'owner.lock';path.unlink();path.write_bytes(b'');path.chmod(0o600)
            with self.assertRaises(DockerError):self.backend._call(['info','--format',INFO],self.backend.clock()+10)
            other=DockerBackend(self.config,enabled=True,runner=self.engine)
            with patch.object(other,'_host_identity',return_value=['authored-host']):self.assertFalse(other.qualify())
        self.assertEqual(len(self.engine.calls),calls)

    def test_pytest_parameter_id_bytes_survive_the_complete_driver_path(self):
        files={'case.py':b'import pytest\n@pytest.mark.parametrize("value", [1], ids=["dir//./case"])\ndef check_value(value):\n    assert value == 1\n'}
        repo=self.root/'pytest-source';repo.mkdir()
        for path,body in files.items():(repo/path).write_bytes(body)
        bundle=SnapshotStore(self.root/'capture/pytest-snapshots',allowed_root=self.root/'capture').capture(repo,RepositoryScope().inventory(repo,loop.PROFILE))
        node='case.py::check_value[dir//./case]'
        spec=deepcopy(self.check);spec.argv_profile='pytest';spec.selectors=[node]
        self.qualify();result=self.backend.run(spec,bundle.ref)
        self.assertEqual((result.execution_status,result.observed_status),('completed','passed'))
        self.assertEqual(self.engine.wire['selectors'],[node]);self.assertEqual(self.engine.wire['test_names'],[node])
