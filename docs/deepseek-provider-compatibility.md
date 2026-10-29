# DeepSeek response compatibility and usage evidence

The conversation adapter normalizes incoming provider messages into the existing strict internal contract. It retains only role, content, reasoning, tool IDs/types, function names and argument strings. Inert extension metadata at message, call and function levels is discarded, without copying its values into replay or traces. This is a provider-boundary operation; outbound conversation validation remains strict.

Required action fields still need valid types, declared function names, unique IDs, assistant role, bounded call count and JSON-object arguments. Argument schemas and read-only scope remain the caller's responsibility. Duplicate JSON keys, including nested argument keys, fail instead of using last-key-wins behavior. Existing valid ASCII tool names remain supported. The provider caps normalization at 64 calls and 64 KiB per arguments string; the policy loop's tighter read/decision limits still apply.

## Official references and uncertainty

The [DeepSeek Chat Completions schema](https://api-docs.deepseek.com/api/create-chat-completion/) lists the core non-streaming tool fields. Its [thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/) shows SDK tool-call objects with additional `index` metadata and requires reasoning to be passed back when tools are in use. The [SDK model implementation](https://github.com/openai/openai-python/blob/main/src/openai/_models.py) permits extra response fields. These references were checked on 2026-10-29; no SDK dependency is added here.

The stopped recovery request retained evidence of one extra tool-call field, not its name or value. A synthetic `index=0` example reproduces the older strict rejection, but does not prove that the actual missing field was `index`. The normalizer repairs that class of interoperability issue without reconstructing or rescoring discarded responses.

## Billing is separate from assistant usability

After a clean provider JSON envelope arrives, the conversation adapter can validate canonical usage before parsing the assistant. A fixed billing envelope binds the expected provider/model, actual request SHA-256, output cap, positive consistent usage counters and latency. It contains no assistant text, arguments, unknown metadata or credentials. Missing counters, ambiguous wire-envelope JSON keys, invalid cache/reasoning arithmetic, output-cap violations or model mismatch produce no accepted billing envelope. Invalid JSON inside a tool-arguments string can make the assistant unusable while independent envelope usage remains valid.

A parsing error may therefore have independent billing evidence while its assistant response remains unavailable. The live reservation gate revalidates the request binding before settling conservative local usage costs. Diagnostic counters alone never settle a reservation. Transport uncertainty and unmatched or invalid evidence keep the full reservation.

The runner records billing evidence separately, without manufacturing an answer or marking an unavailable assistant as received. New accounting-aware records distinguish `batch_halted_contract` from an unknown outcome. Both halt further execution, including before another credential read. Older records without the billing contract marker keep their original interpretation. The legacy `complete` interface is otherwise unchanged.

## Failure records and historical limits

Failure metadata stays within the existing 4,096-byte cap. Fixed validation-error codes and allowlisted first-call field kinds identify the failed checks; known `index` presence/type can be observed. Extension names/values, response text, arguments and arbitrary headers are not failure diagnostics. Retention of a valid response in an authorized private run is governed by its separate log policy.

Historical diagnostics are not retroactive billing evidence. The two unresolved reservations totaling USD 0.66 remain held; their supplier charges are unverified. The stopped recovery batch cannot be restarted by updating this adapter, copying a directory or changing a version label. Another paid continuation needs its own reviewed decision and approval; this patch does not grant it or establish a tool benefit.
