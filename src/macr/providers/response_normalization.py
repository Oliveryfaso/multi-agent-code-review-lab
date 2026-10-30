"""Canonicalize incoming provider messages without retaining extension metadata."""
from __future__ import annotations

import json
import math
import re


_ERROR_CODES = frozenset({
    'message_type', 'required_field', 'role_invalid', 'content_type',
    'reasoning_type', 'tool_calls_type', 'tool_call_count', 'tool_call_shape',
    'tool_call_id', 'tool_call_duplicate_id', 'tool_call_type',
    'tool_function_shape', 'tool_function_name', 'tool_arguments_type',
    'tool_arguments_json', 'duplicate_json_key', 'response_json_invalid',
    'metadata_limit',
})
_FUNCTION_NAME = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_MAX_TOOL_CALLS = 64
_MAX_ARGUMENT_BYTES = 64 * 1024
_TOOL_NAME_REASONS = frozenset({'type', 'format', 'not_declared'})


class ResponseNormalizationError(ValueError):
    def __init__(self, code: str, *, tool_name_reason: str | None = None):
        if type(code) is not str or code not in _ERROR_CODES:
            raise ValueError('response_normalization_code_invalid')
        if tool_name_reason is not None and (code != 'tool_function_name' or
                type(tool_name_reason) is not str or tool_name_reason not in _TOOL_NAME_REASONS):
            raise ValueError('response_normalization_reason_invalid')
        self.code = code
        self.tool_name_reason = tool_name_reason
        super().__init__(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ResponseNormalizationError('duplicate_json_key')
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError()
    return number


def _reject_constant(value):
    raise ValueError()


def _decode_object(raw: str, invalid_code: str) -> dict:
    invalid = False
    try:
        result = json.loads(raw, object_pairs_hook=_unique_object,
                            parse_float=_finite_float, parse_constant=_reject_constant)
    except ResponseNormalizationError:
        raise
    except (ValueError, RecursionError):
        invalid = True
    # Raise outside the handler so parse exceptions cannot retain raw response text.
    if invalid or type(result) is not dict:
        raise ResponseNormalizationError(invalid_code)
    return result


def decode_provider_json(raw: bytes | str) -> dict:
    """Decode one provider JSON object, rejecting ambiguity at every nesting level."""
    if type(raw) is bytes:
        invalid = False
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            invalid = True
        if invalid:
            raise ResponseNormalizationError('response_json_invalid')
    elif type(raw) is not str:
        raise ResponseNormalizationError('response_json_invalid')
    return _decode_object(raw, 'response_json_invalid')


def _arguments_are_bounded(arguments) -> bool:
    if type(arguments) is not str or len(arguments) > _MAX_ARGUMENT_BYTES:
        return False
    try:
        return len(arguments.encode('utf-8')) <= _MAX_ARGUMENT_BYTES
    except UnicodeEncodeError:
        return False


def normalize_assistant_message(message, *, tool_names: set[str]) -> dict:
    """Copy only replayable assistant fields after validating action-bearing data."""
    if type(message) is not dict:
        raise ResponseNormalizationError('message_type')
    if 'role' not in message or 'content' not in message:
        raise ResponseNormalizationError('required_field')
    if type(message['role']) is not str or message['role'] != 'assistant':
        raise ResponseNormalizationError('role_invalid')
    content = message['content']
    if content is not None and type(content) is not str:
        raise ResponseNormalizationError('content_type')
    canonical = {'role': 'assistant', 'content': content}
    if 'reasoning_content' in message:
        reasoning = message['reasoning_content']
        if reasoning is not None and type(reasoning) is not str:
            raise ResponseNormalizationError('reasoning_type')
        canonical['reasoning_content'] = reasoning
    if 'tool_calls' not in message:
        return canonical
    calls = message['tool_calls']
    if type(calls) is not list:
        raise ResponseNormalizationError('tool_calls_type')
    if len(calls) > _MAX_TOOL_CALLS:
        raise ResponseNormalizationError('tool_call_count')
    canonical_calls = []
    ids = set()
    for call in calls:
        if type(call) is not dict:
            raise ResponseNormalizationError('tool_call_shape')
        if 'id' not in call or 'type' not in call or 'function' not in call:
            raise ResponseNormalizationError('required_field')
        identifier = call['id']
        if type(identifier) is not str or not 1 <= len(identifier) <= 256:
            raise ResponseNormalizationError('tool_call_id')
        if identifier in ids:
            raise ResponseNormalizationError('tool_call_duplicate_id')
        ids.add(identifier)
        if type(call['type']) is not str or call['type'] != 'function':
            raise ResponseNormalizationError('tool_call_type')
        function = call['function']
        if type(function) is not dict:
            raise ResponseNormalizationError('tool_function_shape')
        if 'name' not in function or 'arguments' not in function:
            raise ResponseNormalizationError('required_field')
        name, arguments = function['name'], function['arguments']
        if type(name) is not str:
            raise ResponseNormalizationError('tool_function_name', tool_name_reason='type')
        if not _FUNCTION_NAME.fullmatch(name):
            raise ResponseNormalizationError('tool_function_name', tool_name_reason='format')
        if name not in tool_names:
            raise ResponseNormalizationError('tool_function_name', tool_name_reason='not_declared')
        if not _arguments_are_bounded(arguments):
            raise ResponseNormalizationError('tool_arguments_type')
        _decode_object(arguments, 'tool_arguments_json')
        canonical_calls.append({'id': identifier, 'type': 'function',
                                'function': {'name': name, 'arguments': arguments}})
    canonical['tool_calls'] = canonical_calls
    return canonical
