# Twelve new bounded source cases: native measurement and attribution

On 2026-10-11, cached Qwen3-4B-Instruct-2507 Q4_K_M and Qwen2.5-Coder-7B-Instruct Q4_K_M each completed twelve real requests. Both achieved **5/12 diagnostic passes**, with **12/12 strict JSON and 12/12 host-contract passes**. These results do not establish a general model ranking or a measured benefit from the subsequent agent changes.

The [published results](local-model-new-samples-results.json) retain all 24 decoded answers, raw response content, usage, independent semantic reviews and capture bindings. The [cases](../evals/diagnostic-new-samples-v1/cases.json), [reference](../evals/diagnostic-new-samples-v1/reference.json) and [rubric notes](../evals/diagnostic-new-samples-v1/rubric-notes.md) were authored, reviewed and frozen before outputs. Each model received four defects, four healthy cases and four missing-evidence cases. These inputs were separate from the old holdout and development inputs; the prefix-removal topic overlaps a development family, so this is not a topic-disjoint benchmark. The source snippets were inert data and were not executed.

| Measure | 4B | 7B |
| --- | ---: | ---: |
| Captured / independently reviewed | 12/12 | 12/12 |
| Strict JSON | 12/12 | 12/12 |
| Host field and reference contract | 12/12 | 12/12 |
| Diagnostic pass | 5/12 | 5/12 |
| Defect cases passed | 3/4 | 1/4 |
| Healthy cases passed | 2/4 | 1/4 |
| Healthy false alarms, contract-valid denominator | 2/4 | 3/4 |
| Correct missing-evidence abstentions | 0/4 | 3/4 |

A diagnostic pass requires the frozen verdict plus supported mechanism, trigger and citations, or a complete abstention reason for an unknown case. A correct explanation with a contradictory verdict fails. Healthy false-alarm rates use only contract-valid healthy answers; a contract failure is not automatically a false alarm.

## What the outputs establish

The remaining failures are semantic despite valid JSON and citations. In ns01, 4B correctly explains the OR-versus-AND authorization defect but labels it healthy. In ns04, 7B describes the generator-versus-tuple mismatch but also labels it healthy. In ns05, 7B treats returning a new reversed tuple as mutation of the immutable input. In ns11, it invents mutable or concurrently modified frozensets. In ns03 and ns06, 4B supplies durability or consistency guarantees absent from the source and stated assumptions. These are directly observable failures to apply the supplied code and requirements.

This native evaluation path sends a frozen diagnostic prompt to the local HTTP provider, captures outputs and applies independent reviews. It does not run `Orchestrator` or `FinalReviewAgent`. A defect in those agent components therefore cannot explain this batch's 5/12 scores. The observations establish failures in the measured model/prompt/constraint combination; they do not isolate intrinsic model capacity from prompt or decoding effects.

The older batch contained seven 4B cells with exact duplicate reference locators and one out-of-source 4B range. Its original scores remain [unchanged](local-model-holdout-results.json): 4B had 12/12 JSON, 4/12 contract and 4/12 diagnostic passes; 7B had 12/12, 11/12 and 4/12 respectively. The explicitly authorized stable-dedup analysis retains the first exact file/start/end locator and changes no verdict, explanation or range. It produces **derived analytical** 4B contract 11/12 and diagnostic 6/12, with only c02 and c11 gaining diagnostic passes. The 7B derived results remain 11/12 and 4/12. These transformed answers are not native captures or measured model improvements. All four original 4B healthy answers were contract-invalid, so its original healthy false-alarm denominator is zero and its rate is unavailable.

The actual old and new wire requests use the same hand-authored GBNF, SHA-256 `cc5ceae80ed6ffe95c7f3d75c32fb9a1f4820789ecc1962ac96fab6477dc2a40`. They do not invoke JSON Schema conversion. The [official b11521 grammar guide](https://github.com/ggml-org/llama.cpp/blob/b11521/grammars/README.md#json-schemas--gbnf) describes a limited converter, including unsupported `uniqueItems`; that converter limitation is not a demonstrated cause here. The hand grammar itself permits repeated locators and positive lines outside supplied snippets. Host validation must enforce uniqueness, ordering and source bounds.

The new prompt explicitly states reference uniqueness/count, true integer lines, complete snippet bounds, field limits and specific unknown handling. All new answers pass the host contract. Both prompt and case set changed, so the difference from the old batch is a correlation rather than an isolated prompt-effect estimate. The published dataset prompt now matches this measured prompt; the host parser, output schema, grammar and scoring rules are unchanged. The prompt's recommended empty refs for unknown answers and empty unknowns for known verdicts are narrower than the host's permitted optional fields.

All 24 new outputs finish with `stop`; maximum output usage is 306 tokens for 4B and 223 for 7B, below the 384-token cap. Several 4B fields end at their decoded 600/300-character limits with incomplete prose. Those are field-boundary observations; reviewers retain the fragments and do not complete them. The fixed grammar emits verdict before explanation. Early verdict commitment and field limits are plausible contributors, but no controlled order or limit ablation was performed.

## Small agent correction and verification

A separate, reproduced agent-design problem existed in `FinalReviewAgent`: counting `len(answer.evidence or evidence)` allowed repeated locators to increase apparent evidence support, empty citations to borrow retrieved evidence, and answer-owned text to affect term coverage. The final orchestrator call also supplied the answer's references as their own authority.

The minimal correction resolves answer references against the existing retrieved `EvidenceStore` keys, counts distinct file/start/end locations, flags references absent from that store, and computes coverage from the authoritative retrieved objects. Different tools' facts at one location remain available for coverage, but count as one location. Empty citations provide no cited evidence. The final orchestrator call now supplies `evidence.items()`; its existing one-retry limit is retained. This checks provenance and coherence and does not validate causal correctness.

Five targeted checks failed against the former behavior before the change. The final evidence-path suite passed 8/8; the allowlisted CLI suite, including runner guard checks, passed 17/17 with no skips. The diagnostic dataset, grammar and comparison suites passed 46/46. A separate public-loader round trip reproduced the frozen twelve cases and reference exactly. Tests use fixtures and forbid model/VM execution; these passes measure software behavior, not model quality. Independent read-only reviews found no blocker in the prompt and evidence-path changes.

The correction follows existing components and adds no dependency, agent framework or automatic repair request. [Pydantic AI output validation](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators) and [output retry budgets](https://pydantic.dev/docs/ai/core-concepts/retries/#output-retries) support separating validation from explicitly bounded repair calls. [LangGraph's workflow guidance](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph) distinguishes transient, model-recoverable and missing-context failures while keeping state inspectable. The current [Anthropic code-review command](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md) uses per-candidate verification against task intent. These are design references; this patch neither imports those frameworks nor treats a model's self-check as independent evidence.

## Runtime, cost and closure

Both services used the verified official llama.cpp b11521 runtime on the same Mac, CPU-only, two threads, parallel one, context 2048, input cap 1536 and output cap 384, explicit ChatML, temperature zero, top-p one and seed zero. The qualified closure contained 62 bound modules and 95 source pins; 59 macr imports consisted of 56 matching published sources plus three exact historical local source pins. The run's source binding predates these new source edits and is preserved; a future native run must qualify its then-current sources again.

| Observed service measure | 4B | 7B |
| --- | ---: | ---: |
| Service starts / generation requests | 1 / 12 | 1 / 12 |
| Service elapsed seconds | 329.208 | 541.592 |
| Sampled peak RSS, GiB | 4.984 | 6.675 |
| RSS guard, GiB | 5.5 | 8 |
| Available memory at start / sampled minimum | 78% / 69% | 71% / 42% |
| Mean HTTP latency, seconds | 26.304 | 43.441 |
| Input / output tokens | 6234 / 2515 | 6234 / 1797 |

The frozen guards included start/run available memory 30%/20%, request 300 seconds, service 5400 seconds with 60 seconds reserved for cleanup, and bounded logs, disk allocation and free space. Native level-5 usage, release, remove-waiting and idle proofs were retained. Each owned PID/listener identity was checked before requests and shutdown. Identity-bound TERM exited zero; owned PID and listener absence, monitor-thread completion and owned temporary-directory cleanup were confirmed, including independent read-only postclosure checks.

This batch consumed **two starts and 24 generation requests**, with no automatic retry, reserved request use, download, external model API request or VM start. Cumulative actual model service starts are **22**, with **15** retained historical VM starts. Both current start allowances are consumed.

The same live owner accepted all capture-bound semantic reviews at finish checkpoints around 329 seconds under this batch's separately approved 1800-second soft reminder. Original outcome and pending evidence remain intact. Acceptance authority is `accepted_at_checkpoints`; `whole_run_compliance` remains `not_assessed`. The generic offline scorer's `real_model_accuracy_measured=false` marker is retained; actual execution is established by native captures and lifecycle evidence rather than rewriting that marker.

Further prompt-order, field-limit or model-quality measurements require a new frozen and authorized run. No such ablation, additional service or generation request was performed during the audit or software correction.
