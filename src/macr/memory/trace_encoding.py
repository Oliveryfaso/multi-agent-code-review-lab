"""Bound projected legacy trace summaries before persistence."""
import json
import math

from macr.schemas import Trace
from macr.memory.trace_summary import project_trace_summary

MAX_TRACE_BYTES = 1024 * 1024
MAX_TRACE_NODES = 32768
MAX_TRACE_DEPTH = 64
MAX_INTEGER_BITS = 4096


def _bounded_payload(payload: dict) -> dict:
    """Copy while checking; count each occurrence and current-path cycles.

    No second traversal of mutable input or arbitrary deepcopy hook is used.
    """
    nodes = 0
    text_bytes = 0
    active = set()

    def visit(value, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        if nodes > MAX_TRACE_NODES:
            raise ValueError('trace_limit_nodes')
        if depth > MAX_TRACE_DEPTH:
            raise ValueError('trace_limit_depth')
        kind = type(value)
        if value is None or kind is bool:
            return value
        if kind is str:
            # Every UTF-8 character takes at least one byte; reject before a
            # potentially large encoding allocation, then account exact bytes.
            if len(value) > MAX_TRACE_BYTES - text_bytes:
                raise ValueError('trace_limit_bytes')
            try:
                text_bytes += len(value.encode('utf-8'))
            except UnicodeError:
                raise ValueError('trace_value_unsupported') from None
            if text_bytes > MAX_TRACE_BYTES:
                raise ValueError('trace_limit_bytes')
            return value
        if kind is int:
            if value.bit_length() > MAX_INTEGER_BITS:
                raise ValueError('trace_limit_integer_bits')
            return value
        if kind is float:
            if not math.isfinite(value):
                raise ValueError('trace_value_nonfinite')
            return value
        if kind not in (dict, list, tuple):
            raise ValueError('trace_value_unsupported')
        identity = id(value)
        if identity in active:
            raise ValueError('trace_cycle')
        active.add(identity)
        try:
            if kind is dict:
                copied = {}
                for key, item in value.items():
                    if not (key is None or type(key) is str or type(key) is int or
                            type(key) is float or type(key) is bool):
                        raise ValueError('trace_value_unsupported')
                    copied[visit(key, depth + 1)] = visit(item, depth + 1)
                return copied
            else:
                return [visit(item, depth + 1) for item in value]
        finally:
            active.remove(identity)

    if type(payload) is not dict:
        raise ValueError('trace_value_unsupported')
    return visit(payload, 0)


def _encode_payload(payload: dict) -> bytes:
    payload = _bounded_payload(payload)
    data = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
    for chunk in encoder.iterencode(payload):
        remaining = MAX_TRACE_BYTES - len(data)
        if len(chunk) > remaining:
            raise ValueError('trace_limit_bytes')
        encoded = chunk.encode('utf-8')
        if len(encoded) > remaining:
            raise ValueError('trace_limit_bytes')
        data.extend(encoded)
    return bytes(data)


def encode_trace(trace: Trace) -> bytes:
    """Project before bounded copy/JSON; return safe bytes or fail before writing."""
    return _encode_payload(project_trace_summary(trace))
