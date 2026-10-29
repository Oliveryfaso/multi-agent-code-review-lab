"""No-network checks for the local, no-echo DeepSeek pilot launcher."""
import getpass
import io
import json
from pathlib import Path
import stat
import unittest
import warnings
from unittest.mock import Mock, patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import CORE_IMPLEMENTATION_FILES
from macr.evals.deepseek_policy_live_scope import LiveScopeError
from test_deepseek_policy_runner import UNKNOWN, call, reply
import run_deepseek_policy_live as live_cli
from run_deepseek_policy_live import (APPROVED_SOURCES, DATASET, LOCK_FILENAME,
    LiveCliError, execute_prepared, main, prepare_run, status_run)


PROJECT = Path(__file__).resolve().parents[1]
APPROVAL_REF = "synthetic-operator-approval"


class SyntheticTTY(io.StringIO):
    def __init__(self, value=""):
        super().__init__(value)
        self.reads = 0

    def isatty(self):
        return True

    def fileno(self):
        return 0

    def readline(self, *args):
        self.reads += 1
        return super().readline(*args)


class LiveCliTests(unittest.TestCase):
    def stopped_first_recovery(self):
        source, stopped = self.prepare_recovery()
        prior_dir = self.run_dir
        first = stopped['cells'][0]
        summary = execute_prepared(prior_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=lambda: 'DUMMY', cell_limit=1,
            scripted_by_cell={first['cell_id']: (TimeoutError(),)})
        self.assertEqual((summary['reserved_request_count'], summary['reserved_usd']),
                         (6, '0.660000'))
        old_lock = (self.private / 'cumulative-recovery-lock.json').read_bytes()
        return prior_dir, stopped, old_lock

    def prepare_second_recovery(self):
        prior_dir, stopped, old_lock = self.stopped_first_recovery()
        self.run_dir = self.private / 'pilot-retained-v4-fixture'
        batch = live_cli.prepare_recovery_run(self.run_dir,
            project_root=self.project, dataset_root=DATASET, private_root=self.private,
            approval_ref='second-recovery-confirmation', retain_unknown=True,
            predecessor_control_root=prior_dir / 'control',
            predecessor_batch_sha256=stopped['batch_sha256'])
        self.assertEqual((self.private / 'cumulative-recovery-lock.json').read_bytes(), old_lock)
        return prior_dir, stopped, batch

    def test_second_recovery_has_new_confirmation_and_preserves_both_unknown_holds(self):
        prior_dir, stopped, batch = self.prepare_second_recovery()
        summary = status_run(self.run_dir)
        self.assertEqual((summary['planned_cells'], summary['not_attempted_cells'],
                          summary['reserved_request_count'], summary['spent_usd'],
                          summary['retained_unknown_usd'], summary['spend_state']),
                         (44, 44, 6, '0.002089', '0.660000', 'open'))
        self.assertEqual(batch['implementation_version'], 'live-policy-retained-v4')
        self.assertEqual(batch['recovery_plan']['schema'], 'macr.deepseek.policy.recovery.v2')
        confirmation = json.loads((self.run_dir / 'recovery-confirmation.json').read_text())
        self.assertEqual(confirmation['schema'], 'macr.deepseek.policy.recovery.confirmation.v2')
        first = batch['cells'][0]
        if first['arm'] == 'C':
            answers = (reply(json.dumps(UNKNOWN)), reply(json.dumps({
                'status': 'accept', 'coverage': 'complete', 'reason': 'Fixture'})))
        elif first['arm'] == 'D':
            answers = (reply(None, calls=[call('finish-1', 'finish', {'diagnosis': UNKNOWN})]),)
        else:
            answers = (reply(json.dumps(UNKNOWN)),)
        reader = Mock(return_value='DUMMY_SYNTHETIC_NOT_A_CREDENTIAL')
        state = execute_prepared(self.run_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=reader, cell_limit=1,
            scripted_by_cell={first['cell_id']: answers})
        self.assertEqual((state['closed_cells'], state['reserved_request_count'],
                          state['retained_unknown_usd'], state['reserved_usd']),
                         (1, 6 + len(answers), '0.660000', '0.660000'))
        second = batch['cells'][1]
        state = execute_prepared(self.run_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=reader, cell_limit=1,
            scripted_by_cell={second['cell_id']: (TimeoutError(),)})
        self.assertEqual((state['closed_cells'], state['halted_cells'],
                          state['reserved_usd'], state['retained_unknown_usd'],
                          state['spend_state']),
                         (2, 1, '0.990000', '0.660000', 'halted_unknown'))
        before = reader.call_count
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                private_root=self.private, key_reader=reader)
        self.assertEqual(reader.call_count, before)
        previous = status_run(prior_dir)
        self.assertEqual((previous['reserved_request_count'], previous['reserved_usd'],
                          previous['spend_state']), (6, '0.660000', 'halted_unknown'))

    def test_first_recovery_confirmation_schema_cannot_enable_second_recovery(self):
        self.prepare_second_recovery()
        path = self.run_dir / 'recovery-confirmation.json'
        row = json.loads(path.read_text())
        row['schema'] = 'macr.deepseek.policy.recovery.confirmation.v1'
        path.write_text(json.dumps(row))
        reader = Mock(return_value='DUMMY')
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                private_root=self.private, key_reader=reader)
        reader.assert_not_called()
        self.assertFalse(list((self.run_dir / 'control' / 'spend' / 'entries').iterdir()))

    def test_second_recovery_rejects_old_generation_name_before_mutation(self):
        prior_dir, stopped, old_lock = self.stopped_first_recovery()
        target = self.private / 'pilot-retained-v3-second'
        with self.assertRaises(LiveCliError) as blocked:
            live_cli.prepare_recovery_run(target,
                project_root=self.project, dataset_root=DATASET, private_root=self.private,
                approval_ref='second-recovery-confirmation', retain_unknown=True,
                predecessor_control_root=prior_dir / 'control',
                predecessor_batch_sha256=stopped['batch_sha256'])
        self.assertEqual(blocked.exception.code, 'live_generation_name_invalid')
        self.assertFalse(target.exists())
        self.assertEqual((self.private / 'cumulative-recovery-lock.json').read_bytes(), old_lock)

    def test_third_recovery_is_not_enabled_by_second_recovery_support(self):
        _, _, second = self.prepare_second_recovery()
        prior_dir = self.run_dir
        first = second['cells'][0]
        execute_prepared(prior_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=lambda: 'DUMMY', cell_limit=1,
            scripted_by_cell={first['cell_id']: (TimeoutError(),)})
        target = self.private / 'pilot-retained-v4-third'
        with self.assertRaises(LiveCliError) as blocked:
            live_cli.prepare_recovery_run(target,
                project_root=self.project, dataset_root=DATASET, private_root=self.private,
                approval_ref='unapproved-third-confirmation', retain_unknown=True,
                predecessor_control_root=prior_dir / 'control',
                predecessor_batch_sha256=second['batch_sha256'])
        self.assertEqual(blocked.exception.code, 'live_recovery_depth_invalid')
        self.assertFalse(target.exists())

    def prepare_recovery(self):
        from test_deepseek_policy_recovery import make_recovery_source
        self.private.mkdir(mode=0o700)
        source = make_recovery_source(self.private, dataset_root=DATASET,
                                      project_root=self.project)
        self.run_dir = self.private / "pilot-retained-v3-fixture"
        batch = live_cli.prepare_recovery_run(self.run_dir,
            project_root=self.project, dataset_root=DATASET, private_root=self.private,
            approval_ref="new-recovery-confirmation", retain_unknown=True,
            predecessor_control_root=source.current,
            predecessor_batch_sha256=source.batch["batch_sha256"])
        return source, batch

    def test_recovery_subset_settles_new_work_but_keeps_old_hold_and_stops_on_new_unknown(self):
        from macr.evals.deepseek_policy_spend import spend_state
        source, batch = self.prepare_recovery()
        state = status_run(self.run_dir)
        self.assertEqual((state["planned_cells"], state["not_attempted_cells"],
                          state["reserved_request_count"], state["spent_usd"],
                          state["retained_unknown_usd"], state["spend_state"]),
                         (45, 45, 5, "0.002089", "0.330000", "open"))
        first = batch["cells"][0]
        if first["arm"] == "C":
            answers = (reply(json.dumps(UNKNOWN)), reply(json.dumps({
                "status": "accept", "coverage": "complete", "reason": "Fixture"})))
        elif first["arm"] == "D":
            answers = (reply(None, calls=[call("finish-1", "finish", {"diagnosis": UNKNOWN})]),)
        else:
            answers = (reply(json.dumps(UNKNOWN)),)
        key_reader = Mock(return_value="DUMMY_SYNTHETIC_NOT_A_CREDENTIAL")
        state = execute_prepared(self.run_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=key_reader, cell_limit=1,
            scripted_by_cell={first["cell_id"]: answers})
        self.assertEqual((state["closed_cells"], state["reserved_request_count"],
                          state["retained_unknown_usd"], state["reserved_usd"]),
                         (1, 5 + len(answers), "0.330000", "0.330000"))
        key_reader.assert_called_once()
        second = batch["cells"][1]
        state = execute_prepared(self.run_dir, dataset_root=DATASET,
            private_root=self.private, key_reader=key_reader, cell_limit=1,
            scripted_by_cell={second["cell_id"]: (TimeoutError(),)})
        self.assertEqual((state["closed_cells"], state["halted_cells"], state["reserved_usd"],
                          state["retained_unknown_usd"], state["spend_state"]),
                         (2, 1, "0.660000", "0.330000", "halted_unknown"))
        before = key_reader.call_count
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                private_root=self.private, key_reader=key_reader)
        self.assertEqual(key_reader.call_count, before)
        self.assertEqual(spend_state(source.current)["state"], "halted_unknown")

    def test_recovery_confirmation_drift_blocks_before_key_and_does_not_reset_budget(self):
        source, batch = self.prepare_recovery()
        confirmation = self.run_dir / "recovery-confirmation.json"
        row = json.loads(confirmation.read_text())
        row["approval_ref"] = "different-confirmation"
        confirmation.write_text(json.dumps(row))
        key_reader = Mock(return_value="DUMMY")
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                private_root=self.private, key_reader=key_reader)
        key_reader.assert_not_called()
        self.assertEqual(len(list((self.run_dir / "control" / "spend" / "entries").iterdir())), 0)

    def test_recovery_log_policy_drift_blocks_before_key(self):
        self.prepare_recovery()
        policy = self.run_dir / "outputs" / "retention.json"
        record = json.loads(policy.read_text())
        record["auto_delete"] = True
        policy.write_text(json.dumps(record))
        key_reader = Mock(return_value="DUMMY")
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                private_root=self.private, key_reader=key_reader, scripted_by_cell={})
        key_reader.assert_not_called()

    def test_recovery_prepare_requires_explicit_retention_before_mutation(self):
        with patch.object(live_cli, "PRIVATE_ROOT", self.private), patch.object(
                live_cli, "prepare_recovery_run", create=True) as prepare:
            result = main(["--prepare-recovery", "--run-dir",
                str(self.private / "pilot-retained-v3-fixture"),
                "--approval-ref", "new-recovery-confirmation",
                "--predecessor-control", str(self.root / "old" / "control"),
                "--predecessor-sha256", "a" * 64])
        self.assertEqual(result, 2)
        prepare.assert_not_called()
        self.assertFalse(self.private.exists())

    def test_recovery_command_binds_new_confirmation_to_explicit_source(self):
        previous = self.root / "old" / "control"
        with patch.object(live_cli, "PRIVATE_ROOT", self.private), patch.object(
                live_cli, "prepare_recovery_run", create=True,
                side_effect=LiveCliError("fixture-stop-before-preparation")) as prepare:
            result = main(["--prepare-recovery", "--retain-unknown", "--run-dir",
                str(self.private / "pilot-retained-v3-fixture"),
                "--approval-ref", "new-recovery-confirmation",
                "--predecessor-control", str(previous),
                "--predecessor-sha256", "a" * 64])
        self.assertEqual(result, 2)
        prepare.assert_called_once_with(self.private / "pilot-retained-v3-fixture",
            approval_ref="new-recovery-confirmation", retain_unknown=True,
            predecessor_control_root=previous, predecessor_batch_sha256="a" * 64)
        self.assertFalse(self.private.exists())

    def test_echo_fallback_warning_stops_before_input_dispatch_or_reservation(self):
        self.prepare()
        stdin, stderr = SyntheticTTY("DUMMY_SYNTHETIC_NOT_A_CREDENTIAL\n"), SyntheticTTY()
        dispatch = Mock()

        def execute(*args, key_reader, **kwargs):
            key_reader()
            dispatch()
            return {"batch_complete": True}

        with patch.object(live_cli, "PRIVATE_ROOT", self.private), patch.object(
                live_cli.sys, "stdin", stdin), patch.object(live_cli.sys, "stderr", stderr), patch.object(
                live_cli, "execute_prepared", side_effect=execute), patch.object(
                getpass, "getpass", side_effect=getpass.fallback_getpass), patch(
                "macr.evals.deepseek_policy_live_entry._ApprovedTransport") as provider, patch(
                "macr.evals.deepseek_policy_live_entry.reserve_spend") as reserve, warnings.catch_warnings():
            warnings.simplefilter("always")
            result = main(["--execute", "--run-dir", str(self.run_dir)])
        self.assertEqual(result, 2)
        self.assertEqual(stdin.reads, 0)
        self.assertEqual(stderr.getvalue(), "live_no_echo_input_unavailable\n")
        dispatch.assert_not_called()
        provider.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual(status_run(self.run_dir)["reserved_request_count"], 0)

    @unittest.skipUnless(hasattr(getpass, "unix_getpass"), "Unix terminal fixture")
    def test_terminal_echo_control_failure_never_reads_fallback_input(self):
        import termios
        for failure in ("read_attributes", "disable_echo"):
            with self.subTest(failure=failure):
                stdin = SyntheticTTY("DUMMY_SYNTHETIC_NOT_A_CREDENTIAL\n")
                stderr = SyntheticTTY()
                old = [0, 0, 0, termios.ECHO, 0, 0, []]
                attributes = {"side_effect": termios.error("private terminal detail")} if (
                    failure == "read_attributes") else {"return_value": old}
                with patch.object(live_cli.sys, "stdin", stdin), patch.object(
                        live_cli.sys, "stderr", stderr), patch.object(
                        getpass, "getpass", side_effect=getpass.unix_getpass), patch.object(
                        getpass.os, "open", side_effect=OSError("synthetic no controlling tty")), patch.object(
                        termios, "tcgetattr", **attributes), patch.object(
                        termios, "tcsetattr", side_effect=termios.error("private terminal detail")):
                    with self.assertRaises(LiveCliError) as blocked:
                        live_cli._read_key_without_echo()
                self.assertEqual(blocked.exception.code, "live_no_echo_input_unavailable")
                self.assertEqual(stdin.reads, 0)
                self.assertNotIn("private terminal detail", stderr.getvalue())
                self.assertNotIn("DUMMY_SYNTHETIC", stderr.getvalue())

    @unittest.skipUnless(hasattr(getpass, "unix_getpass"), "Unix terminal fixture")
    def test_successful_no_echo_input_reads_once_and_restores_terminal(self):
        import termios
        stdin, stderr = SyntheticTTY("DUMMY_SYNTHETIC_NOT_A_CREDENTIAL\n"), SyntheticTTY()
        old = [0, 0, 0, termios.ECHO, 0, 0, []]
        with patch.object(live_cli.sys, "stdin", stdin), patch.object(
                live_cli.sys, "stderr", stderr), patch.object(
                getpass, "getpass", side_effect=getpass.unix_getpass), patch.object(
                getpass.os, "open", side_effect=OSError("synthetic no controlling tty")), patch.object(
                termios, "tcgetattr", return_value=old), patch.object(
                termios, "tcsetattr") as set_attributes:
            value = live_cli._read_key_without_echo()
        self.assertEqual(value, "DUMMY_SYNTHETIC_NOT_A_CREDENTIAL")
        self.assertEqual(stdin.reads, 1)
        self.assertEqual(set_attributes.call_args_list[0].args[2][3] & termios.ECHO, 0)
        self.assertEqual(set_attributes.call_args_list[-1].args[2], old)
        self.assertNotIn("DUMMY_SYNTHETIC", stderr.getvalue())

    def test_key_input_io_failure_has_fixed_error_and_never_dispatches(self):
        self.prepare()
        for error in (OSError("private terminal detail"), EOFError("private terminal detail")):
            with self.subTest(error_type=type(error).__name__):
                stdin, stderr = SyntheticTTY(), SyntheticTTY()
                dispatch = Mock()

                def execute(*args, key_reader, **kwargs):
                    key_reader()
                    dispatch()
                    return {"batch_complete": True}

                with patch.object(live_cli, "PRIVATE_ROOT", self.private), patch.object(
                        live_cli.sys, "stdin", stdin), patch.object(live_cli.sys, "stderr", stderr), patch.object(
                        live_cli, "execute_prepared", side_effect=execute), patch.object(
                        getpass, "getpass", side_effect=error):
                    result = main(["--execute", "--run-dir", str(self.run_dir)])
                self.assertEqual(result, 2)
                self.assertEqual(stderr.getvalue(), "live_no_echo_input_unavailable\n")
                self.assertEqual(stdin.reads, 0)
                dispatch.assert_not_called()
        self.assertEqual(status_run(self.run_dir)["reserved_request_count"], 0)

    def test_prepare_accepts_explicit_operator_approval_without_private_default(self):
        reference = "operator-approved-fixture"
        batch = prepare_run(self.run_dir, project_root=self.project,
            dataset_root=DATASET, private_root=self.private, approval_ref=reference)
        approval = json.loads((self.run_dir / "approval.json").read_text())
        lock = json.loads((self.private / LOCK_FILENAME).read_text())
        self.assertEqual(approval["approval_ref"], reference)
        self.assertEqual(lock["approval_ref"], reference)
        self.assertEqual(approval["batch_sha256"], batch["batch_sha256"])

    def test_command_prepare_requires_explicit_predecessor_before_mutation(self):
        with patch("run_deepseek_policy_live.PRIVATE_ROOT", self.private), patch(
                "run_deepseek_policy_live.prepare_run") as prepare:
            self.assertEqual(main(["--prepare", "--run-dir",
                str(self.private / "pilot-refs-v2-fixture"),
                "--approval-ref", "operator-approved-fixture"]), 2)
        prepare.assert_not_called()
        self.assertFalse(self.private.exists())

    def test_command_forwards_only_explicit_approval_and_predecessor(self):
        previous = self.root / "previous" / "control"
        with patch("run_deepseek_policy_live.PRIVATE_ROOT", self.private), patch(
                "run_deepseek_policy_live.prepare_run",
                side_effect=LiveCliError("fixture-stop-before-preparation")) as prepare:
            self.assertEqual(main(["--prepare", "--run-dir",
                str(self.private / "pilot-refs-v2-fixture"), "--approval-ref",
                "operator-approved-fixture", "--predecessor-control", str(previous),
                "--predecessor-sha256", "a" * 64]), 2)
        prepare.assert_called_once_with(self.private / "pilot-refs-v2-fixture",
            approval_ref="operator-approved-fixture", predecessor_control_root=previous,
            predecessor_batch_sha256="a" * 64)
        self.assertFalse(self.private.exists())

    def test_approval_and_lock_identity_drift_refuse_before_key_read(self):
        self.prepare()
        approval_path = self.run_dir / "approval.json"
        lock_path = self.private / LOCK_FILENAME
        original = {path: path.read_text() for path in (approval_path, lock_path)}
        reads = []
        for path, field, value in ((approval_path, "approval_ref", "another-approval"),
                (approval_path, "predecessor_batch_sha256", "a" * 64),
                (lock_path, "approval_ref", "another-approval"),
                (lock_path, "predecessor_batch_sha256", "a" * 64)):
            with self.subTest(field=field, file=path.name):
                row = json.loads(original[path])
                row[field] = value
                path.write_text(json.dumps(row), encoding="utf-8")
                with self.assertRaises(LiveCliError):
                    execute_prepared(self.run_dir, dataset_root=DATASET,
                        key_reader=lambda: reads.append(True) or "DUMMY")
                path.write_text(original[path], encoding="utf-8")
        self.assertEqual(reads, [])
        self.assertEqual(status_run(self.run_dir)["not_attempted_cells"], 48)

    def test_real_executor_refuses_missing_carry_before_reading_key(self):
        self.prepare()
        reads = []
        with self.assertRaises(LiveCliError) as blocked:
            execute_prepared(self.run_dir, dataset_root=DATASET,
                key_reader=lambda: reads.append(True) or "DUMMY")
        self.assertEqual(blocked.exception.code, "live_cumulative_budget_missing")
        self.assertEqual(reads, [])

    def test_v2_preparation_preserves_original_batch_lock(self):
        self.private.mkdir(mode=0o700)
        original = self.private / "approval-lock.json"
        original.write_text('{"original_batch":"immutable"}', encoding="utf-8")
        before = original.read_bytes()
        self.prepare()
        self.assertEqual(original.read_bytes(), before)
        self.assertTrue((self.private / LOCK_FILENAME).is_file())

    def setUp(self):
        self.root = fixture_root()
        self.private = self.root / "private-pilot-runs"
        self.run_dir = self.private / "pilot-contract-test"
        self.project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PROJECT / relative).read_bytes())

    def prepare(self, **kwargs):
        return prepare_run(self.run_dir, project_root=self.project,
            dataset_root=DATASET, private_root=self.private,
            approval_ref=APPROVAL_REF, **kwargs)

    def test_prepare_freezes_exact_scope_private_files_and_no_key(self):
        batch = self.prepare()
        approval = json.loads((self.run_dir / "approval.json").read_text())
        self.assertEqual(batch["batch_sha256"], approval["batch_sha256"])
        self.assertEqual(approval["approval_ref"], APPROVAL_REF)
        self.assertEqual(tuple(approval["approved_sources"]), APPROVED_SOURCES)
        self.assertEqual((approval["model_id"], approval["max_requests"],
                          approval["max_usd"], approval["retention_days"]),
                         ("deepseek-flash", 84, "3.00", 14))
        for directory in (self.private, self.run_dir, self.run_dir / "outputs",
                          self.run_dir / "control"):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for file in (self.run_dir / "approval.json",
                     self.run_dir / "outputs" / "retention.json"):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(status_run(self.run_dir)["not_attempted_cells"], 48)
        with self.assertRaises(LiveCliError):
            self.prepare()

    def test_one_approval_cannot_prepare_a_second_run_directory(self):
        self.prepare()
        other = self.private / "pilot-second-run"
        with self.assertRaises(LiveCliError):
            prepare_run(other, project_root=self.project, dataset_root=DATASET,
                        private_root=self.private, approval_ref=APPROVAL_REF)
        self.assertFalse(other.exists())
        lock = json.loads((self.private / LOCK_FILENAME).read_text())
        self.assertEqual(lock["run_dir"], str(self.run_dir))

    def test_execute_command_refuses_noninteractive_input_before_key(self):
        self.prepare()
        with patch("run_deepseek_policy_live.PRIVATE_ROOT", self.private):
            self.assertEqual(main(["--execute", "--run-dir", str(self.run_dir)]), 2)
        self.assertEqual(status_run(self.run_dir)["not_attempted_cells"], 48)

    def test_one_scripted_cell_reads_dummy_key_once_and_can_pause(self):
        self.prepare()
        reads = []

        def dummy_reader():
            reads.append(True)
            return "DUMMY_KEY_NEVER_PERSIST"

        batch = json.loads((self.run_dir / "control" / "batch.json").read_text())
        self.assertEqual(batch["cells"][0]["arm"], "A")
        first = batch["cells"][0]["cell_id"]
        summary = execute_prepared(self.run_dir, dataset_root=DATASET,
            key_reader=dummy_reader, cell_limit=1,
            scripted_by_cell={first: (reply(json.dumps(UNKNOWN)),)})
        self.assertEqual(reads, [True])
        self.assertEqual((summary["closed_cells"], summary["not_attempted_cells"],
                          summary["confirmed_live_attempts"]), (1, 47, 0))
        self.assertEqual(summary["reserved_request_count"], 1)
        self.assertFalse(summary["batch_complete"])
        self.assertNotIn("DUMMY_KEY_NEVER_PERSIST", "".join(
            path.read_text() for path in self.run_dir.rglob("*.json")))

    def test_tampered_scope_fails_before_dummy_key_reader(self):
        self.prepare()
        approval_file = self.run_dir / "approval.json"
        record = json.loads(approval_file.read_text())
        record["approved_sources"].append("gold.json")
        approval_file.write_text(json.dumps(record), encoding="utf-8")
        reads = []
        with self.assertRaises((LiveCliError, LiveScopeError)):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                key_reader=lambda: reads.append(True) or "DUMMY", cell_limit=1)
        self.assertEqual(reads, [])
        self.assertEqual(status_run(self.run_dir)["not_attempted_cells"], 48)

    def test_unknown_scripted_request_halts_without_retry(self):
        self.prepare()
        batch = json.loads((self.run_dir / "control" / "batch.json").read_text())
        first = batch["cells"][0]["cell_id"]
        summary = execute_prepared(self.run_dir, dataset_root=DATASET,
            key_reader=lambda: "DUMMY_KEY", cell_limit=48,
            scripted_by_cell={first: (TimeoutError(),)})
        self.assertEqual((summary["closed_cells"], summary["not_attempted_cells"],
                          summary["halted_cells"], summary["reserved_usd"]),
                         (1, 47, 1, "0.330000"))
        with self.assertRaises(LiveCliError):
            execute_prepared(self.run_dir, dataset_root=DATASET,
                key_reader=lambda: "DUMMY_KEY", cell_limit=1)
        self.assertEqual(status_run(self.run_dir)["reserved_request_count"], 1)


if __name__ == "__main__":
    unittest.main()
