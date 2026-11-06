"""Bounded Docker CLI I/O, not a daemon grant, policy check or closure witness.

The caller qualifies the binary, local socket and private empty client directory.
Only newly launched helper groups can be signalled here. Ending that helper says
nothing about a daemon-side container; its owner must inspect and clean it up.
The I/O deadline is local protection, not a preemptive OS-spawn guarantee;
helper termination waits can add at most .2 + .5 seconds after that deadline.
"""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    status: str
    started: bool


_COMMANDS={'container':{'create','inspect','cp','start','wait','stop','kill','rm'},
           'volume':{'create','inspect','rm'},'image':{'inspect'}}
_GLOBAL={'--host','--context','--config','--tls','--tlsverify','--tlscacert','--tlscert','--tlskey','-c'}


def _path(value):
    text=str(value)
    return (isinstance(value,Path) and value.is_absolute() and not text.startswith('//') and str(Path(text))==text
        and '..' not in value.parts and not any(c in text for c in '\x00\r\n'))


def _arguments(args):
    if (type(args) is not list or not 1<=len(args)<=256
            or any(type(a) is not str or not a or len(a.encode())>8192
                   or any(c in a for c in '\x00\r\n') for a in args)
            or sum(len(a.encode()) for a in args)>65536):return False
    if args[0] not in ('version','info') and not (
            len(args)>=2 and args[0] in _COMMANDS and args[1] in _COMMANDS[args[0]]):return False
    # Docker's persistent flags can otherwise override the qualified prefix even
    # after the command name. Container-policy flags remain the driver's job.
    return not any(a.split('=',1)[0] in _GLOBAL or a.startswith(('-H','-c')) for a in args)


def _close(pipes):
    complete=True
    for pipe in pipes:
        if pipe is not None:
            try:pipe.close()
            except BaseException:complete=False
    return complete


def _stop_owned_helper(process):
    """No saved PIDs, getpgid probes, leader-only signals or stale-group kills."""
    try:
        if process.poll() is not None:return True
        if type(process.pid) is not int or process.pid<=1:return False
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=.2);return True
        except subprocess.TimeoutExpired:pass
        if process.poll() is not None:return True
        os.killpg(process.pid,signal.SIGKILL)
        try:process.wait(timeout=.5);return True
        except subprocess.TimeoutExpired:return False
    except ProcessLookupError:
        try:return process.poll() is not None
        except BaseException:return False
    except BaseException:return False


class NativeDockerRunner:
    def __init__(self,docker_path:Path,endpoint:str,client_root:Path):
        if (not _path(docker_path) or not _path(client_root) or type(endpoint) is not str
                or not endpoint.startswith('unix:///') or len(endpoint.encode())>4096):
            raise ValueError('docker_runner_configuration')
        socket_path=Path(endpoint[7:])
        if not _path(socket_path) or endpoint!='unix://'+str(socket_path):
            raise ValueError('docker_runner_configuration')
        self._prefix=[str(docker_path),'--config',str(client_root),'--host',endpoint]
        self._root=str(client_root)
        self._env={'HOME':self._root,'DOCKER_CONFIG':self._root,'PATH':'/usr/bin:/bin',
                   'LANG':'C','DOCKER_CLI_HINTS':'false'}

    def __call__(self,args:list[str],*,input_bytes=b'',deadline:float,output_limit:int,
                 cancelled:threading.Event|None=None)->CommandResult:
        empty=lambda status:CommandResult(None,b'',b'',status,False)
        try:
            valid=(_arguments(args) and type(input_bytes) is bytes and len(input_bytes)<=20*1024*1024
                and type(output_limit) is int and 1<=output_limit<=1024*1024
                and type(deadline) in (int,float) and math.isfinite(deadline))
        except (ValueError,UnicodeError):valid=False
        if not valid:return empty('launch_failed')
        if cancelled is not None and cancelled.is_set():return empty('cancelled')
        if time.monotonic()>=deadline:return empty('timeout')
        try:
            process=subprocess.Popen(self._prefix+list(args),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,shell=False,close_fds=True,start_new_session=True,bufsize=0,
                cwd=self._root,env=dict(self._env))
        except (OSError,ValueError):return empty('launch_failed')
        except BaseException:
            # Without a returned handle a launch interruption cannot prove that
            # no helper was created. Never signal a guessed PID or permit retry.
            return CommandResult(None,b'',b'','outcome_unknown',True)
        pipes=(process.stdin,process.stdout,process.stderr)
        output={'stdout':bytearray(),'stderr':bytearray()};offset=0;status='outcome_unknown';code=None
        try:
            with selectors.DefaultSelector() as selector:
                for pipe in pipes:os.set_blocking(pipe.fileno(),False)
                selector.register(process.stdout,selectors.EVENT_READ,'stdout')
                selector.register(process.stderr,selectors.EVENT_READ,'stderr')
                if input_bytes:selector.register(process.stdin,selectors.EVENT_WRITE,'stdin')
                else:process.stdin.close()
                while True:
                    if cancelled is not None and cancelled.is_set():status='cancelled';break
                    remaining=deadline-time.monotonic()
                    if remaining<=0:status='timeout';break
                    code=process.poll()
                    if code is not None and not any(key.data!='stdin' for key in selector.get_map().values()):
                        status='completed' if offset==len(input_bytes) or code!=0 else 'outcome_unknown'
                        break
                    if not selector.get_map():time.sleep(min(.01,remaining));continue
                    for key,_ in selector.select(min(.05,remaining)):
                        if key.data=='stdin':
                            try:written=os.write(key.fd,memoryview(input_bytes)[offset:offset+65536])
                            except (BlockingIOError,InterruptedError):continue
                            except BrokenPipeError:
                                selector.unregister(key.fileobj);key.fileobj.close();continue
                            if written<=0:raise OSError('docker_stdin_incomplete')
                            offset+=written
                            if offset==len(input_bytes):selector.unregister(key.fileobj);key.fileobj.close()
                        else:
                            available=output_limit-sum(len(v) for v in output.values())
                            try:data=os.read(key.fd,min(65536,available+1))
                            except (BlockingIOError,InterruptedError):continue
                            if not data:selector.unregister(key.fileobj);key.fileobj.close();continue
                            output[key.data].extend(data[:available])
                            if len(data)>available:status='output_limit';break
                    if status=='output_limit':break
        except KeyboardInterrupt:status='cancelled'
        except BaseException:status='outcome_unknown'
        finally:
            if not _close(pipes):status='outcome_unknown'
            if status!='completed' and not _stop_owned_helper(process):status='outcome_unknown'
            try:code=process.poll()
            except BaseException:status='outcome_unknown';code=None
        return CommandResult(code,bytes(output['stdout']),bytes(output['stderr']),status,True)
