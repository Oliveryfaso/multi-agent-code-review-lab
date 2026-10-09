"""Accounting only. Dispatcher must persist reservations before external work."""
from copy import deepcopy
from dataclasses import asdict
from time import monotonic, time
from .records import ActionRequest, ActionUsage, BudgetProfile, BudgetView, Reservation, CheckArgs
from .validation import ContractError, decode_record, validate_identifier

FIELDS = ('tool_calls', 'model_calls', 'input_tokens', 'output_tokens', 'checks', 'bytes_read')
LIMITS = {'tool_calls': 'tool_calls', 'model_calls': 'model_calls', 'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens', 'checks': 'dynamic_checks'}

def scan_profile():
    return BudgetProfile('scan-default-v1', 1500, 160, 16, 128000, 16000, 8, 90, 1024*1024)

def total(usages):
    result = ActionUsage()
    for usage in usages:
        for name in FIELDS:
            setattr(result, name, getattr(result, name) + getattr(usage, name))
    return result

class BudgetLedger:
    def __init__(self, profile=None, *, clock=monotonic, wall_clock=None):
        self._view = BudgetView(profile=decode_record(BudgetProfile, asdict(profile or BudgetProfile())))
        self.clock, self._last = clock, clock()
        self.wall_clock = wall_clock or (time if clock is monotonic else clock)
        self._active = True
        self._view.active_intervals.append((self._last, None))

    def _tick(self):
        now = self.clock()
        if now < self._last:
            raise ContractError('clock_invalid')
        if self._active:
            self._view.used.active_seconds += now - self._last
        self._last = now

    def _reserved(self):
        return total(r.upper_bounds for r in self._view.reservations.values() if r.state == 'reserved')

    def _exceeded(self, extra=None):
        summed = total([self._view.used, self._reserved(), extra or ActionUsage()])
        return self._view.bounds_exceeded or self._view.used.active_seconds >= self._view.profile.wall_seconds or any(getattr(summed, field) > getattr(self._view.profile, limit) for field, limit in LIMITS.items())

    def reserve(self, action: ActionRequest, upper: ActionUsage) -> Reservation:
        self._tick()
        validate_identifier(action.action_id)
        action = decode_record(ActionRequest, asdict(action))
        upper = decode_record(ActionUsage, asdict(upper))
        if not self._active or action.action_id in self._view.reservations or upper.usage_kind == 'unknown':
            raise ContractError('reservation_invalid')
        if action.action_type == 'call_model':
            if action.requester_role != 'coordinator' or upper.model_calls != 1 or upper.tool_calls or upper.checks or upper.input_tokens <= 0 or upper.output_tokens <= 0:
                raise ContractError('model_upper_bound_missing')
        elif upper.tool_calls != 1 or upper.model_calls or upper.checks != int(action.action_type == 'run_check'):
            raise ContractError('action_usage_invalid')
        if action.action_type == 'run_check':
            if not isinstance(action.typed_args, CheckArgs) or action.typed_args.check.limits is None:
                raise ContractError('check_limits_missing')
            limits = action.typed_args.check.limits
            if limits.wall_seconds > self._view.profile.check_wall_seconds or limits.output_bytes > self._view.profile.check_output_bytes or min(limits.cpu, limits.memory_bytes, limits.pids, limits.wall_seconds, limits.output_bytes) <= 0:
                raise ContractError('check_limits_invalid')
        if self._exceeded(upper):
            raise ContractError('budget_exhausted')
        reservation = Reservation(action.action_id, deepcopy(upper))
        self._view.reservations[action.action_id] = reservation
        return deepcopy(reservation)

    def settle(self, action_id: str, actual: ActionUsage) -> BudgetView:
        self._tick()
        reservation = self._view.reservations.get(action_id)
        actual = decode_record(ActionUsage, asdict(actual))
        if reservation is None or reservation.state != 'reserved':
            raise ContractError('settlement_conflict')
        if actual.usage_kind == 'unknown':
            charged = deepcopy(reservation.upper_bounds)
            for name in FIELDS:
                setattr(charged, name, max(getattr(charged, name), getattr(actual, name)))
            charged.usage_kind = 'estimated'
            reservation.state = 'unknown'
        else:
            charged = deepcopy(actual)
            # A dispatched invocation is charged even if the adapter reports missing call counts.
            charged.tool_calls = max(charged.tool_calls, reservation.upper_bounds.tool_calls)
            charged.model_calls = max(charged.model_calls, reservation.upper_bounds.model_calls)
            charged.checks = max(charged.checks, reservation.upper_bounds.checks)
            reservation.state = 'settled'
        if any(getattr(charged, name) > getattr(reservation.upper_bounds, name) for name in FIELDS):
            self._view.bounds_exceeded = True
        for name in FIELDS:
            setattr(self._view.used, name, getattr(self._view.used, name) + getattr(charged, name))
        if charged.usage_kind != 'measured':
            self._view.used.usage_kind = 'estimated'
        return self.view()

    def view(self) -> BudgetView:
        self._tick()
        self._view.reserved = self._reserved()
        self._view.captured_at = self.wall_clock()
        return deepcopy(self._view)

    def pause(self):
        self._tick()
        if any(r.state in ('reserved', 'unknown') for r in self._view.reservations.values()):
            raise ContractError('inflight_pause_forbidden')
        if self._active:
            start, _ = self._view.active_intervals[-1]
            self._view.active_intervals[-1] = (start, self._last)
        self._active = False

    def resume(self):
        self._tick()
        if not self._active:
            self._view.active_intervals.append((self._last, None))
        self._active = True

    @classmethod
    def restore(cls, view: BudgetView, *, clock=monotonic, wall_clock=None):
        view = decode_record(BudgetView, asdict(view))
        ledger = cls(view.profile, clock=clock, wall_clock=wall_clock)
        if any(key != r.action_id for key, r in view.reservations.items()) or asdict(total(r.upper_bounds for r in view.reservations.values() if r.state == 'reserved')) != asdict(view.reserved):
            raise ContractError('budget_state_invalid')
        ledger._view = deepcopy(view)
        ledger._active = bool(view.active_intervals and view.active_intervals[-1][1] is None)
        if ledger._active:
            # Count elapsed active waiting across process clock epochs, never paused downtime.
            if view.captured_at is None or ledger.wall_clock() < view.captured_at:
                raise ContractError('clock_invalid')
            ledger._view.used.active_seconds += ledger.wall_clock() - view.captured_at
            start, _ = ledger._view.active_intervals[-1]
            ledger._view.active_intervals[-1] = (start, start)
            ledger._view.active_intervals.append((ledger._last, None))
        return ledger
