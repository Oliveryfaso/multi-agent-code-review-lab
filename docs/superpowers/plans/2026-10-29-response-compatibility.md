# Provider response compatibility and independent billing evidence

**Goal:** Normalize usable provider response fields without weakening action validation, and retain independently validated usage when an assistant message fails parsing.

**Architecture:** Normalize only incoming provider messages into the existing strict internal conversation contract. Extension metadata is not forwarded or copied into traces. A separate fixed-schema, request-bound billing envelope carries canonical usage independently of assistant usability; policy failure still stops the batch.

**Tech stack:** Existing Python standard library, fake transports and bounded offline test runner. No dependency, service, model request or credential access is added.

**Spec:** The requested offline repair: protect frozen runs and unknown holds, tolerate inert response metadata, strictly validate actions and duplicate fields, improve bounded failure categories, and separate billing from diagnosis validity.

## Approach and constraints

Continuing to reject every extension preserves the observed interoperability failure. Installing an SDK would add a dependency and would still require canonical action validation. The selected approach is a small provider-boundary normalizer; strict internal/request validation remains unchanged.

- No paid requests, unknown replay, fee reclassification, batch creation or frozen-source/result edits.
- Historical failure diagnostics remain observations, never newly promoted into billing evidence.
- Preserve role, required types, IDs and uniqueness, declared tool names, argument-object syntax/duplicate-key checks and caller-owned argument schemas. Normalize at most 64 calls and at most 64 KiB per arguments string within the existing 1 MiB wire cap.
- Incoming duplicate JSON keys are rejected, including nested function arguments; no last-key-wins behavior.
- Unknown extension values are discarded without deep-copying them. Required message/reasoning/argument strings are preserved exactly for replay.
- Billing evidence requires a clean provider envelope, expected model, request SHA-256, positive internally consistent canonical usage and the request output cap. Missing, inconsistent, ambiguous or unmatched evidence keeps the reservation and stops.
- A validated billing envelope is not a usable assistant response, task success or a supplier bill. New future parsing failures may settle only from that envelope; old unknowns remain unknown.

## Interfaces and tasks

1. `src/macr/providers/response_normalization.py`: `ResponseNormalizationError(code)`, `decode_provider_json(raw)`, `normalize_assistant_message(message, *, tool_names)`; return a copied canonical message, retain no extension values. Tests first: metadata round trip, required fields, bad/duplicate IDs, names, role, count, argument JSON duplicates, raw JSON duplicates and ignored sensitive metadata.
2. `src/macr/providers/base.py`: `validate_billing_evidence(value)` and optional validated/copy-isolated `ProviderError.billing_evidence`. Fixed envelope keys: schema `macr.provider.billing.v1`, provider, model, request_sha256, max_output_tokens, raw_usage, latency_ms. Tests first: positive usage/caps and request binding, invalid/missing/boolean/inconsistent counters, extension-value exclusion and mutation isolation.
3. `src/macr/providers/failure_evidence.py`: fixed validation-error codes and bounded known-field structure/type metadata, with no raw extension names/values, contents or credentials. Existing failure v1 records remain readable.
4. Provider integration: raw duplicate rejection; extract billing evidence before assistant normalization; attach it to parse errors without manufacturing a validated response. Fake-wire tests cover both success and billing-known/policy-invalid cases.
5. Runner/live/receipt integration: record billing evidence separately, bind to the actual reserved request, settle only valid bound usage, retain parsing failure and stop. Version new accounting-aware runner records so old registry outcomes retain their original meaning. Validate durable billing evidence in new receipts/carry proofs without interpreting historical diagnostic counters.
6. Complete related offline regressions plus independent code/security/experimental-contract review. Publish only reviewed code/tests/docs under existing authorization; live continuation remains a separate decision.

## Review focus

- Duplicate or shadowed action fields must fail before dispatch.
- Ignored metadata must not reappear in replay, success/failure logs or billing records.
- Billing-only errors must not gain an answer, execute a tool, allow another decision or silently release an unmatched reservation.
- Missing/invalid usage and transport uncertainty must still retain the full reservation.
- Historical receipt bytes, states, proofs and stopped batches must remain intact.
