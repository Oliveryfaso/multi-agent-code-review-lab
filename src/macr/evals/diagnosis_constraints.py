"""Opt-in generation constraints for Diagnosis; no generation or I/O authority.

The existing answer parser remains the contract authority. These constraints
cover field shape and verdict-dependent requirements, not source or semantic
validity. Callers must still check stripped text, relative paths, unique refs,
ordered/source-bounded line ranges and the diagnosis itself.

String bounds here apply before stripping, so this is a deliberately narrower
generation language than the parser's strip-then-bound checks. The GBNF also
uses a canonical field order and counts JSON character productions (including
each escaped unicode unit), rather than claiming exact decoded-string length
equivalence. Nothing in this module attests to a runtime's schema support.
"""

import json


VERDICTS = ('supported_defect', 'no_defect_supported', 'unknown')


def diagnosis_schema():
    """Return a fresh conditional schema, preserving the legal unknown exit.

    Complete, mutually exclusive branches avoid depending on if/then/else.
    uniqueItems and patterns are intentionally absent: keep unsupported or
    context-dependent checks in the existing application validator.
    """
    branches = []
    for verdict in VERDICTS:
        unknown = verdict == 'unknown'
        text = lambda limit: {'type': 'string', 'minLength': 1, 'maxLength': limit}
        reference = {
            'type': 'object', 'additionalProperties': False,
            'required': ['file', 'start', 'end'],
            'properties': {
                'file': {'type': 'string', 'minLength': 1},
                'start': {'type': 'integer', 'minimum': 1},
                'end': {'type': 'integer', 'minimum': 1},
            },
        }
        branches.append({
            'type': 'object', 'additionalProperties': False,
            'required': ['verdict', 'cause', 'when', 'refs', 'unknowns'],
            'properties': {
                'verdict': {'const': verdict},
                'cause': {'type': 'null'} if unknown else text(600),
                'when': {'type': 'null'} if unknown else text(300),
                'refs': {'type': 'array', 'minItems': 0 if unknown else 1,
                         'maxItems': 3, 'items': reference},
                'unknowns': {'type': 'array', 'minItems': 1 if unknown else 0,
                             'maxItems': 6, 'items': text(200)},
            },
        })
    return {'oneOf': branches}


def build_optional_response_format():
    """Build an explicit schema candidate; no existing caller is changed.

    This is the schema wrapper consumed by the fixed llama.cpp server API.
    A caller still needs separately established runtime compatibility and new
    execution authorization before using it for an actual model request.
    """
    return {'type': 'json_schema', 'json_schema': {'schema': diagnosis_schema()}}


def diagnosis_gbnf():
    """Return a bounded structural candidate with canonical field order.

    This hand-authored grammar is not a replacement for host validation or an
    attestation that schema conversion produces an equivalent native grammar.
    Whitespace between JSON tokens is allowed; positive integers exclude a
    minus sign, decimal point, exponent and leading zero.
    """
    literal = lambda value: json.dumps(value, ensure_ascii=True)
    member = lambda name, value: literal(json.dumps(name)) + ' ws ":" ws ' + value
    rules = ['root ::= ws (supported-defect | no-defect-supported | unknown) ws']
    for verdict in VERDICTS:
        unknown = verdict == 'unknown'
        values = [
            member('verdict', literal(json.dumps(verdict))),
            member('cause', '"null"' if unknown else 'cause-string'),
            member('when', '"null"' if unknown else 'when-string'),
            member('refs', 'optional-refs' if unknown else 'required-refs'),
            member('unknowns', 'required-gaps' if unknown else 'optional-gaps'),
        ]
        rules.append(verdict.replace('_', '-') + ' ::= "{" ws '
                     + ' ws "," ws '.join(values) + ' ws "}"')
    reference = [member('file', 'file-string'), member('start', 'positive-integer'),
                 member('end', 'positive-integer')]
    rules.append('ref ::= "{" ws ' + ' ws "," ws '.join(reference) + ' ws "}"')
    rules.extend([
        'required-refs ::= "[" ws ref ws ("," ws ref ws){0,2} "]"',
        'optional-refs ::= "[" ws (ref ws ("," ws ref ws){0,2})? "]"',
        'required-gaps ::= "[" ws gap-string ws ("," ws gap-string ws){0,5} "]"',
        'optional-gaps ::= "[" ws (gap-string ws ("," ws gap-string ws){0,5})? "]"',
        'positive-integer ::= [1-9] [0-9]*',
        r'cause-string ::= "\"" json-char{1,600} "\""',
        r'when-string ::= "\"" json-char{1,300} "\""',
        r'gap-string ::= "\"" json-char{1,200} "\""',
        r'file-string ::= "\"" json-char+ "\""',
        r'json-char ::= [^"\\\x00-\x1f] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F]{4})',
        r'ws ::= [ \t\n\r]*',
    ])
    return '\n'.join(rules) + '\n'
