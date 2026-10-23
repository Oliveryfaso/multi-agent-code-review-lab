"""Allowlisted operational metadata for new legacy trace writes.

This projection never walks raw tool data, prompts, source, board or messages.
It is not a diagnostic checkpoint, source proof or arbitrary secret detector.
"""
from datetime import datetime
import math
from uuid import UUID

from macr.investigation.validation import validate_identifier
from macr.schemas import AgentAnswer, PatchArtifact, Plan, RunState, Trace

SUMMARY_SCHEMA = 'macr.trace.summary'
SUMMARY_VERSION = 1
MAX_SUMMARY_ROWS = 64
MAX_COUNTER = 2**63 - 1
STATUSES = frozenset(('unknown', 'pending', 'running', 'completed', 'failed', 'skipped',
    'prepared', 'candidate_completed', 'accepted', 'rejected', 'blocked', 'partial',
    'incomplete', 'cancelled', 'needs_review'))
FAILURE_KINDS = frozenset(('transport', 'incomplete', 'invalid_json', 'invalid_contract'))
TOOLS = frozenset(('search_text', 'parse_ast', 'symbol_graph', 'code_graph', 'repo_map',
                   'git_log', 'run_tests', 'verify_patch'))
ROLES = frozenset(('planner', 'solver', 'llm_solver', 'patcher', 'diagnostic_candidate', 'diagnostic_verifier'))
STATE_NAMES = STATUSES | frozenset(('task_received', 'planned', 'routed', 'repo_mapped',
    'retrieved', 'analyzed', 'solved', 'reviewed', 'patching', 'verified', 'monitored',
    'retrieval', 'code_quality', 'final_review', 'verification', 'monitor'))
PROTOCOLS = frozenset(('diagnostic-compact-v2', 'diagnostic-compact-v3'))


def _mapping(value):
    # Do not invoke arbitrary mapping/subclass hooks.
    return value if type(value) is dict else {}


def _enum(value, allowed):
    # Check length before hashing or comparing unbounded caller text.
    return value if type(value) is str and len(value) <= 64 and value in allowed else 'unknown'


def _flag(value):
    return value if type(value) is bool else None


def _number(value, *, integer=False):
    if value is None:
        return None
    if type(value) is float and not math.isfinite(value):
        raise ValueError('trace_value_nonfinite')
    if type(value) not in ((int,) if integer else (int, float)) or not 0 <= value <= MAX_COUNTER:
        raise ValueError('trace_value_number')
    return value


def _count(value):
    # len on exact containers does not traverse or allocate their raw contents.
    return len(value) if type(value) in (dict, list, tuple) else 0


def _time(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Canonical output, never an arbitrary date-like string copied unchanged.
    return parsed.isoformat()


def _attempt_id(value):
    if type(value) is not str or len(value) != 36:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _tool(row, index):
    row = _mapping(row)
    return {'id': f'tool-{index + 1}', 'tool': _enum(row.get('tool'), TOOLS),
        'ok': _flag(row.get('ok')), 'latency_ms': _number(row.get('latency_ms')),
        'error_present': row.get('error_type') is not None,
        'retryable': _flag(row.get('retryable'))}


def _llm(row, index):
    row = _mapping(row)
    usage = _mapping(row.get('usage'))
    return {'id': f'llm-{index + 1}', 'role': _enum(row.get('role'), ROLES),
        'provider': _enum(row.get('provider'), frozenset(('mock', 'local_http', 'deepseek'))),
        'model_present': type(row.get('model')) is str and bool(row['model']),
        'status': _enum(row.get('status'), STATUSES),
        'failure_kind': _enum(row.get('failure_kind'), FAILURE_KINDS),
        'provider_invoked': _flag(row.get('provider_invoked')),
        'latency_ms': _number(row.get('latency_ms')),
        'usage_kind': _enum(row.get('usage_kind'), frozenset(('measured', 'estimated', 'unknown'))),
        'usage': {key: _number(usage[key], integer=True)
                  for key in ('input_tokens', 'output_tokens', 'total_tokens') if key in usage},
        'finish_reason': _enum(row.get('finish_reason'),
                              frozenset(('stop', 'length', 'tool_calls', 'content_filter', 'unknown')))}


def _state(row, index):
    if type(row) is not RunState:
        return {'id': f'state-{index + 1}', 'name': 'unknown', 'status': 'unknown', 'created_at': None}
    return {'id': f'state-{index + 1}', 'name': _enum(row.name, STATE_NAMES),
            'status': _enum(row.status, STATUSES), 'created_at': _time(row.created_at)}


def project_trace_summary(trace: Trace) -> dict:
    """Read only fixed fields and at most 64 rows per selected collection."""
    if type(trace) is not Trace or type(trace.task_id) is not str or not 1 <= len(trace.task_id) <= 128:
        raise ValueError('trace_value_unsupported')
    validate_identifier(trace.task_id)
    truncated = {}

    def rows(value, name, project):
        if type(value) not in (list, tuple):
            raise ValueError('trace_value_collection')
        total = len(value)
        truncated[name + '_omitted'] = max(0, total - MAX_SUMMARY_ROWS)
        return [project(value[index], index) for index in range(min(total, MAX_SUMMARY_ROWS))]

    tools = rows(trace.tool_calls, 'tool_calls', _tool)
    llms = rows(trace.llm_calls, 'llm_calls', _llm)
    states = rows(trace.state_timeline, 'state_timeline', _state)
    diagnosis = _mapping(trace.diagnosis)
    scope = _mapping(diagnosis.get('scope'))
    metrics = _mapping(trace.metrics)
    review = _mapping(metrics.get('final_review'))
    outcome = _mapping(metrics.get('run_outcome'))
    answer = trace.answer if type(trace.answer) is AgentAnswer else None
    plan = trace.plan if type(trace.plan) is Plan else None
    patch = trace.patch if type(trace.patch) is PatchArtifact else None
    verification = _mapping(patch.verification) if patch else {}
    truncated['applied'] = any(truncated.values())
    return {
        'schema': SUMMARY_SCHEMA, 'version': SUMMARY_VERSION,
        'task_id': trace.task_id, 'created_at': _time(trace.created_at),
        'counts': {'tool_calls': len(trace.tool_calls), 'llm_calls': len(trace.llm_calls),
                   'states': len(trace.state_timeline), 'evidence': _count(answer.evidence) if answer else 0},
        'plan': {'present': plan is not None, 'step_count': _count(plan.steps) if plan else 0},
        'tool_calls': tools, 'llm_calls': llms, 'state_timeline': states,
        'answer': {'present': answer is not None,
                   'status': _enum(answer.status if answer else None, STATUSES)},
        'patch': {'present': patch is not None, 'target_file_count': _count(patch.target_files) if patch else 0,
            'source': _enum(patch.source if patch else None, frozenset(('template', 'model', 'llm'))),
            'verification': {'ok': _flag(verification.get('ok')),
                             'status': _enum(verification.get('status'), STATUSES)}},
        'diagnosis': {'present': type(trace.diagnosis) is dict,
            'attempt_id': _attempt_id(scope.get('attempt_id')),
            'status': _enum(diagnosis.get('status'), STATUSES),
            'protocol': _enum(diagnosis.get('protocol'), PROTOCOLS),
            'candidate_present': diagnosis.get('candidate') is not None,
            'final_present': diagnosis.get('final') is not None,
            'candidate_parsed': _flag(diagnosis.get('candidate_parsed')),
            'verification_parsed': _flag(diagnosis.get('verification_parsed')),
            'semantic_proof': _flag(diagnosis.get('semantic_proof'))},
        'metrics': {
            'final_review': {'present': type(metrics.get('final_review')) is dict,
                'ok': _flag(review.get('ok')), 'human_review_required': _flag(review.get('human_review_required')),
                'issue_count': _count(review.get('issues')), 'question_count': _count(review.get('questions'))},
            'run_outcome': {'status': _enum(outcome.get('status'), STATUSES),
                            'end_to_end_completed': _flag(outcome.get('end_to_end_completed'))}},
        'omission': {'free_text': True, 'paths': True, 'tool_data': True, 'requests': True,
                     'source': True, 'board': True, 'raw_model_output': True},
        'truncation': truncated,
    }
