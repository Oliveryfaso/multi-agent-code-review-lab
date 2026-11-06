"""Opt-in Docker lifecycle, never a host-code fallback or an execution grant.

Only an already approved dedicated engine/image may be qualified. No engine,
image, login, service or VM is created by qualification; run() owns its new
containers/volume, and withholds completion until their removal is confirmed.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import fcntl
import io
import os
from pathlib import Path
import re
import stat
import tarfile
import threading
import time
from uuid import uuid4

from macr.investigation.evidence_workspace import safe_write
from macr.investigation.repair_io import encode_bounded, load_json
from macr.investigation.records import BackendCapabilities, CheckSpec, SnapshotRef
from macr.investigation.scope import open_directory, read_safe, safe_parent, validate_workspace_path
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError, decode_record
from .results import classify_check

PROFILE='docker-public-python-v1'
CAP=1024*1024
MAX_INPUT=16*CAP
_ID=re.compile('[0-9a-f]{64}\\Z')
_HEX=re.compile('[0-9a-f]{64}\\Z')
_ENV={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/work','LANG':'C.UTF-8',
      'LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
      'PYTHONNOUSERSITE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
_IMAGE_ENV={'PATH','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256','PYTHON_PIP_VERSION',
            'PYTHON_SETUPTOOLS_VERSION','PYTHON_GET_PIP_URL','PYTHON_GET_PIP_SHA256'}
_KEYS='[{{range $i,$v := .Config.Env}}{{if $i}},{{end}}{{json (index (split $v "=") 0)}}{{end}}]'
VERSION='{"server":{{json .Server.Version}},"api":{{json .Server.APIVersion}},"client":{{json .Client.Version}}}'
INFO='{"id":{{json .ID}},"version":{{json .ServerVersion}},"swarm_manager":{{json .Swarm.ControlAvailable}},"os":{{json .OSType}},"architecture":{{json .Architecture}},"security":{{json .SecurityOptions}},"cgroup":{{json .CgroupVersion}},"memory":{{json .MemoryLimit}},"swap":{{json .SwapLimit}},"cpu":{{json .CpuCfsQuota}},"pids":{{json .PidsLimit}}}'
IMAGE='{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"digests":{{json .RepoDigests}},"volumes":{{if .Config.Volumes}}true{{else}}false{{end}},"env_keys":'+_KEYS+'}'
VOLUME='{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},"options":{{json .Options}},"labels":{{json .Labels}}}'
_HOST='{'+','.join('"'+k+'":{{json .HostConfig.'+k+'}}' for k in ('ReadonlyRootfs','NetworkMode','Privileged',
    'CapDrop','SecurityOpt','Memory','MemorySwap','NanoCpus','PidsLimit','IpcMode','PidMode','CgroupnsMode',
    'PublishAllPorts','Tmpfs'))+',"RestartPolicy":{"Name":{{json .HostConfig.RestartPolicy.Name}}},"LogConfig":{"Type":{{json .HostConfig.LogConfig.Type}}},'+','.join('"'+k+'":{{if .HostConfig.'+k+'}}true{{else}}false{{end}}'
    for k in ('Binds','Devices','DeviceRequests','VolumesFrom','PortBindings'))+'}'
_STATE='{'+','.join('"'+k+'":{{json .State.'+k+'}}' for k in ('Status','Running','Pid','ExitCode','OOMKilled'))+'}'
CONTAINER='{"id":{{json .Id}},"image":{{json .Image}},"labels":{{json .Config.Labels}},"user":{{json .Config.User}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"workdir":{{json .Config.WorkingDir}},"env_keys":'+_KEYS+',"state":'+_STATE+',"restart_count":{{json .RestartCount}},"host":'+_HOST+',"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"type":{{json $m.Type}},"name":{{json $m.Name}},"destination":{{json $m.Destination}},"rw":{{json $m.RW}}}{{end}}]}'


class DockerError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)


def _need(ok,code):
    if not ok:raise DockerError(code)


def _digest(value):return sha256(encode_bounded(value,4*CAP)).hexdigest()


def _path(value):
    _need(type(value) is str and 0<len(value.encode())<=4096 and Path(value).is_absolute()
        and not value.startswith('//') and not any(ord(c)<32 or ord(c)==127 for c in value)
        and str(Path(value))==value and '..' not in Path(value).parts,'docker_config_path')
    return Path(value)


def validate_config(value):
    fields={'schema','backend_id','docker_path','docker_sha256','endpoint','daemon_id',
            'engine_version','image_ref','image_id','architecture','control_root','snapshot_root','expires_at','max_jobs'}
    _need(type(value) is dict and set(value)==fields and value['schema']=='macr.docker.backend.v1','docker_config')
    for field in ('control_root','snapshot_root','docker_path'):_path(value[field])
    _need(Path(value['docker_path']).name=='docker','docker_config_binary')
    _need(type(value['endpoint']) is str and value['endpoint'].startswith('unix://'),'docker_endpoint')
    _path(value['endpoint'][7:])
    _need(type(value['backend_id']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',value['backend_id']) is not None,'docker_config')
    _need(type(value['docker_sha256']) is str and _HEX.fullmatch(value['docker_sha256']) is not None,'docker_config')
    _need(type(value['daemon_id']) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',value['daemon_id']) is not None,'docker_config')
    # Exact source-qualified CLI/daemon spelling is used for absence evidence.
    # Another engine version needs qualification, never an automatic fallback.
    _need(value['engine_version']=='29.7.2','docker_version_unsupported')
    _need(type(value['image_id']) is str and re.fullmatch('sha256:[0-9a-f]{64}',value['image_id']) is not None,'docker_config')
    _need(type(value['image_ref']) is str and re.fullmatch('[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}',value['image_ref']) is not None,'docker_config')
    _need(value['architecture'] in ('arm64','amd64'),'docker_config')
    _need(type(value['expires_at']) is int and 0<value['expires_at']<=4102444800
        and type(value['max_jobs']) is int and 1<=value['max_jobs']<=64,'docker_config')
    a,b=Path(value['control_root']),Path(value['snapshot_root'])
    _need(not(a==b or a.is_relative_to(b) or b.is_relative_to(a)),'docker_root_overlap')
    for p in (a,b):validate_workspace_path(p,output=True)
    return deepcopy(value)


class DockerBackend:
    def __init__(self,config,*,enabled=False,runner=None,clock=time.monotonic):
        self.config=validate_config(config);self.enabled=enabled is True
        self.root=Path(self.config['control_root']);self.anchors={};self.ready=False
        self.clock=clock;self.runner=runner;self.policy=_digest(self.config)
        self.active=None;self.cancel=threading.Event();self.receipts={};self.states={}
        self.last_error=None;self._host=None;self._image_keys=set();self._mutex=threading.RLock()
        self._lease_fd=None

    def _anchor(self):
        _need(self._lease_fd is not None,'docker_control_unlocked')
        with open_directory(self.root,identities=self.anchors) as directory:
            named=os.stat('owner.lock',dir_fd=directory,follow_symlinks=False)
            held=os.fstat(self._lease_fd)
        _need(stat.S_ISREG(named.st_mode) and named.st_nlink==1 and named.st_uid==os.geteuid()
            and (named.st_dev,named.st_ino)==(held.st_dev,held.st_ino),'docker_control_lock_changed')

    def _read(self,path,limit=CAP):return load_json(read_safe(self.root,path,limit).decode(),max_bytes=limit,max_depth=32)

    def _save(self,path,value):
        self._anchor()
        safe_write(self.root,path,encode_bounded(value,CAP),limit=CAP)

    @contextmanager
    def _lock(self):
        with open_directory(self.root,create=True,identities=self.anchors,observed=self.anchors) as directory:
            info=os.fstat(directory)
            _need(info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700,'docker_control_private')
            identity=[info.st_dev,info.st_ino]
        with safe_parent(self.root,'owner.lock',identities=self.anchors) as (directory,leaf):
            fd=os.open(leaf,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            _need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.geteuid()
                and stat.S_IMODE(info.st_mode)==0o600,'docker_control_lock')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise DockerError('docker_busy') from None
            self._lease_fd=fd
            lock_identity=[info.st_dev,info.st_ino]
            try:meta=self._read('policy.json')
            except FileNotFoundError:
                with open_directory(self.root,identities=self.anchors) as directory:
                    _need(set(os.listdir(directory))=={'owner.lock'},'docker_control_unowned')
                meta={'schema':'macr.docker.control.v1','policy':self.policy,'root_identity':identity,'lock_identity':lock_identity}
                self._save('policy.json',meta)
                for path in ('jobs','client'):
                    with safe_parent(self.root,path,identities=self.anchors) as (directory,leaf):os.mkdir(leaf,0o700,dir_fd=directory)
                safe_write(self.root/'client','config.json',b'{}',limit=2)
            _need(meta=={'schema':'macr.docker.control.v1','policy':self.policy,'root_identity':identity,'lock_identity':lock_identity},'docker_control_changed')
            _need(read_safe(self.root/'client','config.json',2)==b'{}','docker_client_config')
            with open_directory(self.root/'client',identities=self.anchors) as directory:
                _need(set(os.listdir(directory))=={'config.json'},'docker_client_config')
            with open_directory(self.root/'jobs',identities=self.anchors) as directory:
                names=os.listdir(directory)
                _need(len(names)<=self.config['max_jobs'] and all(re.fullmatch('[0-9a-f]{64}',n) for n in names),'docker_job_inventory')
            for name in names:
                old=self._read('jobs/'+name+'/state.json')
                _need(old.get('closed') is True and old.get('state') in ('completed','stopped'),'docker_previous_outcome_unknown')
            yield
        finally:
            if self._lease_fd==fd:self._lease_fd=None
            os.close(fd)

    def _host_identity(self,deadline,*,full=False):
        binary=Path(self.config['docker_path']);sock=Path(self.config['endpoint'][7:])
        # Explicit approved paths only; no context, PID, process or env discovery.
        with open_directory(binary.parent) as parent:
            fd=os.open(binary.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            try:
                before=os.fstat(fd)
                _need(stat.S_ISREG(before.st_mode) and 0<before.st_size<=128*CAP,'docker_binary')
                if full:
                    h=sha256();total=0
                    while True:
                        _need(self.clock()<deadline,'docker_deadline')
                        data=os.read(fd,65536)
                        if not data:break
                        total+=len(data);_need(total<=128*CAP,'docker_binary');h.update(data)
                    _need(h.hexdigest()==self.config['docker_sha256'],'docker_binary_identity')
                after=os.fstat(fd)
                _need((before.st_dev,before.st_ino,before.st_mtime_ns,before.st_size)==
                    (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_size),'docker_binary_identity')
            finally:os.close(fd)
        with open_directory(sock.parent) as parent:info=os.stat(sock.name,dir_fd=parent,follow_symlinks=False)
        _need(stat.S_ISSOCK(info.st_mode) and info.st_uid in (0,os.geteuid())
            and not stat.S_IMODE(info.st_mode)&0o002,'docker_socket_identity')
        return [before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,info.st_dev,info.st_ino,info.st_uid]

    def _call(self,args,deadline,*,input_bytes=b'',limit=65536,cancel=False,closing=False):
        self._anchor()
        _need(self.enabled and self.clock()<deadline and (closing or time.time()<self.config['expires_at']),'docker_not_admitted')
        if closing:
            _need(args[0]=='info' or args[:2] in (['container','inspect'],['container','kill'],['container','rm'],
                ['volume','inspect'],['volume','rm']),'docker_cleanup_scope')
        _need(self._host_identity(deadline)==self._host,'docker_host_changed')
        if self.runner is None:
            from .docker_process import NativeDockerRunner
            self.runner=NativeDockerRunner(Path(self.config['docker_path']),self.config['endpoint'],self.root/'client')
        return self.runner(args,input_bytes=input_bytes,deadline=min(deadline,self.clock()+10) if args[:2]!=['container','start'] else deadline,
            output_limit=limit,cancelled=self.cancel if cancel else None)

    def _json(self,args,deadline,*,closing=False):
        response=self._call(args,deadline,closing=closing)
        _need(response.status=='completed' and response.returncode==0,'docker_metadata_unavailable')
        try:
            value=load_json(response.stdout.decode(),max_bytes=65536,max_depth=24)
            _need(type(value) is dict,'docker_metadata_invalid')
            return value
        except (ValueError,ContractError,UnicodeError):raise DockerError('docker_metadata_invalid') from None

    def _daemon(self,deadline,*,closing=False):
        value=self._json(['info','--format',INFO],deadline,closing=closing)
        _need(value.get('id')==self.config['daemon_id'] and value.get('os')=='linux'
            and value.get('version')==self.config['engine_version'] and value.get('swarm_manager') is False
            and {'aarch64':'arm64','x86_64':'amd64'}.get(value.get('architecture'),value.get('architecture'))==self.config['architecture']
            and str(value.get('cgroup'))=='2' and all(value.get(k) is True for k in ('memory','swap','cpu','pids'))
            and any('name=seccomp' in s and 'unconfined' not in s for s in value.get('security',[])),'docker_engine_identity')

    def qualify(self):
        self.ready=False
        if not self.enabled:self.last_error='docker_disabled';return False
        if time.time()>=self.config['expires_at']:self.last_error='docker_expired';return False
        try:
            deadline=self.clock()+20
            with self._lock():
                self._host=self._host_identity(deadline,full=True)
                version=self._json(['version','--format',VERSION],deadline)
                api=version.get('api','').split('.')
                _need(version.get('server')==self.config['engine_version'] and version.get('client')=='29.7.2' and len(api)==2
                    and all(x.isdigit() for x in api) and tuple(map(int,api))>=(1,45),'docker_version')
                self._daemon(deadline)
                image=self._json(['image','inspect','--format',IMAGE,self.config['image_id']],deadline)
                normalize=lambda s:s.removeprefix('docker.io/library/').removeprefix('docker.io/')
                _need(image.get('id')==self.config['image_id'] and image.get('os')=='linux'
                    and image.get('architecture')==self.config['architecture'] and not image.get('volumes')
                    and normalize(self.config['image_ref']) in [normalize(s) for s in image.get('digests') or []]
                    and type(image.get('env_keys')) is list and set(image['env_keys'])<=_IMAGE_ENV,'docker_image_identity')
                self._image_keys=set(image['env_keys'])
                self._save('qualification.json',{'schema':'macr.docker.qualification.v1','policy':self.policy,
                    'metadata_qualified_at':time.time(),'isolation_observed':False,'origin':'local_unattested'})
            self.ready=True;self.last_error=None;return True
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
            self.last_error=error.code if isinstance(error,DockerError) else 'docker_qualification_failed';return False

    def capabilities(self):
        allowed=self.enabled and self.ready and time.time()<self.config['expires_at']
        return BackendCapabilities(**{k:allowed for k in ('network_disabled','readonly_input','isolated_writes',
            'secrets_absent','cpu_limit','memory_limit','pid_limit','wall_limit','output_limit','process_tree_stop')},
            architecture=self.config['architecture'],environment_ref=self.policy)

    def _inputs(self,snapshot,spec):
        from macr.investigation import repair_loop as loop
        snapshot=decode_record(SnapshotRef,asdict(snapshot));spec=decode_record(CheckSpec,asdict(spec))
        limits=spec.limits
        _need(spec.purpose=='test' and spec.argv_profile in ('unittest','pytest') and not spec.input_artifacts
            and spec.execution_profile==PROFILE and limits is not None and 0<limits.cpu<=1
            and 16*CAP<=limits.memory_bytes<=512*CAP and 1<=limits.pids<=32
            and 15<=limits.wall_seconds<=60 and 1024<=limits.output_bytes<=CAP,'docker_check_unsupported')
        root=Path(snapshot.root_ref)
        _need(root.is_relative_to(Path(self.config['snapshot_root'])),'docker_snapshot_scope')
        store=SnapshotStore(root.parent.parent,allowed_root=Path(self.config['snapshot_root']),read_only=True)
        manifest=store._manifest(snapshot)
        _need(1<=len(manifest.files)<=32 and sum(f['size_bytes'] for f in manifest.files)<=MAX_INPUT,'docker_snapshot_limit')
        files={f['path']:store.read_verified(snapshot,f['path']) for f in manifest.files}
        _need(all(not any(p.startswith('._macr_') for p in Path(n).parts) for n in files),'docker_reserved_path')
        if spec.argv_profile=='unittest':
            _need(1<=len(spec.selectors)<=8 and all(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*(?:\\.[A-Za-z_][A-Za-z0-9_]*)*',s) for s in spec.selectors),'docker_selectors')
            paths=[s.replace('.','/')+'.py' for s in spec.selectors]
            names=loop.test_manifest(files,paths,full_originals=True)
        else:
            paths=sorted({s.split('::')[0] for s in spec.selectors})
            names=loop.test_manifest(files,paths,test_plan={'schema':'macr.repair.test-plan.v2','framework':'pytest','nodeids':spec.selectors},full_originals=True)
        return snapshot,spec,files,names,loop.source_id(files,full_originals=True)

    def _labels(self,job,role):return {'macr.owner':job['owner'],'macr.job':job['key'],'macr.role':role}

    def _create_args(self,job,role):
        limits=job['spec']['limits'];name=job[role+'_name']
        args=['container','create','--name',name,'--pull','never','--network','none','--read-only',
            '--user','65534:65534','--cap-drop','ALL','--security-opt','no-new-privileges=true',
            '--security-opt','seccomp=builtin','--ipc','none','--cgroupns','private','--no-healthcheck',
            '--log-driver','none','--restart','no','--stop-signal','SIGKILL',
            '--memory',str(limits['memory_bytes']),'--memory-swap',str(limits['memory_bytes']),
            '--cpus',str(limits['cpu']),'--pids-limit',str(limits['pids']),
            '--ulimit','core=0:0','--ulimit','nofile=256:256',
            '--workdir','/work','--tmpfs','/work:rw,nosuid,nodev,size=67108864,mode=1777',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=33554432,mode=1777',
            '--mount','type=volume,src='+job['volume']+',dst=/input,volume-nocopy'+(',readonly' if role=='target' else '')]
        for k,v in self._labels(job,role).items():args+=['--label',k+'='+v]
        for k in sorted(self._image_keys|set(_ENV)):args+=['--env',k+'='+_ENV.get(k,'')]
        args+=['--entrypoint','/usr/local/bin/python',self.config['image_id']]
        args+=(['-I','-B','/input/._macr_runner.py','/input/._macr_job.json'] if role=='target' else
            ['-I','-S','-B','/dev/null'])
        return args

    def _record(self,job):self._save('jobs/'+job['key']+'/state.json',job)

    def _mutate(self,job,action,args,deadline,*,allowed_codes=(0,),**kw):
        job['phase']=action;job['actions'].append({'action':action,'status':'intent'});self._record(job)
        response=self._call(args,deadline,**kw)
        job['actions'][-1]['status']=response.status;job['actions'][-1]['returncode']=response.returncode;self._record(job)
        if response.status!='completed' or response.returncode not in allowed_codes:
            raise DockerError('docker_mutation_unknown')
        return response

    def _volume(self,job,deadline,*,closing=False):
        value=self._json(['volume','inspect','--format',VOLUME,job['volume']],deadline,closing=closing)
        _need(value.get('name')==job['volume'] and value.get('driver')=='local' and value.get('scope')=='local'
            and not value.get('options') and value.get('labels')==self._labels(job,'input'),'docker_volume_identity')

    def _container(self,job,role,deadline,*,unstarted=False,closing=False):
        name=job.get(role+'_id') or job[role+'_name']
        value=self._json(['container','inspect','--format',CONTAINER,name],deadline,closing=closing)
        cid=value.get('id');_need(type(cid) is str and _ID.fullmatch(cid) is not None,'docker_container_identity')
        _need((not job.get(role+'_id') or job[role+'_id']==cid) and value.get('image')==self.config['image_id']
            and value.get('labels')==self._labels(job,role),'docker_container_identity')
        job[role+'_id']=cid
        if unstarted:
            host=value.get('host',{});limits=job['spec']['limits'];mounts=value.get('mounts',[])
            _need(value.get('user')=='65534:65534' and value.get('entrypoint')==['/usr/local/bin/python']
                and value.get('cmd')==self._create_args(job,role)[self._create_args(job,role).index(self.config['image_id'])+1:]
                and value.get('workdir')=='/work' and set(value.get('env_keys') or [])==self._image_keys|set(_ENV)
                and value.get('restart_count')==0 and value.get('state',{}).get('Status')=='created'
                and value['state'].get('Running') is False and value['state'].get('Pid')==0
                and host.get('ReadonlyRootfs') is True and host.get('NetworkMode')=='none' and not host.get('Privileged')
                and host.get('CapDrop')==['ALL'] and set(host.get('SecurityOpt') or [])=={'no-new-privileges=true','seccomp=builtin'}
                and host.get('Memory')==limits['memory_bytes'] and host.get('MemorySwap')==limits['memory_bytes']
                and host.get('NanoCpus')==int(limits['cpu']*1_000_000_000) and host.get('PidsLimit')==limits['pids']
                and host.get('IpcMode')=='none' and not host.get('PidMode') and host.get('CgroupnsMode')=='private'
                and not host.get('Binds') and not host.get('Devices') and not host.get('DeviceRequests')
                and not host.get('VolumesFrom') and not host.get('PortBindings') and not host.get('PublishAllPorts')
                and host.get('RestartPolicy',{}).get('Name')=='no'
                and host.get('LogConfig',{}).get('Type')=='none'
                and host.get('Tmpfs')=={'/work':'rw,nosuid,nodev,size=67108864,mode=1777','/tmp':'rw,nosuid,nodev,size=33554432,mode=1777'}
                and [m for m in mounts if m.get('type')!='tmpfs']==[{'type':'volume','name':job['volume'],'destination':'/input','rw':role=='stager'}]
                and all(m.get('destination') in ('/work','/tmp') for m in mounts if m.get('type')=='tmpfs'),'docker_policy_mismatch')
        self._record(job)
        return value

    def _absent(self,kind,name,deadline,*,closing=False):
        _need(kind=='container' and _ID.fullmatch(name) is not None or kind=='volume'
            and re.fullmatch('macr-[a-zA-Z0-9_-]+',name) is not None,'docker_resource_identity')
        response=self._call([kind,'inspect','--format','{{.Id}}' if kind=='container' else '{{.Name}}',name],deadline,closing=closing)
        # Versioned Docker29.7.2 explicit inspect errors. Custom formatting emits
        # one newline even for zero objects; generic inspect is never accepted.
        expected=('Error response from daemon: No such container: '+name+'\n' if kind=='container' else
            'Error response from daemon: get '+name+': no such volume\n').encode()
        return response.status=='completed' and response.returncode==1 and response.stdout==b'\n' and response.stderr==expected

    def _cleanup(self,job,deadline):
        self._daemon(deadline,closing=True)
        for role in ('target','stager'):
            if not job.get(role+'_intent') or job.get(role+'_removed'):continue
            value=self._container(job,role,deadline,closing=True)
            if value.get('state',{}).get('Running') is True:
                self._mutate(job,'kill_'+role,['container','kill','--signal','KILL',job[role+'_id']],deadline,closing=True)
                value=self._container(job,role,deadline,closing=True)
            _need(value.get('state',{}).get('Running') is False and value.get('state',{}).get('Pid')==0,'docker_exit_unknown')
            self._mutate(job,'remove_'+role,['container','rm',job[role+'_id']],deadline,closing=True)
            _need(self._absent('container',job[role+'_id'],deadline,closing=True),'docker_removal_unknown')
            job[role+'_removed']=True;self._record(job)
        if job.get('volume_intent') and not job.get('volume_removed'):
            self._volume(job,deadline,closing=True)
            self._mutate(job,'remove_volume',['volume','rm',job['volume']],deadline,closing=True)
            _need(self._absent('volume',job['volume'],deadline,closing=True),'docker_removal_unknown')
            job['volume_removed']=True;self._record(job)

    def _archive(self,files,wire):
        # Host-generated regular files only; no repository tar or extractall.
        runner=Path(__file__).with_name('python_check.py').read_bytes()
        values=files|{'._macr_runner.py':runner,'._macr_job.json':encode_bounded(wire,65536)}
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,data in sorted(values.items()):
                entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o444;entry.uid=entry.gid=0;entry.mtime=0
                archive.addfile(entry,io.BytesIO(data))
        _need(output.tell()<=20*CAP,'docker_archive_limit')
        return output.getvalue()

    def run(self,check,snapshot):
        result=classify_check({'execution_status':'blocked'},check)
        if not self.capabilities().network_disabled:return result
        job=None;claimed=False
        try:
            wall=getattr(check.limits,'wall_seconds',None)
            _need(type(wall) in (int,float) and 15<=wall<=60,'docker_check_unsupported')
            start=self.clock();deadline=start+wall;work_deadline=deadline-10
            snapshot,check,files,names,source_id=self._inputs(snapshot,check)
            key=_digest({'policy':self.policy,'snapshot':asdict(snapshot),'spec':asdict(check)})
            with self._lock():
                self._daemon(work_deadline)
                with open_directory(self.root/'jobs',identities=self.anchors) as directory:
                    _need(len(os.listdir(directory))<self.config['max_jobs'],'docker_job_limit')
                owner=uuid4().hex
                job={'schema':'macr.docker.job.v1','key':key,'owner':owner,'policy':self.policy,
                    'snapshot':asdict(snapshot),'spec':asdict(check),'source_id':source_id,'test_names':names,
                    'volume':'macr-'+owner+'-input','stager_name':'macr-'+owner+'-stage','target_name':'macr-'+owner+'-test',
                    'actions':[],'phase':'claimed','closed':False,'state':'running'}
                wire={'schema':'macr.docker.public-job.v1','nonce':owner,'source_id':source_id,
                    'argv_profile':check.argv_profile,'selectors':check.selectors,'test_names':names,
                    'files':{p:sha256(b).hexdigest() for p,b in files.items()},
                    'limits':{k:v for k,v in asdict(check.limits).items() if k!='schema_version'}}
                from .python_check import parse_job
                parse_job(encode_bounded(wire,65536))
                archive=self._archive(files,wire)
                job['input_archive_sha256']=sha256(archive).hexdigest()
                _need(self.clock()<work_deadline,'docker_deadline')
                with safe_parent(self.root/'jobs',key,identities=self.anchors) as (directory,leaf):
                    try:os.mkdir(leaf,0o700,dir_fd=directory)
                    except FileExistsError:raise DockerError('docker_no_replay') from None
                claimed=True;self._record(job)
                with self._mutex:self.active=job;self.cancel.clear();self.states.setdefault(check.check_id,[]).append(key)
                status='outcome_unknown';outcome=None
                try:
                    job['volume_intent']=True;self._record(job)
                    args=['volume','create','--driver','local']
                    for k,v in self._labels(job,'input').items():args+=['--label',k+'='+v]
                    self._mutate(job,'create_volume',args+[job['volume']],work_deadline);self._volume(job,work_deadline)
                    for role in ('stager','target'):
                        job[role+'_intent']=True;self._record(job)
                        response=self._mutate(job,'create_'+role,self._create_args(job,role),work_deadline)
                        cid=response.stdout.strip().decode('ascii');_need(_ID.fullmatch(cid) is not None,'docker_container_identity')
                        job[role+'_id']=cid;self._container(job,role,work_deadline,unstarted=True)
                        if role=='stager':
                            self._mutate(job,'stage_input',['container','cp','-',cid+':/input'],work_deadline,input_bytes=archive)
                            self._container(job,role,work_deadline,unstarted=True)
                            self._mutate(job,'remove_stager',['container','rm',cid],work_deadline)
                            _need(self._absent('container',cid,work_deadline),'docker_removal_unknown')
                            job['stager_removed']=True;self._record(job)
                    response=self._mutate(job,'start_target',['container','start','--attach',job['target_id']],
                        work_deadline,allowed_codes=(0,1,2),limit=check.limits.output_bytes,cancel=True)
                    observed=self._container(job,'target',work_deadline,closing=True)
                    _need(observed.get('state',{}).get('Running') is False and observed['state'].get('Pid')==0
                        and observed['state'].get('ExitCode') in (0,1,2),'docker_exit_unknown')
                    from .python_check import parse_result
                    outcome=parse_result(response.stdout,wire)
                    if outcome['status']=='completed':
                        failed=sum(v=='failed' for v in outcome['outcomes'].values())
                        _need(observed['state']['ExitCode']==int(bool(failed)),'docker_exit_contradiction')
                        status='completed'
                    else:status='resource_limit' if outcome['status']=='resource_limit' else 'error'
                except (ValueError,OSError,TypeError,KeyError,AttributeError) as error:
                    job['failure']=getattr(error,'code',getattr(error,'reason','docker_operation_failed'))
                finally:
                    try:self._cleanup(job,deadline);job['closed']=True
                    except (ValueError,OSError,TypeError,KeyError,AttributeError):job['cleanup_failure']='docker_cleanup_unknown';status='outcome_unknown'
                if status=='completed':
                    failed=sum(v=='failed' for v in outcome['outcomes'].values())
                    counts={'passed':len(names)-failed,'failed':failed,'executed':len(names),'collected':len(names)}
                    result=classify_check({'execution_status':'completed','exit_code':int(bool(failed)),
                        'test_counts':counts,'environment_ref':self.policy},check)
                    job['framework_errors']=outcome['framework_errors']
                else:result=classify_check({'execution_status':status,'environment_ref':self.policy},check)
                job.update(state='completed' if status=='completed' else 'outcome_unknown' if status=='outcome_unknown' else 'stopped',result=asdict(result))
                self._record(job)
                if status=='completed':
                    self.receipts[key]={'source_id':source_id,'execution_status':'completed',
                        'outcomes':outcome['outcomes'],'diagnostics':[]}
                if not job['closed'] or status=='outcome_unknown':self.ready=False
                return result
        except (ValueError,OSError,TypeError,KeyError,AttributeError) as error:
            self.last_error=error.code if isinstance(error,DockerError) else 'docker_operation_failed'
            if claimed:
                self.ready=False
                return classify_check({'execution_status':'outcome_unknown'},check)
            return result
        finally:
            with self._mutex:
                if claimed and self.active is job:self.active=None

    def public_result(self,request):
        key=_digest({'policy':self.policy,'snapshot':request['snapshot'],'spec':request['spec']})
        value=self.receipts.get(key)
        _need(request.get('phase')=='public' and request.get('backend',{}).get('backend_id')==self.config['backend_id']
            and request['backend'].get('execution_profile')==PROFILE and value is not None
            and request.get('source_id')==value['source_id'] and sorted(value['outcomes'])==request.get('test_names'),'docker_public_result')
        return {'schema':'macr.repair.public-check-receipt.v1','phase':'public',
            'request_sha256':_digest(request),'observation':deepcopy(value)}

    def inspect(self,check_id):
        with self._mutex:
            if self.active and self.active['spec']['check_id']==check_id:return 'running'
            keys=self.states.get(check_id,[])
            if len(keys)!=1:return 'outcome_unknown'
            try:return self._read('jobs/'+keys[0]+'/state.json')['state']
            except (OSError,ValueError,ContractError):return 'outcome_unknown'

    def stop(self,check_id):
        with self._mutex:
            if self.active and self.active['spec']['check_id']==check_id:
                self.cancel.set();return 'running'  # closure must still be observed by run()
        return self.inspect(check_id)


def configured_verifier(path,*,enabled=False):
    from macr.investigation.repair_entry import _json
    from macr.investigation.repair_verification import BackendVerifier
    backend=DockerBackend(_json(path),enabled=enabled)
    if not backend.qualify():raise DockerError(backend.last_error or 'docker_qualification_failed')
    return BackendVerifier(backend,backend.public_result,backend_id=backend.config['backend_id'],execution_profile=PROFILE)
