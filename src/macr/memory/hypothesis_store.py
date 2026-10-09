"""Program-owned preliminary grade gates; final adjudication belongs to Task15."""
from copy import deepcopy
from dataclasses import asdict
from macr.investigation.records import HypothesisRecord, GradeDecision, CheckResult, CheckSpec
from macr.investigation.validation import ContractError, decode_record
from macr.execution.results import classify_check


class HypothesisStore:
    def __init__(self, evidence, checks: dict[str, CheckResult] | None = None):
        self.evidence, self.checks = evidence, checks if checks is not None else {}
        self._versions: dict[str, list[HypothesisRecord]] = {}

    def _evidence(self, ids):
        if len(ids) != len(set(ids)):
            raise ContractError('duplicate_evidence')
        return [self.evidence.get_record(e) for e in ids]

    def propose(self, record: HypothesisRecord) -> str:
        record = decode_record(HypothesisRecord, asdict(record))
        if record.status != 'candidate' or record.hypothesis_id in self._versions:
            raise ContractError('hypothesis_proposal')
        self._evidence(record.support_ids + record.counter_ids)
        self._versions[record.hypothesis_id] = [deepcopy(record)]
        return record.hypothesis_id

    def get(self, hypothesis_id: str) -> HypothesisRecord:
        if hypothesis_id not in self._versions:
            raise ContractError('unknown_hypothesis')
        return deepcopy(self._versions[hypothesis_id][-1])

    def history(self, hypothesis_id: str) -> list[HypothesisRecord]:
        self.get(hypothesis_id)
        return deepcopy(self._versions[hypothesis_id])

    def decide(self, decision: GradeDecision) -> HypothesisRecord:
        decision = decode_record(GradeDecision, asdict(decision))
        record = self.get(decision.hypothesis_id)
        statuses = {'candidate': 'candidate', 'static_supported': 'supported', 'behavior_reproduced': 'behavior_reproduced', 'causal_supported': 'causal_supported', 'refuted': 'refuted', 'inconclusive': 'inconclusive'}
        if decision.grade not in statuses:
            raise ContractError('invalid_grade')
        self._evidence(decision.support_ids + decision.counter_ids)
        if decision.grade in ('static_supported', 'behavior_reproduced', 'causal_supported') and not decision.support_ids:
            raise ContractError('unsupported_grade')
        if decision.grade == 'refuted' and not decision.counter_ids:
            raise ContractError('unsupported_refutation')
        if decision.grade in ('behavior_reproduced', 'causal_supported'):
            predictions = [p for p in record.predictions if p.get('check_id') in decision.check_ids]
            if not decision.check_ids or len(set(decision.check_ids)) != len(decision.check_ids) or len(predictions) != len(decision.check_ids):
                raise ContractError('missing_prediction')
            for check_id in decision.check_ids:
                check = self.checks.get(check_id)
                if check is None or check.check_id != check_id:
                    raise ContractError('unknown_check')
                check = decode_record(CheckResult, asdict(check))
                prediction = next(p for p in predictions if p['check_id'] == check_id)
                normalized = classify_check(asdict(check), CheckSpec(check_id, prediction=prediction))
                counts = check.test_counts or {}
                executed = sum(counts.get(key, 0) for key in ('passed', 'failed', 'xfailed', 'xpassed'))
                if check.execution_status != 'completed' or normalized.observed_status not in ('passed', 'failed') or normalized.expectation_result != 'matches' or check.expectation_result != 'matches' or not check.environment_ref or executed <= 0 or counts.get('errors', 0) or counts.get('collection_errors', 0):
                    raise ContractError('unobserved_prediction')
            if not any(p.get('kind') == 'reproduction' for p in predictions):
                raise ContractError('missing_reproduction')
            if decision.grade == 'causal_supported':
                interventions = [p for p in predictions if p.get('kind') == 'intervention']
                distinguished = {alt for p in interventions for alt in p.get('distinguishes', []) if isinstance(alt, str)}
                if not interventions or len(decision.check_ids) < 2 or not record.causal_chain or record.gaps or not set(record.alternatives) <= distinguished:
                    raise ContractError('causal_experiment_missing')
        record.support_ids, record.counter_ids = list(decision.support_ids), list(decision.counter_ids)
        record.status = statuses[decision.grade]
        self._versions[record.hypothesis_id].append(deepcopy(record))
        return record
