"""Self-contained public-test collector for an explicitly authorized container.

This is NOT a host executor or an authenticated scorer. Imported target Python
can interfere with its framework/process; results remain backend-reported.
"""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
import errno
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

MAX_JOB=131072
MAX_SOURCE=16*1024*1024
_JSON=json.dumps
_REASONS={'job_invalid','source_mismatch','isolation_probe_failed','collection_mismatch',
          'unsupported_test_outcome','framework_error','output_limit','collector_invalid'}


class ContractFailure(ValueError):
    def __init__(self,reason):
        self.reason=reason if reason in _REASONS else 'framework_error'
        super().__init__(self.reason)


class OutputLimit(RuntimeError):pass


def _need(ok,reason='job_invalid'):
    if not ok:raise ContractFailure(reason)


def _canonical(value):
    return _JSON(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def _path(value):
    _need(type(value) is str and 0<len(value.encode())<=512 and '\\' not in value
        and not any(ord(c)<32 or ord(c)==127 for c in value))
    path=PurePosixPath(value)
    _need(not path.is_absolute() and str(path)==value and all(p not in ('.','..')
        and not p.startswith('._macr_') for p in path.parts))
    return path


def _unit_id(value):
    parts=value.split('::')
    _need(len(parts)==2 and str(_path(parts[0])).endswith('.py'))
    module=parts[0][:-3].replace('/','.')
    _need(all(p.isidentifier() for p in module.split('.')) and len(parts[1].split('.'))==2
        and all(p.isidentifier() for p in parts[1].split('.')))
    return module+'.'+parts[1]


def validate_job(value):
    _need(type(value) is dict and set(value)=={'schema','nonce','source_id','argv_profile','selectors','test_names','files','limits'}
        and value['schema']=='macr.docker.public-job.v1')
    _need(type(value['nonce']) is str and re.fullmatch('[0-9a-f]{32}',value['nonce']) is not None
        and type(value['source_id']) is str and re.fullmatch('[0-9a-f]{64}',value['source_id']) is not None
        and value['argv_profile'] in ('unittest','pytest'))
    files=value['files'];names=value['test_names'];selectors=value['selectors'];limits=value['limits']
    _need(type(files) is dict and 1<=len(files)<=32)
    for path,digest in files.items():
        _path(path);_need(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest) is not None)
    _need(sha256(_canonical(files)).hexdigest()==value['source_id'])
    _need(type(names) is list and 1<=len(names)<=32 and all(type(n) is str and 0<len(n.encode())<=2048 for n in names)
        and names==sorted(set(names)) and type(selectors) is list and 1<=len(selectors)<=32
        and all(type(n) is str and 0<len(n.encode())<=2048 and not n.startswith('-')
            and not any(c in n for c in '\x00\n\r;') for n in selectors) and len(selectors)==len(set(selectors)))
    if value['argv_profile']=='unittest':
        _need(all(all(p.isidentifier() for p in s.split('.')) for s in selectors))
        for name in names:
            _unit_id(name);_need(name.split('::')[0] in files
                and name.split('::')[0][:-3].replace('/','.') in selectors)
    else:
        _need(sorted(selectors)==names)
        for name in names:
            base,bracket,suffix=name.partition('[');parts=base.split('::')
            _need(len(parts) in (2,3) and str(_path(parts[0])).endswith('.py') and parts[0] in files
                and all(p.isidentifier() for p in parts[1:]) and (not bracket or suffix.endswith(']') and '[' not in suffix))
    _need(type(limits) is dict and set(limits)=={'cpu','memory_bytes','pids','wall_seconds','output_bytes'})
    for key,low,high in (('cpu',0,4),('wall_seconds',0,300)):
        _need(type(limits[key]) in (int,float) and math.isfinite(limits[key]) and low<limits[key]<=high)
    for key,low,high in (('memory_bytes',16*1024*1024,2*1024*1024*1024),('pids',1,64),('output_bytes',1024,1024*1024)):
        _need(type(limits[key]) is int and low<=limits[key]<=high)
    envelope={'schema':'macr.docker.public-result.v1','nonce':value['nonce'],'source_id':value['source_id'],
        'argv_profile':value['argv_profile'],'test_names':names,'outcomes':dict.fromkeys(names,'failed'),
        'status':'completed','probes':{'passed':True},'framework_errors':32,'reason':'unsupported_test_outcome'}
    _need(len(_canonical(envelope))+1<=limits['output_bytes'])
    return value


def parse_job(raw):
    def pairs(items):
        result={}
        for key,value in items:
            _need(key not in result);result[key]=value
        return result
    try:
        _need(type(raw) is bytes and 0<len(raw)<=MAX_JOB)
        return validate_job(json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ContractFailure('job_invalid'))))
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise ContractFailure('job_invalid') from None


def verify_sources(job,root=Path('/input')):
    """Only the captured regular bytes, with no imports, symlinks or extras."""
    try:
        root=Path(root);_need(not root.is_symlink(),'source_mismatch')
        entries=0;seen=set();total=0
        for parent,dirs,files in os.walk(root,followlinks=False):
            for name in dirs+files:
                entries+=1;path=Path(parent)/name
                _need(entries<=128 and not path.is_symlink(),'source_mismatch')
            for name in files:
                path=Path(parent)/name;relative=path.relative_to(root).as_posix()
                if relative in ('._macr_runner.py','._macr_job.json'):continue
                _need(relative in job['files'],'source_mismatch');seen.add(relative)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as stream:
                    before=os.fstat(stream.fileno());_need(stat.S_ISREG(before.st_mode)
                        and before.st_nlink==1 and before.st_size<=MAX_SOURCE-total,'source_mismatch')
                    data=stream.read(MAX_SOURCE-total+1);after=os.fstat(stream.fileno())
                total+=len(data)
                _need(total<=MAX_SOURCE and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
                    (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
                    and sha256(data).hexdigest()==job['files'][relative],'source_mismatch')
        _need(seen==set(job['files']),'source_mismatch')
    except (OSError,ValueError,KeyError):
        raise ContractFailure('source_mismatch') from None


def _readonly(path,*,statvfs=os.statvfs,open_file=os.open):
    test=Path(path)/'._macr_probe_write'
    try:
        if not statvfs(path).f_flag & os.ST_RDONLY:return False
    except OSError:return False
    try:
        fd=open_file(test,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except OSError as error:return error.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
    else:
        os.close(fd);test.unlink();return False


def _writable(path):
    test=Path(path)/'._macr_probe_write'
    try:
        fd=os.open(test,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        os.write(fd,b'owned-probe');os.close(fd);test.unlink();return True
    except OSError:return False


def probe_isolation(job,*,read_text=lambda p:Path(p).read_text(),uid=os.geteuid,
                    readonly=_readonly,writable=_writable,net_interfaces=lambda:os.listdir('/sys/class/net')):
    """Effective container probes only, not host or producer attestation."""
    try:
        _need(uid()!=0,'isolation_probe_failed')
        fields={line.partition(':')[0]:line.partition(':')[2].strip() for line in read_text('/proc/self/status').splitlines()}
        _need(fields.get('NoNewPrivs')=='1' and fields.get('Seccomp')=='2'
            and int(fields.get('CapEff','1'),16)==0,'isolation_probe_failed')
        _need(set(net_interfaces())=={'lo'} and readonly('/input') and readonly('/')
            and writable('/work') and writable('/tmp'),'isolation_probe_failed')
        limits=job['limits']
        memory=int(read_text('/sys/fs/cgroup/memory.max').strip());pids=int(read_text('/sys/fs/cgroup/pids.max').strip())
        quota,period=map(int,read_text('/sys/fs/cgroup/cpu.max').split())
        _need(0<memory<=limits['memory_bytes'] and 0<pids<=limits['pids'] and period>0
            and 0<quota/period<=limits['cpu'],'isolation_probe_failed')
        return True
    except (OSError,ValueError,TypeError,KeyError):
        raise ContractFailure('isolation_probe_failed') from None


class BoundedSink(io.TextIOBase):
    def __init__(self,limit):self.limit,self.count,self.exceeded=limit,0,False
    def write(self,text):
        self.count+=len(text.encode('utf-8',errors='replace'))
        if self.count>self.limit:self.exceeded=True;raise OutputLimit()
        return len(text)
    def isatty(self):return False
    @property
    def encoding(self):return 'utf-8'


@contextmanager
def _capture(limit,root):
    sink=BoundedSink(limit);previous=os.getcwd();paths=list(sys.path)
    try:
        os.chdir(root);sys.path.insert(0,str(Path(root).absolute()))
        with redirect_stdout(sink),redirect_stderr(sink):yield
        if sink.exceeded:raise OutputLimit()
    finally:os.chdir(previous);sys.path[:]=paths


def collect_unittest(job,*,framework=None,root=Path('/input')):
    if framework is None:import unittest as framework
    expected={_unit_id(name):name for name in job['test_names']};outcomes={};started=set();invalid=[];errors=set()
    class Result(framework.TestResult):
        def startTest(self,test):
            name=test.id()
            if name not in expected or name in started:invalid.append('collection_mismatch')
            started.add(name);super().startTest(test)
        def _outcome(self,test,status):
            name=test.id()
            if name not in started or name not in expected:invalid.append('collection_mismatch');return
            if outcomes.get(expected[name])!='failed':outcomes[expected[name]]=status
        def addSuccess(self,test):self._outcome(test,'passed')
        def addFailure(self,test,err):self._outcome(test,'failed')
        def addError(self,test,err):errors.add(test.id());self._outcome(test,'failed')
        def addSkip(self,test,reason):invalid.append('unsupported_test_outcome')
        def addExpectedFailure(self,test,err):invalid.append('unsupported_test_outcome')
        def addUnexpectedSuccess(self,test):invalid.append('unsupported_test_outcome')
        def addSubTest(self,test,subtest,err):
            if err is not None:
                self._outcome(test,'failed')
                if not issubclass(err[0],getattr(test,'failureException',AssertionError)):errors.add(test.id())
    with _capture(job['limits']['output_bytes'],root):
        suite=framework.TestLoader().loadTestsFromNames(job['selectors'])
        collected=[];seen=set()
        def visit(item,depth=0):
            _need(depth<=32 and id(item) not in seen,'collection_mismatch');seen.add(id(item))
            if callable(getattr(item,'id',None)):
                collected.append(item.id());_need(len(collected)<=32,'collection_mismatch')
            else:
                for child in item:visit(child,depth+1)
        visit(suite)
        _need(sorted(collected)==sorted(expected) and len(set(collected))==len(collected),'collection_mismatch')
        result=Result();suite.run(result)
    _need(not invalid,invalid[0] if invalid else 'framework_error')
    _need(result.testsRun==len(expected) and started==set(expected) and set(outcomes)==set(job['test_names']),'collection_mismatch')
    return {'status':'completed','outcomes':outcomes,'framework_errors':len(errors)}


def collect_pytest(job,*,framework=None,root=Path('/input')):
    # Trusted image dependency is imported before the captured source is added.
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    if framework is None:import pytest as framework
    expected=set(job['test_names']);outcomes={};seen=set();invalid=[];errors=set()
    class Plugin:
        def pytest_collection_finish(self,session):
            ids=[item.nodeid for item in session.items]
            if len(ids)!=len(set(ids)) or set(ids)!=expected:
                invalid.append('collection_mismatch');raise framework.UsageError('public_collection_mismatch')
        def pytest_runtest_logreport(self,report):
            key=(report.nodeid,report.when)
            if report.nodeid not in expected or report.when not in ('setup','call','teardown') or key in seen:
                invalid.append('collection_mismatch');return
            seen.add(key)
            if getattr(report,'wasxfail',None) or report.outcome=='skipped':invalid.append('unsupported_test_outcome');return
            if report.outcome not in ('passed','failed'):invalid.append('unsupported_test_outcome');return
            if report.outcome=='failed':
                outcomes[report.nodeid]='failed'
                if report.when!='call':errors.add(report.nodeid)
            elif report.when=='call' and outcomes.get(report.nodeid)!='failed':outcomes[report.nodeid]='passed'
    args=['-q','-s','--disable-warnings','--tb=no','--color=no','-c','/dev/null','-o','addopts=',
          '-p','no:cacheprovider','--rootdir='+str(root),'--basetemp=/work/pytest']+[str(root).rstrip('/')+'/'+n for n in job['selectors']]
    with _capture(job['limits']['output_bytes'],root):code=int(framework.main(args,plugins=[Plugin()]))
    _need(not invalid,invalid[0] if invalid else 'framework_error')
    _need(code in (0,1) and set(outcomes)==expected and all((n,'setup') in seen and (n,'teardown') in seen for n in expected),'collection_mismatch')
    _need((code==0)==all(v=='passed' for v in outcomes.values()),'framework_error')
    return {'status':'completed','outcomes':outcomes,'framework_errors':len(errors)}


def execute(job,*,probe=probe_isolation,collector=None):
    result={'schema':'macr.docker.public-result.v1','nonce':job['nonce'],'source_id':job['source_id'],
        'argv_profile':job['argv_profile'],'test_names':list(job['test_names']),'outcomes':{},'status':'error',
        'probes':{'passed':False},'framework_errors':0,'reason':None}
    try:
        _need(probe(job) is True,'isolation_probe_failed');result['probes']['passed']=True
        value=(collector or (collect_unittest if job['argv_profile']=='unittest' else collect_pytest))(job)
        _need(type(value) is dict and set(value)=={'status','outcomes','framework_errors'}
            and value['status']=='completed' and type(value['outcomes']) is dict
            and set(value['outcomes'])==set(job['test_names']) and all(v in ('passed','failed') for v in value['outcomes'].values())
            and type(value['framework_errors']) is int and 0<=value['framework_errors']<=32,'collector_invalid')
        result.update(value)
    except OutputLimit:result.update(status='resource_limit',reason='output_limit')
    except ContractFailure as error:result['reason']=error.reason
    except BaseException:result['reason']='framework_error'
    return result


def parse_result(raw,job):
    """Host-side bounded decode; a legal record still is not an attestation."""
    def pairs(items):
        value={}
        for key,item in items:
            _need(key not in value,'collector_invalid');value[key]=item
        return value
    try:
        validate_job(job)
        _need(type(raw) is bytes and 0<len(raw)<=job['limits']['output_bytes'],'collector_invalid')
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ContractFailure('collector_invalid')))
        _need(type(value) is dict and set(value)=={'schema','nonce','source_id','argv_profile','test_names',
            'outcomes','status','probes','framework_errors','reason'}
            and value['schema']=='macr.docker.public-result.v1','collector_invalid')
        _need(all(value[key]==job[key] for key in ('nonce','source_id','argv_profile','test_names'))
            and type(value['probes']) is dict and set(value['probes'])=={'passed'}
            and type(value['probes']['passed']) is bool and type(value['outcomes']) is dict
            and type(value['framework_errors']) is int and 0<=value['framework_errors']<=len(job['test_names']),
            'collector_invalid')
        if value['status']=='completed':
            _need(value['probes']['passed'] and value['reason'] is None
                and set(value['outcomes'])==set(job['test_names'])
                and all(outcome in ('passed','failed') for outcome in value['outcomes'].values())
                and value['framework_errors']<=sum(x=='failed' for x in value['outcomes'].values()),'collector_invalid')
        else:
            _need(value['status'] in ('error','resource_limit') and value['outcomes']=={}
                and value['framework_errors']==0 and value['reason'] in _REASONS
                and (value['status']=='resource_limit')==(value['reason']=='output_limit'),'collector_invalid')
        return value
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise ContractFailure('collector_invalid') from None


def main(argv=None,*,output=None,probe=probe_isolation,collector=None,input_root=Path('/input')):
    argv=sys.argv[1:] if argv is None else argv;output=sys.stdout if output is None else output
    job=None
    try:
        _need(len(argv)==1 and Path(argv[0])==Path(input_root)/'._macr_job.json')
        with open(argv[0],'rb') as stream:job=parse_job(stream.read(MAX_JOB+1))
        verify_sources(job,Path(input_root))
        if collector is None:
            collector=lambda value:(collect_unittest if value['argv_profile']=='unittest' else collect_pytest)(value,root=Path(input_root))
        result=execute(job,probe=probe,collector=collector)
    except BaseException:
        if job is None:job={'nonce':'0'*32,'source_id':'0'*64,'argv_profile':'unittest','test_names':[],'limits':{'output_bytes':1024}}
        result={'schema':'macr.docker.public-result.v1','nonce':job['nonce'],'source_id':job['source_id'],
            'argv_profile':job['argv_profile'],'test_names':job['test_names'],'outcomes':{},'status':'error',
            'probes':{'passed':False},'framework_errors':0,'reason':'source_mismatch'}
    raw=_canonical(result)
    if len(raw)+1>job['limits']['output_bytes']:
        result.update(outcomes={},status='resource_limit',reason='output_limit');raw=_canonical(result)
    output.write(raw.decode()+'\n');output.flush()
    return 0 if result['status']=='completed' and all(v=='passed' for v in result['outcomes'].values()) else 1 if result['status']=='completed' else 2


if __name__=='__main__':raise SystemExit(main())
