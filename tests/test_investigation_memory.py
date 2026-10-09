import unittest
from dataclasses import replace
from macr.investigation.records import EvidenceRecord, HypothesisRecord, GradeDecision, RoleMessage, Location, CheckResult, SnapshotRef
from macr.investigation.validation import ContractError
from macr.memory.board import AgentBoard
from macr.memory.evidence_store import EvidenceStore
try:
    from macr.memory.hypothesis_store import HypothesisStore
except ModuleNotFoundError:
    HypothesisStore = None


class InvestigationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(HypothesisStore, 'hypothesis gate missing')
        self.ref = SnapshotRef('snapshot', 'content', 'unused', 'unused')
        self.evidence = EvidenceStore(self.ref)
        self.evidence.add_record(EvidenceRecord('e1', 'snapshot', 'branch returns early', Location('a.py', 1, 2), 'return value', origin='rg'))

    def test_provenance_is_not_confidence_voting_and_is_immutable(self):
        self.evidence.add_record(EvidenceRecord('e2', 'snapshot', 'branch returns early', Location('a.py', 1, 2), 'return value', origin='ast'))
        self.assertEqual(len(self.evidence.source_groups()), 1)
        copy = self.evidence.get_record('e1')
        copy.observation = 'mutated'
        self.assertEqual(self.evidence.get_record('e1').observation, 'branch returns early')
        with self.assertRaises(ContractError):
            self.evidence.add_record(replace(copy, evidence_id='e3', snapshot_id='other'))
        with self.assertRaises(ContractError):
            self.evidence.get_record('unknown')

    def test_role_messages_consume_ack_and_roundtrip(self):
        board = AgentBoard(evidence=self.evidence, hypothesis_ids={'h1'}, request_ids={'a1'})
        message = RoleMessage('m1', 1, 'investigator', 'falsifier', request_id='a1', evidence_ids=['e1'], hypothesis_ids=['h1'])
        self.assertEqual(board.post_message(message), 'm1')
        self.assertEqual(board.consume('falsifier', 0)[0].message_id, 'm1')
        self.assertEqual(board.consume('investigator', 0), [])
        with self.assertRaises(ContractError):
            board.acknowledge('investigator', ['m1'])
        board.acknowledge('falsifier', ['m1'])
        restored = AgentBoard.from_messages(board.messages(), evidence=self.evidence, hypothesis_ids={'h1'}, request_ids={'a1'})
        self.assertEqual(restored.messages()[0].consumption_status, 'consumed')
        with self.assertRaises(ContractError):
            board.post_message(replace(message, message_id='m2', sequence=2, request_id='missing'))

    def test_refutation_preserves_prior_version_and_causal_gate(self):
        store = HypothesisStore(self.evidence)
        h = HypothesisRecord('h1', 'early return causes the symptom', support_ids=['e1'], alternatives=['dependency error'])
        store.propose(h)
        with self.assertRaises(ContractError):
            store.propose(replace(h, hypothesis_id='h2', status='causal_supported'))
        with self.assertRaises(ContractError):
            store.decide(GradeDecision('h1', 'causal_supported', ['e1']))
        self.assertEqual(store.decide(GradeDecision('h1', 'static_supported', ['e1'])).status, 'supported')
        self.assertEqual(store.decide(GradeDecision('h1', 'refuted', counter_ids=['e1'])).status, 'refuted')
        self.assertEqual([v.status for v in store.history('h1')], ['candidate', 'supported', 'refuted'])

    def test_behavior_gate_requires_observed_matching_check(self):
        check = CheckResult('c1', 'completed', 'failed', 1, {'failed': 1}, expectation_result='matches', environment_ref='fixture-only')
        store = HypothesisStore(self.evidence, checks={'c1': check})
        store.propose(HypothesisRecord('h1', 'fixture behavior', support_ids=['e1'], predictions=[{'check_id': 'c1', 'kind': 'reproduction', 'observed_status': 'failed'}]))
        self.assertEqual(store.decide(GradeDecision('h1', 'behavior_reproduced', ['e1'], check_ids=['c1'])).status, 'behavior_reproduced')
        store.checks['c1'] = replace(check, execution_status='timeout')
        with self.assertRaises(ContractError):
            store.decide(GradeDecision('h1', 'behavior_reproduced', ['e1'], check_ids=['c1']))
