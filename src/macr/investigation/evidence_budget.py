"""Run-local accounting; limits are configurable defaults, never read authority."""
from __future__ import annotations

import time

from .evidence_profiles import EvidenceProfile
from .validation import ContractError


class BudgetExceeded(ContractError):
    def __init__(self, field, stage, limit, observed):
        super().__init__('scope_limit', field)
        self.stage, self.limit, self.observed = stage, limit, observed

    def detail(self):
        return f'{self.field};stage={self.stage};limit={self.limit};observed={self.observed}'


class EvidenceBudget:
    def __init__(self, profile: EvidenceProfile, *, deadline=None, cancelled=None):
        profile.to_dict()
        self.profile = profile
        self.started = time.monotonic()
        self.deadline = self.started + profile.active_seconds if deadline is None else deadline
        self.cancelled = cancelled
        self.usage = {}
        self.anchors = {}
        self.stage = 'initial'

    def check(self, stage):
        self.stage = stage
        if self.cancelled and self.cancelled():
            raise ContractError('cancelled', stage)
        elapsed = time.monotonic() - self.started
        self.usage['active_seconds'] = elapsed
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded('active_seconds', stage, self.profile.active_seconds, elapsed)

    def charge(self, field, amount, stage):
        self.check(stage)
        if type(amount) is not int or amount < 0:
            raise ContractError(field='budget_charge')
        self.observe(field, self.usage.get(field, 0) + amount, stage)

    def observe(self, field, value, stage):
        self.check(stage)
        limit = getattr(self.profile, field)
        self.usage[field] = value
        if value > limit:
            raise BudgetExceeded(field, stage, limit, value)

    def minimum(self, field, value, stage):
        self.check(stage)
        self.usage[field] = value
        limit = getattr(self.profile, field)
        if value < limit:
            raise BudgetExceeded(field, stage, limit, value)
