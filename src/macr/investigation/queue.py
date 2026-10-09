"""Deterministic two-lane scheduler with explicit retained state."""
import math
from copy import deepcopy
from dataclasses import asdict
from .records import ActionRequest, ActionResult, BudgetView
from .validation import ContractError, decode_record

def action_kind(action):
    return 'model' if action.action_type == 'call_model' else 'check' if action.action_type == 'run_check' else 'read'

class InvestigationQueue:
    def __init__(self):
        self._pending = []
        self._seen, self._observed = set(), set()
        self._used = {lane: {'model': 0, 'read': 0, 'check': 0} for lane in ('breadth', 'depth')}
        self._uninformative = 0
        self.released_lanes = set()

    def push(self, action: ActionRequest, lane: str, priority: float):
        action = decode_record(ActionRequest, asdict(action))
        if lane not in self._used or type(priority) not in (int, float) or not math.isfinite(priority) or action.action_id in self._seen:
            raise ContractError('queue_entry_invalid')
        self._seen.add(action.action_id)
        self._pending.append({'action': action, 'lane': lane, 'priority': priority, 'order': len(self._seen)})
        self.released_lanes.discard(lane)

    def pop(self, budget: BudgetView):
        if not self._pending or budget.bounds_exceeded or budget.used.active_seconds >= budget.profile.wall_seconds:
            return None
        available = []
        for entry in self._pending:
            kind = action_kind(entry['action'])
            if kind == 'model':
                allowed = budget.used.model_calls + budget.reserved.model_calls < budget.profile.model_calls
            else:
                allowed = budget.used.tool_calls + budget.reserved.tool_calls < budget.profile.tool_calls
                if kind == 'check':
                    allowed = allowed and budget.used.checks + budget.reserved.checks < budget.profile.dynamic_checks
            if allowed:
                available.append(entry)
        if not available:
            return None
        candidates = sorted(available, key=lambda e: (-e['priority'], e['order']))
        selected = candidates[0]
        kind = action_kind(selected['action'])
        limit = budget.profile.model_calls if kind == 'model' else budget.profile.tool_calls
        present = {entry['lane'] for entry in candidates if action_kind(entry['action']) == kind}
        self.released_lanes.update(set(self._used) - present)
        if len(present) == 2 and kind != 'check':
            other = 'depth' if selected['lane'] == 'breadth' else 'breadth'
            if self._used[selected['lane']][kind] >= math.floor(limit * .75):
                selected = next(entry for entry in candidates if entry['lane'] == other and action_kind(entry['action']) == kind)
        self._pending.remove(selected)
        self._used[selected['lane']][kind] += 1
        return deepcopy(selected['action'])

    def observe(self, action: ActionRequest, result: ActionResult):
        result = decode_record(ActionResult, asdict(result))
        if action.action_id != result.action_id or action.action_id not in self._seen or action.action_id in self._observed or any(e['action'].action_id == action.action_id for e in self._pending):
            raise ContractError('queue_result_invalid')
        self._observed.add(action.action_id)
        self._uninformative = 0 if result.new_information else self._uninformative + 1

    def should_stop(self):
        return self._uninformative >= 3 and not self._pending and self._seen == self._observed

    def snapshot(self) -> list[dict]:
        return [{'queue_meta': {'used': deepcopy(self._used), 'seen': sorted(self._seen), 'observed': sorted(self._observed), 'uninformative': self._uninformative, 'released': sorted(self.released_lanes)}}] + [{'action': asdict(e['action']), 'lane': e['lane'], 'priority': e['priority'], 'order': e['order']} for e in self._pending]

    @classmethod
    def restore(cls, entries: list[dict]):
        queue = cls()
        if not entries:
            return queue
        try:
            meta = entries[0]['queue_meta']
            if set(entries[0]) != {'queue_meta'} or set(meta) != {'used', 'seen', 'observed', 'uninformative', 'released'} or set(meta['used']) != {'breadth', 'depth'}:
                raise ContractError('queue_state_invalid')
            for counters in meta['used'].values():
                if set(counters) != {'model', 'read', 'check'} or any(type(n) is not int or n < 0 for n in counters.values()):
                    raise ContractError('queue_state_invalid')
            if any(not isinstance(meta[key], list) or any(not isinstance(x, str) for x in meta[key]) or len(set(meta[key])) != len(meta[key]) for key in ('seen', 'observed', 'released')) or type(meta['uninformative']) is not int or meta['uninformative'] < 0:
                raise ContractError('queue_state_invalid')
            queue._used, queue._seen, queue._observed = deepcopy(meta['used']), set(meta['seen']), set(meta['observed'])
            queue._uninformative, queue.released_lanes = meta['uninformative'], set(meta['released'])
            if not queue._observed <= queue._seen or not queue.released_lanes <= {'breadth', 'depth'}:
                raise ContractError('queue_state_invalid')
            for entry in entries[1:]:
                action = decode_record(ActionRequest, entry['action'])
                if set(entry) != {'action', 'lane', 'priority', 'order'} or type(entry['priority']) not in (int, float) or not math.isfinite(entry['priority']) or type(entry['order']) is not int or entry['order'] <= 0:
                    raise ContractError('queue_state_invalid')
                if action.action_id not in queue._seen or action.action_id in queue._observed or entry['lane'] not in queue._used:
                    raise ContractError('queue_state_invalid')
                queue._pending.append({**deepcopy(entry), 'action': action})
            if len({e['action'].action_id for e in queue._pending}) != len(queue._pending):
                raise ContractError('queue_state_invalid')
            if sum(sum(c.values()) for c in queue._used.values()) + len(queue._pending) != len(queue._seen):
                raise ContractError('queue_state_invalid')
        except (KeyError, TypeError):
            raise ContractError('queue_state_invalid') from None
        return queue
