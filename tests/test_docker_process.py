"""Authored in-process pipe/process observations; never starts a child or Docker."""
from contextlib import ExitStack
from pathlib import Path
import selectors
import signal
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class Clock:
    def __init__(self):self.now=100.0
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class Pipe:
    def __init__(self,fd,chunks=()):self.fd=fd;self.chunks=list(chunks);self.closed=False
    def fileno(self):return self.fd
    def close(self):self.closed=True


class Process:
    def __init__(self,*,stdout=(b'',),stderr=(b'',),code=0,hang=False,term=True,kill=True):
        self.stdin=Pipe(101);self.stdout=Pipe(102,stdout);self.stderr=Pipe(103,stderr)
        self.pid=43210;self.code=code;self.hang=hang;self.term=term;self.kill=kill
        self.returncode=None;self.written=bytearray();self.signals=[];self.waits=[]
        self.write_error=None;self.read_errors={};self.write_size=65536

    def poll(self):
        if self.returncode is None and not self.hang and self.stdin.closed and not self.stdout.chunks and not self.stderr.chunks:
            self.returncode=self.code
        return self.returncode

    def wait(self,timeout):
        self.waits.append(timeout)
        if self.poll() is None:raise subprocess.TimeoutExpired('AUTHORED',timeout)
        return self.returncode

    def killpg(self,pid,sig):
        if pid!=self.pid:raise AssertionError('foreign group')
        self.signals.append(sig)
        if sig==signal.SIGTERM and self.term or sig==signal.SIGKILL and self.kill:
            self.returncode=-sig

    def read(self,fd,size):
        failures=self.read_errors.get(fd,[])
        if failures:raise failures.pop(0)
        pipe=self.stdout if fd==102 else self.stderr
        value=pipe.chunks.pop(0)
        if len(value)>size:pipe.chunks.insert(0,value[size:]);value=value[:size]
        return value

    def write(self,fd,value):
        if fd!=101:raise AssertionError('wrong pipe')
        if self.write_error is not None:raise self.write_error
        size=min(len(value),self.write_size);self.written.extend(value[:size]);return size


class Selector:
    def __init__(self,clock):self.clock=clock;self.entries={};self.closed=False
    def register(self,obj,events,data=None):
        self.entries[obj.fileno()]=selectors.SelectorKey(obj,obj.fileno(),events,data)
    def unregister(self,obj):
        fd=obj if type(obj) is int else obj.fileno()
        return self.entries.pop(fd)
    def get_map(self):return self.entries
    def select(self,timeout):
        self.clock.sleep(min(.01,timeout))
        return [(key,key.events) for key in list(self.entries.values())]
    def close(self):self.closed=True
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class DockerProcessTests(unittest.TestCase):
    def invoke(self,args=None,*,process=None,input_bytes=b'',deadline=101.0,output_limit=1024,cancelled=None,launch_error=None,setup_error=None,forbid_environ=False):
        from macr.execution import docker_process as module
        clock=Clock();process=process or Process();selector=Selector(clock)
        with ExitStack() as stack:
            popen=stack.enter_context(patch.object(module.subprocess,'Popen',side_effect=launch_error or (lambda *a,**kw:process)))
            stack.enter_context(patch.object(module.selectors,'DefaultSelector',return_value=selector))
            stack.enter_context(patch.object(module.time,'monotonic',side_effect=clock.monotonic))
            stack.enter_context(patch.object(module.time,'sleep',side_effect=clock.sleep))
            stack.enter_context(patch.object(module.os,'set_blocking',side_effect=setup_error))
            if forbid_environ:
                class ForbiddenEnvironment:
                    def __getitem__(self,*a):raise AssertionError('must not inspect inherited environment')
                    def __iter__(self):raise AssertionError('must not inspect inherited environment')
                    def copy(self):raise AssertionError('must not inspect inherited environment')
                stack.enter_context(patch.object(module.os,'environ',ForbiddenEnvironment()))
            stack.enter_context(patch.object(module.os,'read',side_effect=process.read))
            stack.enter_context(patch.object(module.os,'write',side_effect=process.write))
            killpg=stack.enter_context(patch.object(module.os,'killpg',side_effect=process.killpg))
            runner=module.NativeDockerRunner(Path('/trusted/docker'),'unix:///owned/docker.sock',Path('/owned/client'))
            result=runner(args if args is not None else ['version','--format','{{json .Server}}'],
                input_bytes=input_bytes,deadline=deadline,output_limit=output_limit,cancelled=cancelled)
        return SimpleNamespace(result=result,process=process,popen=popen,killpg=killpg,selector=selector)

    def test_empty_success_and_exact_fixed_process_contract(self):
        run=self.invoke(forbid_environ=True);self.assertEqual((run.result.status,run.result.returncode,run.result.started),('completed',0,True))
        self.assertEqual((run.result.stdout,run.result.stderr),(b'',b''))
        argv=run.popen.call_args.args[0]
        self.assertEqual(argv[:5],['/trusted/docker','--config','/owned/client','--host','unix:///owned/docker.sock'])
        kwargs=run.popen.call_args.kwargs
        self.assertEqual(kwargs['env'],{'HOME':'/owned/client','DOCKER_CONFIG':'/owned/client','PATH':'/usr/bin:/bin','LANG':'C','DOCKER_CLI_HINTS':'false'})
        self.assertIs(kwargs['shell'],False);self.assertIs(kwargs['close_fds'],True)
        self.assertIs(kwargs['start_new_session'],True);self.assertEqual(kwargs['bufsize'],0)
        self.assertEqual(kwargs['cwd'],'/owned/client')
        self.assertEqual((kwargs['stdin'],kwargs['stdout'],kwargs['stderr']),(subprocess.PIPE,)*3)
        run.killpg.assert_not_called();self.assertTrue(run.selector.closed)
        self.assertTrue(all(p.closed for p in (run.process.stdin,run.process.stdout,run.process.stderr)))

    def test_partial_input_and_both_output_streams_are_drained(self):
        process=Process(stdout=[b'one',b'two',b''],stderr=[b'note',b'']);process.write_size=2
        process.read_errors[102]=[BlockingIOError(),InterruptedError()]
        run=self.invoke(['container','cp','-','a'*64+':/payload'],process=process,input_bytes=b'abcdefgh')
        self.assertEqual(run.result.status,'completed');self.assertEqual(process.written,b'abcdefgh')
        self.assertEqual((run.result.stdout,run.result.stderr),(b'onetwo',b'note'))
        run.killpg.assert_not_called()

    def test_combined_output_limit_clips_prefix_and_stops_only_owned_helper(self):
        process=Process(stdout=[b'ab',b''],stderr=[b'cd',b''],hang=True)
        run=self.invoke(process=process,output_limit=3)
        self.assertEqual(run.result.status,'output_limit');self.assertEqual(len(run.result.stdout)+len(run.result.stderr),3)
        self.assertEqual(process.signals,[signal.SIGTERM]);self.assertEqual(process.waits,[.2])
        self.assertEqual(run.killpg.call_args.args[0],process.pid)

    def test_exact_output_bound_then_eof_is_not_a_false_limit(self):
        run=self.invoke(process=Process(stdout=[b'ab',b''],stderr=[b'c',b'']),output_limit=3)
        self.assertEqual(run.result.status,'completed');self.assertEqual((run.result.stdout,run.result.stderr),(b'ab',b'c'))

    def test_expired_deadline_and_already_cancelled_do_not_launch(self):
        expired=self.invoke(deadline=100);self.assertEqual((expired.result.status,expired.result.started),('timeout',False));expired.popen.assert_not_called()
        event=Mock();event.is_set.return_value=True
        stopped=self.invoke(cancelled=event);self.assertEqual((stopped.result.status,stopped.result.started),('cancelled',False));stopped.popen.assert_not_called()

    def test_timeout_escalates_owned_helper_group_with_bounded_waits(self):
        process=Process(hang=True,term=False)
        run=self.invoke(process=process,deadline=100.08)
        self.assertEqual(run.result.status,'timeout');self.assertEqual(process.signals,[signal.SIGTERM,signal.SIGKILL])
        self.assertEqual(process.waits,[.2,.5]);self.assertEqual(run.result.returncode,-signal.SIGKILL)

    def test_cancel_during_io_stops_helper_and_preserves_bounded_observations(self):
        event=Mock();event.is_set.side_effect=[False,False,True,True]
        process=Process(stdout=[b'partial',b''],hang=True)
        run=self.invoke(process=process,cancelled=event)
        self.assertEqual(run.result.status,'cancelled');self.assertTrue(run.result.started)
        self.assertEqual(process.signals,[signal.SIGTERM])

    def test_launch_denial_has_no_raw_exception_or_signal(self):
        run=self.invoke(launch_error=PermissionError('DUMMY-PRIVATE-NO-LOG'))
        self.assertEqual((run.result.status,run.result.started,run.result.returncode),('launch_failed',False,None))
        self.assertEqual((run.result.stdout,run.result.stderr),(b'',b''));run.killpg.assert_not_called()
        self.assertNotIn('DUMMY-PRIVATE',repr(run.result))

    def test_interrupted_or_unclassified_launch_is_unknown_without_guessed_cleanup(self):
        for error in (KeyboardInterrupt(),RuntimeError('DUMMY-PRIVATE-UNKNOWN')):
            with self.subTest(error=type(error).__name__):
                run=self.invoke(launch_error=error)
                self.assertEqual((run.result.status,run.result.started),('outcome_unknown',True))
                run.killpg.assert_not_called();self.assertNotIn('DUMMY-PRIVATE',repr(run.result))

    def test_pipe_setup_failure_still_closes_and_stops_new_helper(self):
        run=self.invoke(setup_error=OSError('DUMMY-PRIVATE-FD'))
        self.assertEqual(run.result.status,'outcome_unknown');self.assertTrue(run.result.started)
        self.assertEqual(run.process.signals,[signal.SIGTERM])
        self.assertTrue(all(p.closed for p in (run.process.stdin,run.process.stdout,run.process.stderr)))

    def test_unsafe_group_or_signal_denial_never_targets_a_foreign_process(self):
        process=Process(hang=True);process.pid=1
        run=self.invoke(process=process,deadline=100.04)
        self.assertEqual(run.result.status,'outcome_unknown');run.killpg.assert_not_called()
        process=Process(hang=True);process.killpg=Mock(side_effect=PermissionError('DUMMY-PRIVATE'))
        run=self.invoke(process=process,deadline=100.04)
        self.assertEqual(run.result.status,'outcome_unknown');self.assertIsNone(run.result.returncode)
        self.assertEqual(run.killpg.call_args.args,(43210,signal.SIGTERM))

    def test_cleanup_interruptions_return_unknown_and_continue_other_owned_cleanup(self):
        for exception in (KeyboardInterrupt,SystemExit):
            with self.subTest(stage='wait',exception=exception.__name__):
                process=Process(hang=True);process.wait=Mock(side_effect=exception())
                run=self.invoke(process=process,deadline=100.04)
                self.assertEqual(run.result.status,'outcome_unknown')
                self.assertEqual(process.signals,[signal.SIGTERM])
                self.assertTrue(all(p.closed for p in (process.stdin,process.stdout,process.stderr)))
            with self.subTest(stage='pipe-close',exception=exception.__name__):
                process=Process(hang=True);process.stdout.close=Mock(side_effect=exception())
                run=self.invoke(process=process,deadline=100.04)
                self.assertEqual(run.result.status,'outcome_unknown')
                self.assertTrue(process.stdin.closed);self.assertTrue(process.stderr.closed)
                self.assertEqual(process.signals,[signal.SIGTERM])
            with self.subTest(stage='final-poll',exception=exception.__name__):
                process=Process();original=process.poll;finished_polls=[]
                def poll():
                    if all(p.closed for p in (process.stdin,process.stdout,process.stderr)):
                        finished_polls.append(True)
                        if len(finished_polls)>1:raise exception()
                    return original()
                process.poll=poll;run=self.invoke(process=process)
                self.assertEqual(run.result.status,'outcome_unknown');self.assertIsNone(run.result.returncode)
                run.killpg.assert_not_called()

    def test_unconfirmed_helper_stop_is_unknown_and_never_claims_container_closure(self):
        process=Process(hang=True,term=False,kill=False)
        run=self.invoke(process=process,deadline=100.04)
        self.assertEqual(run.result.status,'outcome_unknown');self.assertIsNone(run.result.returncode)
        self.assertEqual(process.signals,[signal.SIGTERM,signal.SIGKILL])
        self.assertTrue(all(p.closed for p in (process.stdin,process.stdout,process.stderr)))

    def test_reaped_helper_never_causes_a_late_group_signal(self):
        process=Process();process.returncode=0
        run=self.invoke(process=process,deadline=100.005)
        run.killpg.assert_not_called()

    def test_incomplete_stdin_cannot_be_a_success_but_known_rejection_is_retained(self):
        for code,status in ((0,'outcome_unknown'),(125,'completed')):
            with self.subTest(code=code):
                process=Process(code=code);process.write_error=BrokenPipeError()
                run=self.invoke(['container','cp','-','a'*64+':/payload'],process=process,input_bytes=b'archive')
                self.assertEqual(run.result.status,status);self.assertEqual(run.result.returncode,code)

    def test_invalid_verb_arguments_and_resource_bounds_never_dispatch(self):
        for args in (['run','--privileged','image'],['image','pull','image'],['container','exec','x','sh'],['--host','unix:///other','version'],['version','--host=unix:///other'],['container','inspect','--context','other'],['version','-cother'],['version','bad\narg'],[] ):
            with self.subTest(args=args):
                run=self.invoke(args);self.assertEqual(run.result.status,'launch_failed');run.popen.assert_not_called()
        for options in ({'input_bytes':b'x'*(20*1024*1024+1)},{'output_limit':0},{'output_limit':1024*1024+1},{'deadline':float('nan')}):
            with self.subTest(options=list(options)):
                run=self.invoke(**options);self.assertFalse(run.result.started);run.popen.assert_not_called()

    def test_constructor_only_accepts_canonical_absolute_local_configuration(self):
        from macr.execution.docker_process import NativeDockerRunner
        for docker,endpoint,root in ((Path('docker'),'unix:///owned/docker.sock',Path('/owned/client')),
                (Path('/trusted/docker'),'tcp://127.0.0.1:2375',Path('/owned/client')),
                (Path('/trusted/docker'),'unix:////owned/docker.sock',Path('/owned/client')),
                (Path('/trusted/docker'),'unix:///owned/../other.sock',Path('/owned/client')),
                (Path('/trusted/docker'),'unix:///owned/docker.sock',Path('client'))):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):NativeDockerRunner(docker,endpoint,root)
