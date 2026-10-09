"""Strict host-side decoding. Error text identifies fields, never input values."""
from __future__ import annotations

import math
import re
import types
from dataclasses import MISSING, asdict, fields, is_dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from . import records as r


class ContractError(ValueError):
    def __init__(self, code: str = "contract_invalid", field: str = ""):
        self.code, self.field = code, field
        super().__init__(f"{code}: {field}")


def validate_relative(value: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\\\x00\n\r:"):
        raise ContractError("forbidden_path", "path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in (".", "..") for p in value.split("/")) or path.as_posix() != value:
        raise ContractError("forbidden_path", "path")
    return value


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or value in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ContractError("contract_invalid", "identifier")
    return value


def _decode(hint, value, name):
    origin, args = get_origin(hint), get_args(hint)
    if hint is Any:
        return _json_value(value, name)
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _decode(option, value, name)
            except ContractError:
                pass
        raise ContractError(field=name)
    if origin is Literal:
        if not any(type(value) is type(a) and value == a for a in args):
            raise ContractError(field=name)
        return value
    if hint is type(None):
        if value is not None:
            raise ContractError(field=name)
        return None
    if is_dataclass(hint):
        return decode_record(hint, asdict(value) if is_dataclass(value) else value)
    if origin in (list, tuple):
        if not isinstance(value, (list, tuple)) or (origin is list and not isinstance(value, list)):
            raise ContractError(field=name)
        if origin is tuple and len(value) != len(args):
            raise ContractError(field=name)
        decoded = [_decode(args[0] if origin is list else args[i], item, name) for i, item in enumerate(value)]
        return decoded if origin is list else tuple(decoded)
    if origin is dict:
        if not isinstance(value, dict):
            raise ContractError(field=name)
        return {_decode(args[0], key, name): _decode(args[1], item, name) for key, item in value.items()}
    if hint is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ContractError(field=name)
        return float(value)
    if hint in (str, int, bool, bytes):
        if type(value) is not hint:
            raise ContractError(field=name)
        return value
    raise ContractError(field=name)


def _json_value(value, name, depth=0):
    if depth > 64:
        raise ContractError(field=name)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, name, depth+1) for item in value]
    if isinstance(value, dict) and all(type(key) is str for key in value):
        return {key: _json_value(item, name, depth+1) for key, item in value.items()}
    raise ContractError(field=name)


def decode_record(cls, raw: dict):
    if not isinstance(raw, dict):
        raise ContractError(field=cls.__name__)
    allowed = {f.name: f for f in fields(cls)}
    if set(raw) - set(allowed):
        raise ContractError(field=f"{cls.__name__}.extra_fields")
    hints, values = get_type_hints(cls), {}
    for name, definition in allowed.items():
        if name in raw:
            values[name] = _decode(hints[name], raw[name], name)
        elif definition.default is MISSING and definition.default_factory is MISSING:
            raise ContractError(field=name)
    record = cls(**values)
    if isinstance(record, r.Record) and record.schema_version != r.SCHEMA_VERSION:
        raise ContractError(field="schema_version")
    for name, value in asdict(record).items():
        if type(value) in (int, float) and name not in ("exit_code",) and (value < 0 or value > 2**63 - 1):
            raise ContractError(field=name)
    if isinstance(record, (r.Location, r.SymbolRecord)):
        validate_relative(record.file)
        if record.start < 1 or record.end < record.start:
            raise ContractError(field="line_range")
    if isinstance(record, r.FileEntry):
        validate_relative(record.path)
    if isinstance(record, r.TaskSpec):
        validate_identifier(record.run_id)
        if record.mode == "locate" and not record.symptom:
            raise ContractError(field="symptom")
    if isinstance(record, (r.BudgetProfile, r.Limits)) and record.wall_seconds <= 0:
        raise ContractError(field="wall_seconds")
    if isinstance(record, (r.ContextRequest, r.ReadArgs, r.IndexQuery, r.SearchArgs)):
        for name in ("max_bytes", "max_tokens", "limit", "max_results"):
            if hasattr(record, name) and getattr(record, name) <= 0:
                raise ContractError(field=name)
    return record


def validate_task(raw: dict) -> r.TaskSpec:
    return decode_record(r.TaskSpec, raw)


def _location(location: r.Location, state: r.InvestigationState):
    validate_relative(location.file)
    allowed = {f.path: f for f in state.scope.eligible} if state.scope else {}
    if location.file not in allowed:
        raise ContractError("forbidden_path", "location")
    if allowed[location.file].lines is not None and location.end > allowed[location.file].lines:
        raise ContractError(field="line_range")


def _evidence(ids: list[str], state: r.InvestigationState):
    ref = state.task.snapshot_ref
    for evidence_id in ids:
        item = state.evidence.get(evidence_id)
        if item is None or ref is None or item.snapshot_id != ref.snapshot_id or item.validity != "valid":
            raise ContractError("citation_invalid", "evidence_ids")
        if item.location:
            _location(item.location, state)


def validate_action(raw: dict, state: r.InvestigationState) -> r.ActionRequest:
    action = decode_record(r.ActionRequest, raw)
    validate_identifier(action.action_id)
    arg_types = {"read_context": r.ReadArgs, "query_index": r.QueryArgs, "search_text": r.SearchArgs, "run_check": r.CheckArgs, "call_model": r.ModelArgs}
    if not isinstance(action.typed_args, arg_types[action.action_type]):
        raise ContractError(field="typed_args")
    if action.action_type == "call_model" and action.requester_role != "coordinator":
        raise ContractError("forbidden_action", "call_model")
    if action.budget_reservation is not None:
        raise ContractError("forbidden_action", "budget_reservation")
    if action.hypothesis_id and action.hypothesis_id not in state.hypotheses:
        raise ContractError(field="hypothesis_id")
    if isinstance(action.typed_args, r.ReadArgs):
        if not action.typed_args.locations:
            raise ContractError(field="locations")
        for location in action.typed_args.locations:
            _location(location, state)
    if isinstance(action.typed_args, r.CheckArgs):
        check = action.typed_args.check
        if check.execution_profile != state.task.execution_profile:
            raise ContractError("forbidden_action", "execution_profile")
        for path in check.input_artifacts:
            validate_relative(path)
        if any(not s or s.startswith("-") or any(c in s for c in "\x00\n\r;") for s in check.selectors):
            raise ContractError("forbidden_action", "selectors")
    return action


def validate_proposal(raw: dict, state: r.InvestigationState) -> r.RoleProposal:
    proposal = decode_record(r.RoleProposal, raw)
    _evidence(proposal.cited_evidence_ids, state)
    for hypothesis in proposal.hypotheses:
        validate_identifier(hypothesis.hypothesis_id)
        if hypothesis.status != "candidate":
            raise ContractError(field="hypothesis.status")
        _evidence(hypothesis.support_ids + hypothesis.counter_ids, state)
    for action in proposal.proposed_actions:
        validate_action(asdict(action), state)
    return proposal


TERMINALS = {"completed", "budget_exhausted", "inconclusive", "blocked_environment", "failed", "cancelled"}
TRANSITIONS = {"received": {"indexing"}, "indexing": {"checking"}, "checking": {"investigating"}, "investigating": {"verifying", "reporting"}, "verifying": {"investigating", "reporting"}, "reporting": {"completed"}}


def validate_transition(before: str, after: str) -> None:
    valid = TRANSITIONS.get(before, set()) | (TERMINALS - {"completed"} if before in TRANSITIONS else set())
    if after not in valid:
        raise ContractError(field="state_transition")
