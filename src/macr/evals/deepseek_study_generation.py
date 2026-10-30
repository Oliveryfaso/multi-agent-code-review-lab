"""Versioned generation ceilings; the cost envelope does not widen usage trust."""
from __future__ import annotations
from macr.evals.deepseek_policy_manifest import (
    PROMPT_VERSION as _LEGACY_PROMPT_VERSION, STUDY_V2_PROMPT_VERSION as V2_PROMPT_VERSION,
    STUDY_EVIDENCE_PROMPT_VERSION as EVIDENCE_PROMPT_VERSION)

V2_PROFILE = 'study-8192-v2'
EVIDENCE_PROFILE = 'study-evidence-v1'
_MAX_OUTPUT_TOKENS = 393216
_MAX_CONTEXT_INPUT_ENVELOPE = 1048576


def minimum_reservation_micro_usd(max_output_tokens, context_input_envelope=1048576):
    """Ceil the all-miss peak-price envelope in micro-USD, not a supplier cap."""
    if (type(max_output_tokens) is not int or not 1<=max_output_tokens<=_MAX_OUTPUT_TOKENS
            or type(context_input_envelope) is not int
            or not 1<=context_input_envelope<=_MAX_CONTEXT_INPUT_ENVELOPE):
        raise ValueError('study_generation_budget_invalid')
    # $0.30 / 1M input and $1.20 / 1M output; independent loose upper bounds.
    return (3*context_input_envelope+12*max_output_tokens+9)//10


def validate_reservation_micro_usd(max_output_tokens, reserved_micro_usd,
                                 context_input_envelope=1048576):
    """Accept only an integer reserve that covers the complete loose envelope."""
    minimum = minimum_reservation_micro_usd(max_output_tokens,context_input_envelope)
    if type(reserved_micro_usd) is not int or reserved_micro_usd<minimum:
        raise ValueError('study_generation_reservation_invalid')
    return reserved_micro_usd


def get_generation(profile=None):
    """Return a detached contract for the legacy or explicitly named profiles."""
    if profile is not None and (type(profile) is not str or profile not in (V2_PROFILE,EVIDENCE_PROFILE)):
        raise ValueError('study_generation_profile_invalid')
    output = 2000 if profile is None else 8192
    context = 1000000 if profile is None else _MAX_CONTEXT_INPUT_ENVELOPE
    minimum = minimum_reservation_micro_usd(output,context)
    reserved = 330000 if profile is None else ((minimum+9999)//10000)*10000
    return {'name':profile,
        'prompt_version':{None:_LEGACY_PROMPT_VERSION,V2_PROFILE:V2_PROMPT_VERSION,
            EVIDENCE_PROFILE:EVIDENCE_PROMPT_VERSION}[profile],
        'max_output_per_request':output,'max_total_output_tokens':3*output,
        'planned_input_per_request':8000,'max_total_input_tokens':24000,
        'context_input_envelope':context,
        'reservation_micro_usd':validate_reservation_micro_usd(output,reserved,context),
        'minimum_reservation_micro_usd':minimum}
