"""Explicit operator HTTP adapter, not approval or model identity attestation.

The caller supplies a temporary credential reader. This module never discovers
credentials or enables the normal repair CLI, and it adds no retry behavior.
Dropping credential references does not securely zero Python's string memory.
"""
from __future__ import annotations

import math

from .base import ProviderError
from .deepseek import _ApprovedTransport
from .failure_evidence import validate_failure_diagnostic
from .response_normalization import ResponseNormalizationError, decode_provider_json

_ENDPOINT = 'https://api.deepseek.com/chat/completions'
_MAX_BYTES = 1024 * 1024
_MAX_NODES = 65536
_MAX_DEPTH = 64
_MAX_PROPOSAL_BYTES = 65536
_TRANSPORT_ERRORS = frozenset({'model_transport', 'model_timeout',
    'model_redirect_blocked', 'model_response_limit', 'model_response_invalid'})


def _scan_credential(value, key, budget):
    """Bounded JSON traversal; only this request's exact credential is checked."""
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        budget[0] += 1
        if budget[0] > _MAX_NODES or depth > _MAX_DEPTH:
            return 'model_response_limit'
        if type(current) is str:
            budget[1] += len(current)
            if budget[1] > _MAX_BYTES:
                return 'model_response_limit'
            if key in current:
                return 'model_response_credential_echo'
        elif type(current) in (dict, list):
            needed = len(current) * (2 if type(current) is dict else 1)
            if budget[0] + len(pending) + needed > _MAX_NODES:
                return 'model_response_limit'
            if type(current) is dict:
                for name, item in current.items():
                    pending.append((name, depth + 1))
                    pending.append((item, depth + 1))
            else:
                pending.extend((item, depth + 1) for item in current)
        elif current is not None and type(current) not in (bool, int, float):
            return 'model_response_invalid'
    return None


def _response_failure(value, key):
    budget = [0, 0]
    failure = _scan_credential(value, key, budget)
    if failure is not None:
        return failure
    # The repair consumer decodes this one known JSON field again. Inspect that
    # object without changing its original bytes or decoding arbitrary strings.
    choices = value.get('choices') if type(value) is dict else None
    choice = choices[0] if type(choices) is list and len(choices) == 1 else None
    message = choice.get('message') if type(choice) is dict else None
    content = message.get('content') if type(message) is dict else None
    if type(content) is str and content:
        try:
            if len(content.encode('utf-8')) > _MAX_PROPOSAL_BYTES:
                return 'model_response_invalid'
            decoded = decode_provider_json(content)
        except (ResponseNormalizationError, UnicodeError, RecursionError):
            return 'model_response_invalid'
        failure = _scan_credential(decoded, key, budget)
        return 'model_response_invalid' if failure == 'model_response_limit' else failure
    return None


class OfficialRepairTransport:
    def __init__(self, credential_reader, *, allow_remote=False):
        if type(allow_remote) is not bool or not callable(credential_reader):
            raise ProviderError('remote_config_missing')
        self._credential_reader = credential_reader
        self._allow_remote = allow_remote
        self._response_metadata = None

    def __getstate__(self):
        raise TypeError('repair_transport_not_serializable')

    @property
    def enabled(self):
        return self._allow_remote

    @property
    def response_metadata(self):
        return (validate_failure_diagnostic(self._response_metadata)
                if self._response_metadata is not None else None)

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self._response_metadata = None
        if not self._allow_remote:
            raise ProviderError('remote_disabled')
        if not (type(endpoint) is str and endpoint == _ENDPOINT
                and type(body) is bytes and 0 < len(body) <= _MAX_BYTES
                and type(timeout_seconds) in (int, float)
                and 0 < timeout_seconds <= 300 and math.isfinite(timeout_seconds)
                and type(max_response_bytes) is int
                and 0 < max_response_bytes <= _MAX_BYTES):
            raise ProviderError('model_request_invalid')

        temporary = key = None
        failure = result = None
        try:
            try:
                key = self._credential_reader()
            except Exception:
                failure = 'remote_config_missing'
            if failure is None and not (type(key) is str and 0 < len(key) <= 4096
                    and all(33 <= ord(character) <= 126 for character in key)):
                failure = 'remote_config_missing'
            if failure is None:
                try:
                    temporary = _ApprovedTransport(key)
                    result = temporary.post(endpoint, body, timeout_seconds, max_response_bytes)
                    failure = _response_failure(result, key)
                except ProviderError as error:
                    failure = (error.code if type(error) is ProviderError
                        and type(error.code) is str and error.code in _TRANSPORT_ERRORS
                        else 'model_transport')
                except Exception:
                    failure = 'model_transport'
        finally:
            if temporary is not None:
                try:
                    # Only the delegate's secret-filtered metadata is retained;
                    # an arbitrary exception's diagnostic is not trusted.
                    self._response_metadata = temporary.response_metadata
                except Exception:
                    self._response_metadata = None
                    failure = failure or 'model_transport'
                temporary._api_key = None
            key = None
        # Raise outside the handlers so a raw credential-bearing exception is
        # neither displayed nor retained as this error's __context__.
        if failure is not None:
            result = None
            raise ProviderError(failure, diagnostic=self._response_metadata)
        return result
