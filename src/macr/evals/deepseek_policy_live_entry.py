"""Separate, explicit DeepSeek pilot entry. Importing this module sends nothing.

The public offline runner remains fixture-only. A caller must present a frozen
batch, exact human-approved scope and an ephemeral Key reader to use this path.
The local ledger is a conservative dispatch gate, not a provider billing cap.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from macr.evals.deepseek_policy_batch import (PolicyBatchError, _check_drift,
    _load_batch, _locked, _registry, claim_policy_cell, record_policy_cell)
from macr.evals.deepseek_policy_live_scope import (LiveApproval, LiveScopeError,
    ValidatedLiveScope, acquire_runtime_inputs, validate_live_scope,
    write_log_policy)
from macr.evals.deepseek_policy_manifest import DEFAULT_BUDGET
from macr.evals.deepseek_policy_recovery import RecoveryError
from macr.evals.deepseek_policy_runner import ARM_REQUESTS, _run_policy_arm_core
from macr.evals.deepseek_policy_spend import (PRICE_SNAPSHOT, PolicySpendError,
    initialize_spend_ledger, mark_spend_unknown, reserve_spend, settle_spend,
    spend_state)
from macr.providers.base import ConversationRequest, ProviderError
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport
from macr.providers.failure_evidence import validate_failure_diagnostic


PRICE_SNAPSHOT_ID = "flash-peak-20261028"
_RESERVATION = Decimal("0.33")


class LiveEntryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise LiveEntryError(code)


def _scope(control_root, approval: LiveApproval, dataset_root) -> tuple[dict, ValidatedLiveScope]:
    _, batch = _load_batch(control_root)
    scope = validate_live_scope(approval, batch, dataset_root=dataset_root)
    _require(scope.price_snapshot_id == PRICE_SNAPSHOT_ID and
             scope.price_checked_on == PRICE_SNAPSHOT["checked_date"],
             "live_price_snapshot_mismatch")
    _require(batch["budget"] == DEFAULT_BUDGET, "live_budget_profile_invalid")
    return batch, scope


def prepare_approved_policy_batch(control_root, *, dataset_root, approval: LiveApproval,
                                  predecessor_control_root=None,
                                  predecessor_batch_sha256=None,
                                  recovery_confirmation=None) -> dict:
    """Freeze the approved 12×4×1 pilot's local policy before any Key ingress."""
    try:
        batch, scope = _scope(control_root, approval, dataset_root)
        recovery = batch.get("recovery_plan")
        if recovery is None:
            _require(recovery_confirmation is None and
                     scope.predecessor_batch_sha256 == predecessor_batch_sha256 and
                     (predecessor_control_root is None) ==
                     (predecessor_batch_sha256 is None), "live_predecessor_scope_mismatch")
        else:
            _require(recovery_confirmation is not None and
                     predecessor_control_root is None and predecessor_batch_sha256 is None,
                     "live_recovery_confirmation_missing")
            from macr.evals.deepseek_policy_recovery import validate_recovery_confirmation
            validate_recovery_confirmation(recovery, recovery_confirmation,
                                            batch_sha256=scope.batch_sha256)
        root = Path(control_root).absolute()
        with _locked(root):
            claims, outcomes = _registry(root, batch)
            _require(not claims and not outcomes, "live_batch_already_started")
            _check_drift(batch)
        write_log_policy(scope)
        return initialize_spend_ledger(control_root, batch_sha256=scope.batch_sha256,
            max_usd=scope.max_usd, max_requests=scope.max_requests,
            price_snapshot=PRICE_SNAPSHOT,
            predecessor_control_root=predecessor_control_root,
            predecessor_batch_sha256=predecessor_batch_sha256,
            recovery_plan=recovery, recovery_confirmation=recovery_confirmation)
    except (PolicyBatchError, PolicySpendError, LiveScopeError, RecoveryError) as error:
        raise LiveEntryError(error.code) from None


class _BoundWire:
    """Bind the actual outbound body and endpoint to a pre-reserved request."""

    def __init__(self, inner, *, endpoint: str, simulated: bool):
        self.inner, self.endpoint, self.simulated = inner, endpoint, simulated
        self.expected_digest: bytes | None = None
        self.live_attempts = 0

    @property
    def response_metadata(self):
        metadata = getattr(self.inner, "response_metadata", None)
        if metadata is None:
            return None
        try:
            return validate_failure_diagnostic(metadata)
        except (TypeError, ValueError):
            return None

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        if (endpoint != self.endpoint or type(body) is not bytes or
                self.expected_digest is None or
                sha256(body).digest() != self.expected_digest):
            raise ProviderError("model_request_invalid")
        if not self.simulated:
            self.live_attempts += 1
        return self.inner.post(endpoint, body, timeout_seconds, max_response_bytes)


class _ScriptedWire:
    """Only returns bounded literal responses; cannot dispatch to any endpoint."""

    def __init__(self, replies):
        _require(type(replies) is tuple and 1 <= len(replies) <= 3,
                 "live_script_invalid")
        self.replies = []
        for reply in replies:
            if type(reply) is TimeoutError:
                self.replies.append(reply)
            elif type(reply) is dict:
                try:
                    encoded = json.dumps(reply, allow_nan=False).encode("utf-8")
                    _require(len(encoded) <= 1024 * 1024, "live_script_invalid")
                    self.replies.append(json.loads(encoded))
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    raise LiveEntryError("live_script_invalid") from None
            else:
                raise LiveEntryError("live_script_invalid")

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        if not self.replies:
            raise ProviderError("model_transport")
        reply = self.replies.pop(0)
        if type(reply) is TimeoutError:
            raise TimeoutError() from None
        return reply


class _MeteredDeepSeekProvider(DeepSeekProvider):
    def __init__(self, *, control_root, scope: ValidatedLiveScope, cell_id: str,
                 arm: str, inner_transport, simulated: bool):
        wire = _BoundWire(inner_transport, endpoint=scope.endpoint,
                          simulated=simulated)
        super().__init__(base_url="https://api.deepseek.com", model=scope.model_id,
                         transport=wire, allow_remote=False)
        self.control_root, self.scope, self.cell_id, self.arm = (
            control_root, scope, cell_id, arm)
        self.decision = 0

    def converse(self, request: ConversationRequest):
        body = self.encode_conversation_request(request)
        try:
            wire = json.loads(body)
        except (ValueError, UnicodeError):
            raise ProviderError("model_request_invalid") from None
        expected_output = DEFAULT_BUDGET["max_total_output_tokens"] // ARM_REQUESTS[self.arm]
        if (wire.get("model") != self.scope.model_id or
                wire.get("max_tokens") != expected_output or
                wire.get("thinking") != {"type": "enabled"} or
                wire.get("reasoning_effort") != "high" or
                self.decision >= ARM_REQUESTS[self.arm]):
            raise ProviderError("model_request_invalid")
        decision = self.decision + 1
        try:
            reserve_spend(self.control_root, batch_sha256=self.scope.batch_sha256,
                cell_id=self.cell_id, decision=decision,
                max_output_tokens=wire["max_tokens"])
        except PolicySpendError as error:
            raise ProviderError(error.code, failure_kind="invalid_contract") from None
        self.decision = decision
        self.transport.expected_digest = sha256(body).digest()
        try:
            try:
                response = super().converse(request)
            except ProviderError as error:
                if error.response is None:
                    # Even a local timeout does not prove that remote billing stopped.
                    try:
                        mark_spend_unknown(self.control_root,
                            batch_sha256=self.scope.batch_sha256,
                            cell_id=self.cell_id, decision=decision)
                    except PolicySpendError:
                        pass  # An unresolved reservation still blocks the batch.
                else:
                    self._settle(decision, error.response)
                raise
            self._settle(decision, response)
            return response
        finally:
            self.transport.expected_digest = None

    def _settle(self, decision: int, response) -> None:
        try:
            outcome = settle_spend(self.control_root,
                batch_sha256=self.scope.batch_sha256,
                cell_id=self.cell_id, decision=decision,
                raw_usage=response.raw_usage)
        except PolicySpendError:
            raise ProviderError("model_token_accounting_unavailable",
                                response=response) from None
        if outcome["state"] != "settled":
            raise ProviderError("model_token_accounting_unavailable",
                                response=response)


def run_next_approved_policy_cell(control_root, *, dataset_root, task_id, repeat,
                                  arm, out, approval: LiveApproval, key_reader,
                                  scripted_replies=None) -> dict:
    """Run one approved cell; literal scripted replies permit no-network tests.

    The default transport is capable of a real POST, so callers must obtain the
    separate user approval recorded by LiveApproval before invoking this entry.
    """
    try:
        batch, scope = _scope(control_root, approval, dataset_root)
        state = spend_state(control_root)
        _require(state["batch_sha256"] == scope.batch_sha256 and
                 state["price_snapshot"] == PRICE_SNAPSHOT and
                 Decimal(state["max_usd"]) == Decimal(scope.max_usd) and
                 state["max_requests"] == scope.max_requests,
                 "live_spend_policy_mismatch")
        _require(state["state"] == "open", "live_batch_spend_halted")
        if scripted_replies is None:
            _require(scope.predecessor_batch_sha256 is not None and
                     type(state.get("carried_request_count")) is int and
                     state["carried_request_count"] > 0 and
                     state.get("carried_batch_sha256") == scope.predecessor_batch_sha256,
                     "live_cumulative_budget_missing")
        _require(state["request_count"] < scope.max_requests,
                 "live_request_ceiling")
        _require(Decimal(state["spent_usd"]) +
                 Decimal(state["reserved_usd"]) + _RESERVATION <=
                 Decimal(scope.max_usd), "live_budget_exhausted")
        root = Path(control_root).absolute()
        with _locked(root):
            claims, outcomes = _registry(root, batch)
            _require(set(claims) == set(outcomes), "live_cell_unresolved")
            _require(len(claims) < len(batch["cells"]), "live_batch_complete")
            next_cell = batch["cells"][len(claims)]
            _require((task_id, repeat, arm, str(Path(out).absolute())) ==
                     (next_cell["task_id"], next_cell["repeat"], next_cell["arm"],
                      next_cell["out"]), "live_cell_order_invalid")
            _check_drift(batch)
        scripted = _ScriptedWire(scripted_replies) if scripted_replies is not None else None
        _, key = acquire_runtime_inputs(approval, batch, dataset_root=dataset_root,
                                        key_reader=key_reader)
        if scripted is None:
            inner, simulated = _ApprovedTransport(key), False
        else:
            inner, simulated = scripted, True
        provider = _MeteredDeepSeekProvider(control_root=control_root, scope=scope,
            cell_id=next_cell["cell_id"], arm=arm, inner_transport=inner,
            simulated=simulated)
        ticket = claim_policy_cell(control_root, task_id=task_id, repeat=repeat,
                                   arm=arm, out=out)
        result = _run_policy_arm_core(ticket["manifest_dir"],
            dataset_root=dataset_root, arm=arm, provider=provider, out=ticket["out"],
            provenance="approved_live_fake" if simulated else "approved_live")
        outcome = record_policy_cell(control_root, ticket)
        return {"result": result, "outcome": outcome}
    except (PolicyBatchError, PolicySpendError, LiveScopeError) as error:
        raise LiveEntryError(error.code) from None
