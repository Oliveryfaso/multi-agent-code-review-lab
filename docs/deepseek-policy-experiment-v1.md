# DeepSeek policy comparison protocol

This research entry is separate from the default static evidence product and the historical `diagnostic_compare` interface. It asks whether a bounded Debugger strategy improves static diagnosis compared with a direct call to the same model, while recording calls, evidence, time and local cost estimates. It never imports or executes the target source. The harness uses the Python standard library and adds no runtime dependency.

New preparation and execution use `deepseek-policy-contracts-v3`. The development dataset remains `deepseek-policy-dev-v1`. `deepseek-policy-refs-v2` manifests remain readable for historical audit but cannot dispatch the new instructions. No paid run has used v3; older prompts and failed results retain their versions and scores. See [aggregate results](deepseek-policy-results.md).

## Four arms

| Arm | Input and procedure | Maximum model requests per cell |
| --- | --- | ---: |
| A | Neutral question, scenario, assumptions and all authorized raw source; one diagnosis | 1 |
| B | Initial source and its bounded static evidence package; one diagnosis | 1 |
| C | The same initial package as B; a candidate followed by one independent model verification | 2 |
| D | The same initial package as B; bounded read tools or a terminal action | 3 |

A sees all authorized files. B/C/D start from the same initial files and an AST summary derived only from those files. The summary reports syntax and unresolved relationships, not runtime behavior. Six development cases have equal source visibility; six require retrieval and deliberately give A more initial source. Report these strata separately. Do not describe every arm as having identical evidence.

These arms compare complete strategies. Isolating the contribution of verification or tool feedback requires later comparisons with matched call counts. Evidence found by D after a decision cannot be supplied to A afterward and treated as its original baseline.

D exposes only `search`, `read_symbol`, `read_caller`, `finish` and `ask_user`. Each of its first two decisions may use at most two read tools; the third must terminate. `read_caller` describes static syntax and may be unresolved. `ask_user` records a specific evidence gap and ends the cell without receiving a human answer. Its task remains unresolved and the reasonableness of abstention remains pending review. Illegal or duplicate actions, missing termination and exhausted budgets are failures; the host does not manufacture a correct answer.

## Output-contract communication

Actual system messages carry the diagnosis schema in A/B/C and D, plus the complete verifier schema on C's second request. The provider's `json_object` mode alone does not transmit those field constraints. The verifier's schema defines exact fields, status/coverage enums and a nonblank reason of at most 256 characters; the same schema supplies validation bounds. D's `ask_user.gap` similarly shares its 1–200 character schema with validation. Diagnosis bounds remain cause600/when300/gap200, with unique source-backed references and verdict-dependent required fields/gaps stated explicitly.

Read-tool parameters advertise the existing 128 UTF-8-byte term and 512-byte authorized-file limits, identifier rules and scope. Tool guidance includes the actual task's total read budget, no identical repeated reads and exactly one terminal call. Entire response/argument JSON stays within the existing 8,192-byte bound. These are guidance plus unchanged client validation, not an API guarantee of model compliance. Existing validators still trim surrounding whitespace before character bounds; generation guidance asks for nonblank, unpadded text and applies schema bounds to raw strings. No answer is silently shortened.

## Inputs and scoring

The [dataset](../evals/deepseek-policy-dev-v1/README.md) contains twelve original synthetic development cases: four defects, four healthy implementations within explicit scopes, and four cases whose necessary evidence is absent from the entire authorized snapshot. Eighteen short Python files form the generation scope. Gold, rubrics, answer tests, repair commits and prior reviews remain outside that scope and never enter model or tool messages.

The development gold is included for reproducibility and has been seen by the authors and development agents. It is not an unseen holdout or an external benchmark. Publishing it does not authorize sending it to a model during generation. Future acceptance data must be separately controlled; exposed historical holdouts must not be renamed as unseen data.

A matching verdict is insufficient. Review the causal mechanism, complete permitted trigger/domain and supporting source ranges, or the specific necessary missing evidence. Record structural validity separately from semantic support. `semantic_pending` means the output passed its structural contract, not that it is correct. A model verifier's acceptance is also not semantic proof. A formal blind review must hide model/arm and gold before judgments are joined; the available live-result reviews were post-hoc audits, not formal blind reviews.

Reports retain the planned-cell denominator, structural/transport failures, unknown outcomes, unattempted and pending cells, and quality conditional on reviewable responses. Repeated runs are not independent new examples. Do not merge different prompt versions into an unconditional four-arm score.

## Frozen execution and budgets

A neutral manifest binds the task, source snapshot, model declaration, prompt version, budgets and repeat. A separate control record binds gold/rubric and implementation digests, seed, interleaved order and unique output positions. A cell is claimed before dispatch and closed from durable results and request receipts. Source, model or fingerprint drift, missing evidence and unknown outcomes stop the batch. A directory or batch-name change does not authorize replay.

The public offline runner accepts trusted scripted fixtures and rejects live configuration. Its exact serialized requests and bounded receipts are real test artifacts, but scripted replies are not model-quality measurements. Total timing includes preparation, with provider and tool phases reported separately.

The explicit live entry requires an operator-approved scope and predecessor evidence before reading a credential. It uses the fixed official Chat Completions destination and single concurrency. Approval records are operator-supplied consent records, not cryptographic proof of human authorization. Credentials must never appear in command arguments, the repository, persistent new configuration, traces or logs; the provided CLI uses terminal no-echo input. This is not an unattended live runner.

The tested pilot profile planned 12 cases × 4 arms × 1 repeat: 48 cells, at most 84 model requests, with per-cell planning limits of 24,000 input and 6,000 output tokens. A/B can output up to 6,000 tokens in one call, C up to 3,000 per call, and D up to 2,000 per call. Equal total output budgets do not imply equal truncation risk. Input planning is not trusted pre-dispatch token metering.

All historical generations shared a persistent total of at most 84 requests and a $3 local budget. Verified prior receipts are carried forward; a successor cannot obtain a fresh allowance by changing directories. The local reservation was $0.33 before each request under the frozen model, context and peak-price assumptions. Trusted response usage settles that reservation; absent or inconsistent usage retains it and halts execution. Local timeout does not prove remote billing stopped. These controls do not impose a supplier-side hard spending cap. Recheck the [official model/pricing](https://api-docs.deepseek.com/quick_start/pricing/) and [usage contract](https://api-docs.deepseek.com/api/create-chat-completion/) before any later approved run.

## Failure evidence and current limits

The diagnostic candidate adds a separate, validated failure-evidence record, bounded to 4,096 bytes. It records a stage and selected safe HTTP metadata, fixed field types/counts, allowlisted model/finish values and bounded usage observations. It does not save response/error bodies, reasoning, tool arguments, unknown key names or arbitrary headers. Correlation IDs are optional, narrowly filtered HTTP metadata; credentials and credential fragments are rejected. Metadata resets for every request.

Diagnostic usage is untrusted observation, not settlement evidence. The runner can retain this metadata while the response remains `unknown`; it cannot turn a malformed response into a valid answer or release a reservation. The upgrade cannot recover an already discarded response. The stopped live D request still lacks sufficient evidence to distinguish JSON decoding from provider-envelope validation.

Verification counts and measured execution are recorded separately in the [results](deepseek-policy-results.md). No measured tool benefit, single-version complete live four-arm run, unseen generalization, target-code execution or VM acceptance is claimed. See the [run checklist](deepseek-policy-pilot-checklist-v1.md) for execution and reconciliation limits.
