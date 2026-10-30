"""Bounded failure metadata, never response text or settlement evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import re

SCHEMA = 'macr.provider.failure.v1'
MAX_DIAGNOSTIC_BYTES = 4096
STAGES = frozenset({'http_status', 'transport', 'json_decode', 'response_envelope',
    'assistant_message', 'finish_reason', 'usage', 'response_contract',
    'finish_consistency'})
_VALIDATION_ERRORS = frozenset({'message_type', 'required_field', 'role_invalid',
    'content_type', 'reasoning_type', 'tool_calls_type', 'tool_call_count',
    'tool_call_shape', 'tool_call_id', 'tool_call_duplicate_id', 'tool_call_type',
    'tool_function_shape', 'tool_function_name', 'tool_arguments_type',
    'tool_arguments_json', 'duplicate_json_key', 'response_json_invalid',
    'metadata_limit'})
_KINDS = frozenset({'missing', 'null', 'object', 'array', 'string', 'integer',
                    'boolean', 'number', 'other'})
_KIND_FIELDS = frozenset({'root_kind', 'choices_kind', 'message_kind', 'role_kind',
    'content_kind', 'reasoning_kind', 'tool_calls_kind', 'first_tool_arguments_kind',
    'first_tool_call_kind', 'first_tool_id_kind', 'first_tool_type_kind',
    'first_tool_function_kind', 'first_tool_name_kind', 'first_tool_index_kind',
    'usage_kind'})
_COUNT_FIELDS = frozenset({'choices_count', 'tool_calls_count',
                          'message_extra_fields', 'tool_call_extra_fields'})
_BOOL_FIELDS = frozenset({'request_id_present', 'fingerprint_present'})
_COUNTERS = frozenset({'prompt_tokens', 'completion_tokens', 'total_tokens',
    'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'cached_tokens',
    'reasoning_tokens'})
_FINISH = frozenset({'stop', 'tool_calls', 'length', 'content_filter',
    'insufficient_system_resource', 'aborted', 'missing', 'other', 'invalid_type'})
_MODEL = frozenset({'deepseek-flash', 'deepseek-v4-pro', 'missing', 'other', 'invalid_type'})
_REQUEST_ID = re.compile(r'(?:chatcmpl-)?(?:[0-9a-fA-F]{32,64}|'
    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\Z')
_MISSING = object()
_TOOL_NAME_REASONS = frozenset({'type', 'format', 'not_declared'})
_TOOL_SETS = frozenset({'none', 'terminal_only', 'other'})


def _require(condition):
    if not condition:
        raise ValueError('provider_failure_diagnostic_invalid')


def validate_failure_diagnostic(value) -> dict:
    """Reject unknown keys/types; return an independent, fixed-schema copy."""
    allowed = ({'schema', 'stage', 'http_status', 'response_bytes', 'request_id',
                'model', 'finish_reason', 'usage_counters', 'usage_counter_types',
                'validation_error', 'tool_name_reason', 'declared_tool_set', 'policy_decision'} |
               _KIND_FIELDS | _COUNT_FIELDS | _BOOL_FIELDS)
    _require(type(value) is dict and {'schema', 'stage'} <= set(value) <= allowed)
    _require(value['schema'] == SCHEMA and type(value['stage']) is str and
             value['stage'] in STAGES)
    for key, item in value.items():
        if key in _KIND_FIELDS:
            _require(type(item) is str and item in _KINDS)
        elif key in _COUNT_FIELDS:
            _require(type(item) is int and 0 <= item <= 1_000_000)
        elif key in _BOOL_FIELDS:
            _require(type(item) is bool)
        elif key == 'validation_error':
            _require(type(item) is str and item in _VALIDATION_ERRORS)
        elif key == 'tool_name_reason':
            _require(type(item) is str and item in _TOOL_NAME_REASONS and
                     value.get('validation_error') == 'tool_function_name')
        elif key == 'declared_tool_set':
            _require(type(item) is str and item in _TOOL_SETS)
        elif key == 'policy_decision':
            _require(type(item) is int and 1 <= item <= 3)
        elif key == 'http_status':
            _require(type(item) is int and 100 <= item <= 599)
        elif key == 'response_bytes':
            _require(type(item) is int and 0 <= item <= 2 ** 31 - 1)
        elif key == 'request_id':
            _require(type(item) is str and _REQUEST_ID.fullmatch(item) is not None and
                     value.get('request_id_present') is True)
        elif key in ('model', 'finish_reason'):
            _require(type(item) is str and item in (_MODEL if key == 'model' else _FINISH))
        elif key in ('usage_counters', 'usage_counter_types'):
            _require(type(item) is dict and set(item) <= _COUNTERS)
            for counter in item.values():
                if key == 'usage_counters':
                    _require(type(counter) is int and 0 <= counter <= 2 ** 63 - 1)
                else:
                    _require(type(counter) is str and counter in _KINDS)
    try:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False,
                             separators=(',', ':')).encode('ascii')
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError('provider_failure_diagnostic_invalid') from None
    _require(len(encoded) <= MAX_DIAGNOSTIC_BYTES)
    return deepcopy(value)


def _kind(value):
    if value is _MISSING:
        return 'missing'
    return {type(None): 'null', dict: 'object', list: 'array', str: 'string',
            int: 'integer', bool: 'boolean', float: 'number'}.get(type(value), 'other')


def http_failure_metadata(stage, *, status=None, request_id=None,
                          response_bytes=None, secret=None) -> dict:
    """Allowlist a single HTTP correlation header; never copy all headers."""
    result = {'schema': SCHEMA, 'stage': stage,
              'request_id_present': request_id is not None}
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    if type(response_bytes) is int and 0 <= response_bytes <= 2 ** 31 - 1:
        result['response_bytes'] = response_bytes
    echo = False
    if type(request_id) is str and type(secret) is str and secret:
        token = request_id.removeprefix('chatcmpl-').casefold()
        credential = secret.casefold()
        echo = any(credential in candidate or candidate in credential
                   for candidate in (request_id.casefold(), token))
    if (type(request_id) is str and _REQUEST_ID.fullmatch(request_id) is not None and not echo):
        result['request_id'] = request_id
    return validate_failure_diagnostic(result)


def make_failure_diagnostic(stage, *, data=_MISSING, metadata=None,
                            validation_error=None, tool_name_reason=None,
                            declared_tool_set=None) -> dict:
    """Extract only fixed field types/counts and official integer counters.

    Diagnostic usage counters are untrusted observations, never settlement evidence.
    Body IDs are presence-only; exact IDs can only arrive via filtered HTTP metadata.
    Only the known extension key ``index`` is identified; extension values and
    arbitrary extension names are never retained.
    """
    result = {'schema': SCHEMA, 'stage': stage}
    if validation_error is not None:
        _require(type(validation_error) is str and validation_error in _VALIDATION_ERRORS)
        result['validation_error'] = validation_error
    if tool_name_reason is not None:
        result['tool_name_reason'] = tool_name_reason
    if declared_tool_set is not None:
        result['declared_tool_set'] = declared_tool_set
    if metadata is not None:
        try:
            safe = validate_failure_diagnostic(metadata)
        except ValueError:
            safe = {}
        for name in ('http_status', 'response_bytes', 'request_id_present', 'request_id'):
            if name in safe:
                result[name] = safe[name]
    if data is _MISSING:
        return validate_failure_diagnostic(result)
    result['root_kind'] = _kind(data)
    if type(data) is not dict:
        return validate_failure_diagnostic(result)
    result['request_id_present'] = result.get('request_id_present', False) or 'id' in data
    result['fingerprint_present'] = 'system_fingerprint' in data
    model = data.get('model', _MISSING)
    result['model'] = ('missing' if model is _MISSING else 'invalid_type' if type(model) is not str
                       else model if model in _MODEL else 'other')
    choices = data.get('choices', _MISSING)
    result['choices_kind'] = _kind(choices)
    if type(choices) is list:
        result['choices_count'] = min(len(choices), 1_000_000)
        choice = choices[0] if choices and type(choices[0]) is dict else {}
        finish = choice.get('finish_reason', _MISSING)
        result['finish_reason'] = ('missing' if finish is _MISSING else
            'invalid_type' if type(finish) is not str else finish if finish in _FINISH else 'other')
        message = choice.get('message', _MISSING)
        result['message_kind'] = _kind(message)
        if type(message) is dict:
            result.update(role_kind=_kind(message.get('role', _MISSING)),
                content_kind=_kind(message.get('content', _MISSING)),
                reasoning_kind=_kind(message.get('reasoning_content', _MISSING)),
                message_extra_fields=min(len(set(message) -
                    {'role', 'content', 'reasoning_content', 'tool_calls'}), 1_000_000))
            calls = message.get('tool_calls', _MISSING)
            result['tool_calls_kind'] = _kind(calls)
            if type(calls) is list:
                result['tool_calls_count'] = min(len(calls), 1_000_000)
                observed_first = calls[0] if calls else _MISSING
                first = observed_first if type(observed_first) is dict else {}
                result['tool_call_extra_fields'] = min(len(set(first) -
                    {'id', 'type', 'function'}), 1_000_000)
                function = first.get('function', _MISSING)
                result.update(first_tool_call_kind=_kind(observed_first),
                    first_tool_id_kind=_kind(first.get('id', _MISSING)),
                    first_tool_type_kind=_kind(first.get('type', _MISSING)),
                    first_tool_function_kind=_kind(function),
                    first_tool_name_kind=_kind(function.get('name', _MISSING))
                        if type(function) is dict else 'missing',
                    first_tool_index_kind=_kind(first.get('index', _MISSING)))
                if type(function) is dict:
                    result['first_tool_arguments_kind'] = _kind(function.get('arguments', _MISSING))
    usage = data.get('usage', _MISSING)
    result['usage_kind'] = _kind(usage)
    if type(usage) is dict:
        counters, types = {}, {}
        observed = {key: usage[key] for key in _COUNTERS if key in usage and
                    key not in ('cached_tokens', 'reasoning_tokens')}
        for group, counter in (('prompt_tokens_details', 'cached_tokens'),
                               ('completion_tokens_details', 'reasoning_tokens')):
            detail = usage.get(group)
            if type(detail) is dict and counter in detail:
                observed[counter] = detail[counter]
        for key, item in observed.items():
            types[key] = _kind(item)
            if type(item) is int and 0 <= item <= 2 ** 63 - 1:
                counters[key] = item
        result.update(usage_counters=counters, usage_counter_types=types)
    return validate_failure_diagnostic(result)
