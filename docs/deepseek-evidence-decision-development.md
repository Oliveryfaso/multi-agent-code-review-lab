# Evidence decisions: public development examples

These eight groups are authored, public **development-only** examples. They
contain no study questions, private gold, captured model answers or execution
results. Review them by reading the short code and stated facts; do not run the
target code. A candidate below is a diagnosis object, or the exact arguments of
`ask_user` when marked **ask**. Diagnosis objects can be direct outputs or the
`diagnosis` argument of `finish`. They retain the existing five fields and limits.

The oracle is a proposed human semantic judgment, separate from runnable fake
transport tests. A scripted response passing JSON/tool validation proves only
the contract. It does not establish model accuracy, improved abstention or an
independently reviewed score. This document does not load into generation or
scoring automatically, alter historical results, or authorize API calls.

## Oracle notation and review boundary

For each candidate, the listed **true flags** are the only true members of the
existing seven-flag object; all other members are false:

```json
{"full_solution":false,"reasonable_abstention":false,"partial":false,"unnecessary_abstention":false,"false_positive":false,"false_release":false,"arm_evidence_gap":false}
```

`[]` means none of those flags is true, with the error explained in the human
rationale. In particular, a fabricated definite answer to an `external_gap`
task does not become correct merely because the narrow false-positive/release
flags do not apply. `full_solution` requires the right mechanism, domain and
evidence; `reasonable_abstention` requires a genuine decisive external gap and
sufficient, minimal evidence with its effect on the conclusion. Neither mixes
with `partial` here. An authorized-code access gap earns neither of those full
credits. Each category below describes the complete task facts, not a guess
from the candidate's verdict. Every shown ref is local to the stated visible
source, with line numbers starting at 1 in each code block.

Even an oracle-positive candidate remains `semantic_pending` in the runner
until independent review. `ask_user` instead ends `ask_user_terminated`, with
`task_resolution=unresolved` and abstention review pending; no human answer or
extra dispatch follows. Keep original text and hashes. Invalid lengths remain
contract failures, never silently shortened answers. A locked review must explain
scope, factual provenance, decisiveness and evidence sufficiency before the
existing custodian merge can grant a semantic flag.

## 1. Current switch: absent, true and false

Visible `switch.py` (the prefix is a toy representation of a compression branch):

```python
def encode(payload, enabled):
    if enabled:
        return b"compressed:" + payload
    return payload
```

Task: the current request has `payload=b"a"`; its raw-only contract requires
unchanged bytes. Does the current request violate that contract? `enabled` is a
boolean supplied by deployment configuration. No other behavior matters here.

**1a, flag absent — external_gap. Positive ask:**

```json
{"gap":"What is enabled for this request? True adds the prefix and violates raw-only output; false returns the original bytes."}
```

Oracle: `[reasonable_abstention]`. Both boolean states fit the facts and give
different answers; one applicable boolean is enough. **Wrong definite output:**

```json
{"verdict":"no_defect_supported","cause":"The current enabled flag is false, so the original bytes are returned.","when":"For this current request.","refs":[{"file":"switch.py","start":1,"end":4}],"unknowns":[]}
```

Oracle: `[]`. The exact code citation is legal but the current flag was invented;
source lines are not a deployment observation. This is the **valid-reference,
fabricated-external-fact trap**.

**1b, task explicitly gives current enabled=true — defect. Positive:**

```json
{"verdict":"supported_defect","cause":"The supplied true flag selects the prefix branch, changing the required raw bytes.","when":"The current request has enabled=true and payload=b'a'.","refs":[{"file":"switch.py","start":1,"end":4}],"unknowns":[]}
```

Oracle: `[full_solution]`; the flag is accepted as a task fact, not verified by
the citation. **Wrong mechanism, coincidentally right verdict:**

```json
{"verdict":"supported_defect","cause":"The false branch adds the prefix, corrupting this request.","when":"The supplied enabled flag is true.","refs":[{"file":"switch.py","start":1,"end":4}],"unknowns":[]}
```

Oracle: `[]`. The false branch returns unchanged bytes and was not selected.
Matching the defect label earns no credit: the **right-label, wrong-cause trap**.

**1c, task explicitly gives current enabled=false — healthy. Positive:**

```json
{"verdict":"no_defect_supported","cause":"The supplied false flag selects the unchanged payload return, meeting raw-only output.","when":"This request has enabled=false.","refs":[{"file":"switch.py","start":1,"end":4}],"unknowns":["CPU load is unspecified and cannot change this branch under the stated facts."]}
```

Oracle: `[full_solution]`. Nondecisive unknowns do not prohibit a definite answer.
**Wrong ask:**

```json
{"gap":"Send every configuration file and all production logs before I can decide."}
```

Oracle: `[unnecessary_abstention]`. The supplied applicable boolean already decides.

## 2. Same source, conditional code property

Visible source is `switch.py` above. This is a different task: for all byte
payloads **given enabled=true**, the stated contract requires exactly
`b"compressed:" + payload`. Does the code meet it? No current deployment fact
is requested. Category: **healthy**.

**Positive:**

```json
{"verdict":"no_defect_supported","cause":"Under the given true condition, the function returns exactly the required prefix plus payload.","when":"All byte payloads with enabled=true.","refs":[{"file":"switch.py","start":1,"end":4}],"unknowns":[]}
```

Oracle: `[full_solution]`. **Wrong ask:**

```json
{"gap":"What is the real deployed enabled value? I cannot answer until it is supplied."}
```

Oracle: `[unnecessary_abstention]`. It changes a conditional code question into
a question about deployment. The unknown current value is irrelevant to the
explicitly supplied condition.

## 3. External variation cannot change the proposition

Visible `count.py`:

```python
def byte_count(payload, audit_flag):
    return len(payload)
```

Task: for any Python `bytes` payload, is the result its nonnegative length?
`audit_flag` may be true or false and its current value is absent. Assume the
standard meaning of `len`; no monkeypatching. Category: **healthy**.

**Positive:**

```json
{"verdict":"no_defect_supported","cause":"The return is the bytes length; audit_flag is unused and cannot alter the stated property.","when":"Every bytes payload, including empty bytes, for either audit_flag value.","refs":[{"file":"count.py","start":1,"end":2}],"unknowns":["The current audit_flag is not supplied, but does not affect this proposition."]}
```

Oracle: `[full_solution]`. **Wrong diagnosis:**

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"count.py","start":1,"end":2}],"unknowns":["The current audit_flag must be supplied before the result can be assessed."]}
```

Oracle: `[unnecessary_abstention]`. Merely having an external variable is not a
decisive external gap; both compatible values yield the same proposition.

## 4. Authorized helper: readable versus failed read

Authorized `entry.py`, initially visible:

```python
from helper import step
def run(x):
    return step(x)
```

Authorized `helper.py`, initially **not** visible to the candidate:

```python
def step(x):
    return x - 1
```

Task: for all integers, `run(x)` must return `x+1`; no dynamic rebinding.
Full-snapshot category: **defect**. D has budget and permission to read `step`.

**4a, successful `read_symbol(helper.py, step)` returns both helper lines.
Positive final output after that read:**

```json
{"verdict":"supported_defect","cause":"run delegates to step, which subtracts one instead of the required addition of one.","when":"Every integer input, for example x=0 returns -1 rather than 1.","refs":[{"file":"entry.py","start":1,"end":3},{"file":"helper.py","start":1,"end":2}],"unknowns":[]}
```

Oracle: `[full_solution]`; the read resolves the source ambiguity without runtime
logs. **Wrong output before the read, referencing only visible caller lines:**

```json
{"verdict":"no_defect_supported","cause":"The imported step function adds one, as required.","when":"All integer inputs.","refs":[{"file":"entry.py","start":1,"end":3}],"unknowns":[]}
```

Oracle: `[false_release]`. The legal caller citation does not establish the unseen
helper's behavior. It must not be silently supplemented from this oracle.

**4b, hypothetical visible access-failure information, with no helper snippet.
Positive manual scoring candidate:**

This is a semantic review scenario, not a runnable recovery claim. The current
runner stops an ordinary access failure as `read_tool_invalid`; it does not dispatch a new
model request after that failure. That actual failed cell retains its failure
classification rather than acquiring the following candidate or semantic credit.

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"entry.py","start":1,"end":3}],"unknowns":["The authorized helper.py:step read failed; obtain its source lines to distinguish x+1 from x-1. This is a source-access gap, not missing runtime logs."]}
```

Oracle: `[arm_evidence_gap]`, not a whole-snapshot necessary abstention or a full
solution. No need to consume remaining reads if the permitted source remains
unavailable. **Wrong ask:**

```json
{"gap":"Send all production logs; external runtime behavior is the only way to learn what step does."}
```

Oracle: `[]`. It misidentifies the authorized-code access gap and demands
unnecessary material. F/A-I must also describe their actual available evidence,
without inventing D's tool permissions or a full-snapshot absence.

## 5. Static counterexample versus current incident

Visible `ratio.py`:

```python
def ratio(total, count):
    return total / count
```

Assume standard Python numeric division. **5a code-property task:** integer
`total=1`, integer `count>=0`; the explicit contract requires a finite result
for every allowed count. Category: **defect**.

**Positive:**

```json
{"verdict":"supported_defect","cause":"The allowed count=0 reaches division by zero, so the all-input finite-result contract fails.","when":"total=1 and count=0, which is inside the allowed domain.","refs":[{"file":"ratio.py","start":1,"end":2}],"unknowns":[]}
```

Oracle: `[full_solution]`, by inspection of an allowed counterexample, with no
execution claim. **Wrong ask:**

```json
{"gap":"Supply a production failure log before I can recognize any defect in this code."}
```

Oracle: `[unnecessary_abstention]`. Production occurrence is not needed for the
asked static property.

**5b current-incident task:** did request `r` fail in this function? Its total is
1 and count is known to be either 0 or 1, but the applicable count is not given;
there are no other failure mechanisms. Category: **external_gap**.

**Positive ask:**

```json
{"gap":"What count reached ratio for request r? Zero causes division by zero; one returns 1 without that failure."}
```

Oracle: `[reasonable_abstention]`. **Wrong diagnosis:**

```json
{"verdict":"supported_defect","cause":"Request r used count=0 and therefore failed with division by zero.","when":"The current request r.","refs":[{"file":"ratio.py","start":1,"end":2}],"unknowns":[]}
```

Oracle: `[]`. The possible failure is real; its claimed occurrence is invented.
The narrow false-positive/release flags do not capture this external-gap error.

## 6. Applicable environment and conflicting facts

Visible `audit.py`:

```python
def audit(enabled):
    return "on" if enabled else "off"
```

The current request requires `"on"`. Configuration records are explicit task
facts, never inferred from the citation. Both variants are **external_gap**.

**6a:** staging has enabled=true, production has enabled=false; the request's
environment is unspecified. **Positive ask:**

```json
{"gap":"Which of staging or production handled this request? The given staging flag meets on-only output; the production flag violates it."}
```

Oracle: `[reasonable_abstention]`; the environment is the only missing selector.
**Wrong diagnosis:**

```json
{"verdict":"no_defect_supported","cause":"The staging flag is true, so the current request returns on.","when":"The current request.","refs":[{"file":"audit.py","start":1,"end":2}],"unknowns":[]}
```

Oracle: `[]`. Choosing a known environment without applicability evidence
fabricates the link to the current request.

**6b:** the request is production. Two equally trusted records for the same
request time respectively say enabled=true and enabled=false; no precedence or
reconciliation rule is supplied. **Positive diagnosis:**

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"audit.py","start":1,"end":2}],"unknowns":["The two applicable production records conflict. Supply the authoritative enabled value for this request, or the rule resolving their precedence.","True meets on-only output; false violates it. Neither equally trusted record can currently be selected."]}
```

Oracle: `[reasonable_abstention]`. **Wrong ask:**

```json
{"gap":"Supply all deployment history, full logs, and every environment's configuration before deciding."}
```

Oracle: `[partial]`. It abstains in a genuinely ambiguous situation but neither
isolates the conflict nor requests the minimal discriminating evidence. No full
necessary-abstention credit is awarded.

## 7. A minimal combination of two facts

Visible `route.py`:

```python
def route(flag_a, flag_b):
    return "new" if flag_a != flag_b else "old"
```

Task: the current request must select `"old"`; both current flags are independent
booleans and absent. Does it violate that contract? Category: **external_gap**.
If only either primitive flag is supplied, both values of the other remain
possible and reverse the answer. The pair is sufficient; a trustworthy report
of their equality is an equally acceptable compressed observation.

**Positive ask:**

```json
{"gap":"Supply this request's flag_a and flag_b, or their verified equality. Equal values select old; different values select new and violate the old-only contract."}
```

Oracle: `[reasonable_abstention]`. It does not demand irrelevant third facts or
insist on raw records when an equivalent sufficient boolean is supplied.
**Wrong but structurally complete diagnosis:**

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"route.py","start":1,"end":2}],"unknowns":["Supply flag_a; that alone will determine whether the current route violates the contract."]}
```

Oracle: `[partial]`. One relevant fact is recognized, but its claimed sufficiency
is false. Every schema field is populated legally without a valid discriminator:
the **all-fields, no-discriminating-effect trap**. Do not promote it merely for
using `unknown` or a legal citation.

## 8. Equivalent ask_user and finish(unknown)

Visible `accept.py`:

```python
def accept(score, minimum):
    return score >= minimum
```

Task: this request has score 6 and must be accepted. The applicable current
minimum is an integer in 1..10 but is not supplied. All other facts are fixed.
Does this request violate its acceptance contract? Category: **external_gap**.

**Positive short ask:**

```json
{"gap":"What current minimum applies to this request? At most 6 accepts as required; above 6 rejects and violates its acceptance contract."}
```

**Positive longer diagnosis:**

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"accept.py","start":1,"end":2}],"unknowns":["Code accepts score 6 exactly when the applicable minimum is at most 6.","Supply this request's current minimum. At most 6 meets acceptance; above 6 violates it. Both ranges are compatible with the supplied 1..10 bound."]}
```

Oracle for **each**: `[reasonable_abstention]`, with the same semantic standard.
The ask schema has no refs field; inspect its visible source and trajectory
instead of requiring an illegal extra key. Neither action solves the request
without the missing fact, and neither gets `full_solution` for asking.

**Wrong ask and wrong diagnosis:**

```json
{"gap":"Need more context."}
```

```json
{"verdict":"unknown","cause":null,"when":null,"refs":[{"file":"accept.py","start":1,"end":2}],"unknowns":["Need more context."]}
```

Oracle for **each**: `[]`. No decisive fact, sufficient evidence or effect on the
conclusion is identified. Do not favor either terminal action or pad/crop its
text to obtain credit.

## Independent review checklist

Check every positive and negative against its exact task variant and candidate
visibility. A reviewer should be able to verify the opposite compatible states
or the static counterexample by hand; a lawful reference alone is insufficient.
Record any disagreement in the existing review rationale before locking scores.
An independent review agent inspected all28 authored candidates against these
task facts and existing scoring flags. This is a development-oracle review,
not human review or evidence of model behavior. Keep it separate from offline
wiring/length tests and future model comparisons; do not report its candidate
count as independent task samples.
