import unittest
from dataclasses import replace
from debugger_fixtures import FakeClock
from macr.investigation.records import ActionRequest, ActionUsage, ModelArgs, SearchArgs, CheckArgs, CheckSpec, Limits, BudgetProfile
from macr.investigation.validation import ContractError
try:
    from macr.investigation.budget import BudgetLedger, scan_profile
except ModuleNotFoundError:
    BudgetLedger = scan_profile = None


def tool_action(i):
    return ActionRequest(f'a{i}', 'investigator', 'search_text', SearchArgs('fixture'))


class InvestigationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(BudgetLedger, 'budget ledger missing')
        self.clock = FakeClock()

    def test_profile_tool_model_check_boundaries_and_retries(self):
        self.assertEqual((BudgetProfile().model_calls, scan_profile().tool_calls), (8, 160))
        for profile in (BudgetProfile(), scan_profile()):
            ledger = BudgetLedger(profile, clock=self.clock)
            for i in range(profile.tool_calls):
                ledger.reserve(tool_action(i), ActionUsage(tool_calls=1))
                ledger.settle(f'a{i}', ActionUsage(tool_calls=1))
            with self.assertRaises(ContractError):
                ledger.reserve(tool_action(profile.tool_calls), ActionUsage(tool_calls=1))
            model = BudgetLedger(profile, clock=self.clock)
            for i in range(profile.model_calls):
                model.reserve(ActionRequest(f'm{i}', 'coordinator', 'call_model', ModelArgs({})), ActionUsage(model_calls=1, input_tokens=1, output_tokens=1))
                model.settle(f'm{i}', ActionUsage(model_calls=1, input_tokens=1, output_tokens=1))
            with self.assertRaises(ContractError):
                model.reserve(ActionRequest('retry', 'coordinator', 'call_model', ModelArgs({})), ActionUsage(model_calls=1, input_tokens=1, output_tokens=1))
            check_ledger = BudgetLedger(profile, clock=self.clock)
            for i in range(profile.dynamic_checks):
                check = CheckSpec(f'c{i}', limits=Limits(1, 1024, 2, profile.check_wall_seconds, 1024))
                check_ledger.reserve(ActionRequest(f'c{i}', 'coordinator', 'run_check', CheckArgs(check)), ActionUsage(tool_calls=1, checks=1))
                check_ledger.settle(f'c{i}', ActionUsage(tool_calls=1, checks=1))
            with self.assertRaises(ContractError):
                check_ledger.reserve(ActionRequest('over', 'coordinator', 'run_check', CheckArgs(check)), ActionUsage(tool_calls=1, checks=1))

    def test_unknown_usage_is_conservative_and_double_settlement_rejected(self):
        ledger = BudgetLedger(clock=self.clock)
        action = ActionRequest('model', 'coordinator', 'call_model', ModelArgs({}))
        upper = ActionUsage(model_calls=1, input_tokens=200, output_tokens=30, usage_kind='estimated')
        ledger.reserve(action, upper)
        view = ledger.settle('model', ActionUsage(usage_kind='unknown'))
        self.assertEqual((view.used.model_calls, view.used.input_tokens, view.used.output_tokens), (1, 200, 30))
        self.assertEqual(view.reservations['model'].state, 'unknown')
        restored = BudgetLedger.restore(view, clock=self.clock)
        with self.assertRaises(ContractError):
            restored.settle('model', ActionUsage(model_calls=1))
        with self.assertRaises(ContractError):
            restored.reserve(replace(action, action_id='no-bound'), ActionUsage(model_calls=1, usage_kind='unknown'))

    def test_active_time_wait_pause_and_restore_do_not_reset_budget(self):
        ledger = BudgetLedger(clock=self.clock)
        self.clock.advance(10)
        ledger.reserve(tool_action(1), ActionUsage(tool_calls=1))
        self.clock.advance(5)
        with self.assertRaises(ContractError):
            ledger.pause()
        ledger.settle('a1', ActionUsage(tool_calls=1))
        ledger.pause()
        self.clock.advance(100)
        self.assertEqual(ledger.view().used.active_seconds, 15)
        restored = BudgetLedger.restore(ledger.view(), clock=self.clock)
        restored.resume()
        self.clock.advance(6)
        self.assertEqual(restored.view().used.active_seconds, 21)
        self.clock.advance(700)
        with self.assertRaises(ContractError):
            restored.reserve(tool_action(2), ActionUsage(tool_calls=1))

    def test_overshoot_is_recorded_and_blocks_future_work(self):
        ledger = BudgetLedger(replace(BudgetProfile(), input_tokens=10), clock=self.clock)
        action = ActionRequest('model', 'coordinator', 'call_model', ModelArgs({}))
        ledger.reserve(action, ActionUsage(model_calls=1, input_tokens=5, output_tokens=1))
        view = ledger.settle('model', ActionUsage(model_calls=1, input_tokens=12, output_tokens=1))
        self.assertEqual(view.used.input_tokens, 12)
        with self.assertRaises(ContractError):
            ledger.reserve(tool_action(1), ActionUsage(tool_calls=1))
