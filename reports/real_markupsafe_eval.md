# Eval Report

| Metric | Value |
| --- | ---: |
| case_count | 4 |
| task_success_rate | 1.0 |
| file_hit_rate | 1.0 |
| symbol_hit_rate | 1.0 |
| avg_expected_file_recall | 0.5 |
| avg_expected_symbol_recall | 0.458 |
| avg_evidence_count | 12.0 |
| avg_tool_calls | 12.25 |
| tool_call_failure_rate | 0.0 |
| empty_recall_rate | 0.02 |
| final_review_pass_rate | 1.0 |
| human_review_required_rate | 0.0 |
| avg_code_smell_ratio | 0.031 |

## Cases

| ID | Success | Final Review | Human Review | Code Smell | File Hit | Symbol Hit | File Recall | Symbol Recall | Evidence | Tool Calls | Empty Recall | Failed Tools |
| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| markupsafe_issue_escape_proxy | True | True | False | 0.031 (low) | True | True | 0.5 | 0.333 | 12 | 12 | 0 | 0 |
| markupsafe_issue_native_escape_chars | True | True | False | 0.031 (low) | True | True | 0.5 | 0.5 | 12 | 13 | 1 | 0 |
| markupsafe_issue_format_custom_html | True | True | False | 0.031 (low) | True | True | 0.5 | 0.5 | 12 | 12 | 0 | 0 |
| markupsafe_issue_striptags_comments | True | True | False | 0.031 (low) | True | True | 0.5 | 0.5 | 12 | 12 | 0 | 0 |
