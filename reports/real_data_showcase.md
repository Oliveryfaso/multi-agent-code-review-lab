# Real Data Testing Showcase

## Current Status

This project has completed external-data adapter smoke tests and one small external repository eval. It has not yet run a full SWE-bench-style benchmark suite.

| Layer | Status | What Was Verified |
| --- | --- | --- |
| SWE-bench format adapter | passed | Converts `problem_statement`, `patch`, `test_patch`, `FAIL_TO_PASS` into MACR eval JSONL with expected files, symbols, and test selector. |
| CodeSearchNet format adapter | passed | Converts docstring/query, file path, and function name into a code-search eval case. |
| GitHub issue JSONL adapter | passed | Converts curated issue title/body plus manual file/symbol oracle into a real-issue localization case. |
| Full external repo eval | passed | Ran 4 curated GitHub issue-style cases against local `pallets/markupsafe` checkout. |
| Real patch verification benchmark | not run | Requires repo-specific dependency setup, test commands, sandboxing, and timeout control. |

## MarkupSafe External Repo Eval

| Metric | Value |
| --- | ---: |
| repo | `pallets/markupsafe` |
| case_count | 4 |
| task_success_rate | 1.0 |
| file_hit_rate | 1.0 |
| symbol_hit_rate | 1.0 |
| final_review_pass_rate | 1.0 |
| human_review_required_rate | 0.0 |
| tool_call_failure_rate | 0.0 |
| empty_recall_rate | 0.02 |
| avg_code_smell_ratio | 0.031 |

Report: `reports/real_markupsafe_eval.md`

## Representative Cases

| Source | Representative Task | Offline Oracle |
| --- | --- | --- |
| SWE-bench Lite style | Fix crash from a natural-language issue statement. | Gold patch files and test patch files become expected localization targets. |
| CodeSearchNet style | Map a natural-language docstring to a function. | `path` and `func_name` become expected retrieval targets. |
| GitHub issue style | Diagnose a curated issue with known affected files. | Maintainer-provided or manually labeled files/symbols become expected targets. |

## Why This Is Not Yet A Full Benchmark Suite

The adapter tests prove that public data formats can enter the evaluation pipeline. The MarkupSafe run proves the pipeline can evaluate a real external checkout on curated issue-style cases. It does not claim full SWE-bench-scale issue-to-patch performance.

An external repo test was run after the repository was downloaded locally:

```bash
python3 cli/agent_review.py eval \
  --eval-file eval_sets/real_markupsafe_issues.jsonl \
  --repo-root . \
  --report reports/real_markupsafe_eval.md
```

To run full external data tests, add:

1. Local repository checkout in `external_repos/`.
2. Repo mapping in `external_data/repo_map.json`.
3. Converted eval JSONL in `eval_sets/real_*.jsonl`.
4. Eval report in `reports/real_*.md`.

## Next Command

```bash
python3 cli/agent_review.py prepare-real-eval \
  --source github-issue \
  --input external_data/github_issues.jsonl \
  --repo-map external_data/repo_map.json \
  --output eval_sets/real_github_issues.jsonl
```
