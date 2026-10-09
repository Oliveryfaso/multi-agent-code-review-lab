import unittest
from macr.investigation.records import ActionResult, BudgetView, BudgetProfile
from test_investigation_budget import tool_action
try:
    from macr.investigation.queue import InvestigationQueue
except ModuleNotFoundError:
    InvestigationQueue = None


class InvestigationQueueTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(InvestigationQueue, 'investigation queue missing')

    def test_both_scan_lanes_receive_reserved_quarter(self):
        queue = InvestigationQueue()
        for i in range(16):
            queue.push(tool_action(i), 'breadth', 100)
            queue.push(tool_action(100+i), 'depth', 1)
        view = BudgetView(profile=BudgetProfile(tool_calls=16))
        chosen = [queue.pop(view).action_id for _ in range(16)]
        self.assertGreaterEqual(sum(int(a[1:]) >= 100 for a in chosen), 4)
        self.assertGreaterEqual(sum(int(a[1:]) < 100 for a in chosen), 4)

    def test_empty_lane_releases_share_and_no_information_stop(self):
        queue = InvestigationQueue()
        for i in range(3):
            queue.push(tool_action(i), 'depth', 1)
        for i in range(3):
            action = queue.pop(BudgetView())
            queue.observe(action, ActionResult(action.action_id, 'completed', new_information=False))
            self.assertEqual(queue.should_stop(), i == 2)
        self.assertIn('breadth', queue.released_lanes)
        queue.push(tool_action(4), 'breadth', 1)
        self.assertFalse(queue.should_stop())
        action = queue.pop(BudgetView())
        queue.observe(action, ActionResult(action.action_id, 'completed', new_information=True))
        self.assertFalse(queue.should_stop())

    def test_pending_queue_roundtrip_keeps_priority_and_duplicate_guard(self):
        from macr.investigation.validation import ContractError
        queue = InvestigationQueue()
        queue.push(tool_action(1), 'breadth', 5)
        queue.push(tool_action(2), 'depth', 2)
        with self.assertRaises(ContractError):
            queue.push(tool_action(1), 'depth', 8)
        restored = InvestigationQueue.restore(queue.snapshot())
        self.assertEqual(restored.pop(BudgetView()).action_id, 'a1')
