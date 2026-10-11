# Next debugger validation

The development fixtures in `tests/test_static_locate_abstention.py` are three newly authored, inert source strings: a Unicode code-point counter, a counter that incorrectly counts UTF-8 bytes, and a quote function whose external tariff implementation and pricing unit are absent. They reuse the actual diagnosis parser, captured-response comparison and static-locate API with scripted responses. Duplicate references must be rejected without deduplication; a legal `unknown` must remain `semantic_pending` without independent review; `insufficient_evidence` must retain a concrete gap and create no hypothesis, triggers or citations. Analysed source is never executed.

These are development checks of application behavior. They measure no model quality, GBNF generation compatibility, native-service reliability or generalization. The previous 24 captured answers, prompts, holdout sources and accepted scores remain unchanged.

Before a future model run, obtain approval and freeze **12 new unseen examples per model**: four defects, four healthy implementations and four cases with genuinely missing evidence. Both models receive the same 12 inputs. Use new source bytes and IDs, excluding both the old holdout and these development fixtures; freeze the rubric separately from requests. Reserve these future examples until the implementation and prompts are fixed. Do not tune on their answers or substitute repaired/retried answers.

Preregister two hypotheses:

- **Structure and references:** on the new inputs, explicit source-bounded, unique citation instructions produce accepted contracts with legal references; report the actual coverage. GBNF syntax acceptance is measured separately; it cannot alone establish source bounds, uniqueness or semantic support. The old 24 answers are descriptive evidence, not a paired improvement baseline for different examples.
- **Healthy and insufficient evidence:** source-supported healthy mechanisms do not produce defect claims; missing external facts produce a specific abstention rather than an invented cause. A syntactically legal healthy/unknown answer still needs independent causal or gap review.

For each model, report planned completion against the fixed denominator **12**, with each category against **4**. Also publish captured responses, infrastructure failures, uncertain cells and not-attempted cells. The existing quality denominator is captured non-infrastructure responses; show its actual size beside every quality rate. Contract failures stay in that denominator. Incomplete coverage must remain visible against 12, rather than silently replacing the planned denominator.

Keep outcome counts distinct: `contractRejected` (invalid JSON, fields or citations), `semanticPending` (accepted contract without bound review), `semanticFail` (reviewed but unsupported/wrong verdict, mechanism, trigger or gap), and reviewed passes. Report strict JSON and complete contract validity separately. Bind every review to the captured row hash, use the frozen four review dimensions, and publish no completed diagnostic rate while a valid answer awaits review. For healthy false alarms, give the numerator and contract-valid healthy denominator plus its coverage out of four; contract rejection is neither a semantic false alarm nor evidence of a correct healthy answer.

Any improvement claim requires that approved future run and complete evidence. Passing the present scripted fixtures is only an application regression result; it does not turn development examples into an independent holdout.
