"""Optional process-local finish checkpoints for captured diagnostic responses.

Begin before human review. Only the issued token's object identity is trusted;
its attributes and persisted markers cannot restore an origin. The exclusive
consume claim defaults to pending before any score is saved. Only a matching
accepted authority can accept that first attempt's comparison. This synchronous
wrapper never interrupts human work or I/O and never assesses whole-run timing.
"""
import math
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from macr.evals import diagnostic_compare as comparison
from macr.memory.run_store import encoded

FINISH_SECONDS = 300
START_RECORD = 'review-finish-start.json'
CONSUME_RECORD = 'review-finish-consume.json'
AUTHORITY_RECORD = 'review-finish-authority.json'
_PROCESS_MONOTONIC = monotonic
_ISSUED = {}


class _FinishReview:
    """Opaque identity key. Caller-added attributes are never timing evidence."""
    def __reduce__(self):
        raise TypeError('finish token is process-local and cannot be restored')


def _clock_value():
    try:
        value = monotonic()
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return value
    except Exception:
        pass
    return None


def _exclusive_record(out, name, record):
    data = encoded(record) + b'\n'
    used = sum(path.stat().st_size for path in out.rglob('*') if path.is_file())
    comparison._require(used + len(data) <= comparison.RECORD_LIMIT, 'comparison_record_limit')
    with (out / name).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def begin_finish_review(out):
    """Issue one non-resettable origin using the process's time.monotonic.

    An existing start/consume marker refuses another origin, including after
    failure or another turn. The on-disk marker cannot reconstruct the token.
    """
    started = _clock_value()
    out, _, _, _, _ = comparison._load(out)
    if (out / CONSUME_RECORD).exists():
        raise ValueError('review_finish_already_consumed')
    record = {'schema_version': 1, 'phase': 'finish_review', 'budget_seconds': FINISH_SECONDS,
              'pid': os.getpid(), 'nonce': str(uuid4()), 'start_monotonic_seconds': started,
              'clock_source': 'time.monotonic' if monotonic is _PROCESS_MONOTONIC else 'patched_clock_fixture',
              'resumable': False}
    try:
        _exclusive_record(out, START_RECORD, record)
    except FileExistsError:
        raise ValueError('review_finish_already_started') from None
    token = _FinishReview()
    _ISSUED[token] = {'out': out, 'record': record, 'last': started}
    return token


def save_finish_review(out, *, reviews=None, phase=None):
    """Accept reviews only after bounded checkpoints and required persistence.

    Before scoring, exclusively persist a consume claim with pending authority.
    Missing, copied, reconstructed, consumed or cross-process tokens save only
    reviews={} pending comparisons. A late reviewed report remains evidence but
    is unaccepted even if saving its replacement/sidecar fails: without a valid
    authority naming that report, the durable consume claim remains pending.

    Authority accepts comparison/sidecar checkpoints only. Its own write and
    late evidence writes are not certified within 300 seconds. No caller clock
    or compliance flag is accepted. Later calls cannot replace a sealed first
    authority or accept new reviews; their returned comparison is pending.
    """
    out, _, _, _, _ = comparison._load(out)
    state = _ISSUED.pop(phase, None) if type(phase) is _FinishReview else None
    reason = 'finish_token_missing_or_consumed'
    if state is not None:
        record = state['record']
        if state['out'] != out or record['pid'] != os.getpid():
            reason = 'finish_origin_different_process_or_comparison'
        elif state['last'] is None:
            reason = 'monotonic_unavailable'
        else:
            reason = None
        try:
            if comparison._read(out, START_RECORD) != record:
                reason = 'finish_origin_changed'
        except (OSError, ValueError):
            reason = 'finish_origin_unavailable'
    claim = {'schema_version': 1, 'phase': 'finish_review', 'budget_seconds': FINISH_SECONDS,
             'nonce': str(uuid4()), 'default_authority': 'pending', 'review_acceptance': 'not_accepted',
             'accepted_authority_file': AUTHORITY_RECORD,
             'rule': 'Only a matching accepted_at_checkpoints authority naming a comparison accepts its reviews; every other reviewed report is unaccepted.'}
    try:
        _exclusive_record(out, CONSUME_RECORD, claim)
    except FileExistsError:
        reason = 'finish_claim_already_consumed'
        try:
            claim = comparison._read(out, CONSUME_RECORD)
        except (OSError, ValueError):
            claim = {'nonce': None, 'default_authority': 'pending'}
    checkpoints = []

    def checkpoint(name):
        nonlocal reason
        elapsed = None
        if reason is None:
            now = _clock_value()
            if now is None:
                reason = 'monotonic_unavailable'
            elif now < state['last']:
                reason = 'monotonic_went_backwards'
            else:
                state['last'] = now
                elapsed = now - state['record']['start_monotonic_seconds']
                if elapsed >= FINISH_SECONDS:
                    reason = 'finish_deadline_reached'
        checkpoints.append({'name': name, 'elapsed_seconds': elapsed})

    checkpoint('before_review')
    accepted = reviews if reason is None and reviews is not None else {}
    report = comparison.compare_results(out, reviews=accepted)
    checkpoint('after_comparison')
    if reason is not None and accepted:
        accepted = {}
        report = comparison.compare_results(out, reviews={})
    paths = comparison.save_comparison(out, report)
    checkpoint('after_comparison_save')
    superseded = []
    claim_path = str(out / CONSUME_RECORD)
    authority_path = str(out / AUTHORITY_RECORD)

    def sidecar(paths, status, authoritative):
        folder = Path(paths['json']).parent
        name = folder.name + '/finish-phase.json'
        comparison._write(out, name, {
            'schema_version': 1, 'phase': 'finish_review', 'budget_seconds': FINISH_SECONDS,
            'status': status, 'pending_reason': reason,
            'origin': state['record']['clock_source'] if state is not None else 'missing',
            'resumable': False, 'checkpoints': list(checkpoints),
            'consume_claim': claim_path, 'claim_nonce': claim.get('nonce'),
            'authoritative_comparison': authoritative, 'superseded_not_accepted': list(superseded),
            'accepted_authority_file': authority_path,
            'review_acceptance': 'not_accepted_without_matching_authority',
            'timing_record_persistence_within_budget': None, 'whole_run_compliance': 'not_assessed',
            'new_model_calls': 0,
            'scope': 'Only named comparison checkpoints can be accepted. Authority write and late evidence writes are not certified within the deadline.'})
        return str(out / name)

    if reason is not None and accepted:
        superseded.append(paths)
        sidecar(paths, 'superseded_not_accepted', None)
        accepted = {}
        paths = comparison.save_comparison(out, comparison.compare_results(out, reviews={}))
    timing_path = sidecar(paths, 'candidate_not_accepted' if accepted else 'pending', paths)
    checkpoint('after_comparison_and_sidecar_save')
    if reason is not None and accepted:
        superseded.append(paths)
        sidecar(paths, 'superseded_not_accepted', None)
        accepted = {}
        paths = comparison.save_comparison(out, comparison.compare_results(out, reviews={}))
        timing_path = sidecar(paths, 'pending', paths)
    status = 'pending'
    authority = claim_path
    if reason is None and accepted:
        # The exclusive consume claim reserves the sole accepting writer.
        # Publish only after the existing atomic writer has fsynced its temp:
        # failed flush/fsync must not leave parseable accepted authority here.
        comparison._require(not (out / AUTHORITY_RECORD).exists(), 'review_finish_authority_exists')
        comparison._write(out, AUTHORITY_RECORD, {
            'schema_version': 1, 'phase': 'finish_review', 'status': 'accepted_at_checkpoints',
            'claim_nonce': claim['nonce'], 'comparison': paths, 'timing': timing_path,
            'checkpoints': checkpoints, 'authority_write_within_budget': None,
            'whole_run_compliance': 'not_assessed', 'new_model_calls': 0})
        status, authority = 'reviews_accepted_at_checkpoints', authority_path
    return {'comparison': paths, 'timing': timing_path, 'authority': authority, 'finish_status': status}
