"""Independent prompt changes; baseline references and holdouts are untouched."""
from copy import deepcopy

from macr.evals.diagnostic_dataset import SYSTEM
from macr.providers.base import ProviderError, validate_request


VARIANTS = ('baseline', 'neutral', 'evidence_first', 'cross_field')
EVIDENCE_FIRST_FIELDS = ('cause', 'when', 'refs', 'unknowns', 'verdict')
NEUTRAL_ADDITION = (
    ' Give all three verdicts equal consideration; choose by the supplied evidence. '
    'A question about a possible defect does not establish a defect; missing essential evidence does not establish safety.'
)


def apply_prompt_variant(request, variant):
    """Change one system-prompt factor; cross_field uses the same candidate.

    evidence_first must be paired with the same field order in a constrained
    decoder. A verdict-first grammar otherwise defeats this prompt comparison.
    """
    validate_request(request)
    if variant not in VARIANTS or request.messages[0] != {'role': 'system', 'content': SYSTEM}:
        raise ProviderError('diagnostic_variant_invalid')
    result = deepcopy(request)
    if variant == 'neutral':
        result.messages[0]['content'] += NEUTRAL_ADDITION
    elif variant == 'evidence_first':
        result.messages[0]['content'] = SYSTEM.replace(
            'exactly verdict, cause, when, refs, unknowns.',
            'exactly cause, when, refs, unknowns, verdict, in that order.')
    return result
