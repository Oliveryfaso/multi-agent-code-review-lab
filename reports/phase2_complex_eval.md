# Eval Report

| Metric | Value |
| --- | ---: |
| case_count | 30 |
| task_success_rate | 1.0 |
| file_hit_rate | 1.0 |
| symbol_hit_rate | 1.0 |
| avg_expected_file_recall | 0.947 |
| avg_expected_symbol_recall | 0.913 |
| avg_evidence_count | 11.833 |
| avg_tool_calls | 10.9 |
| tool_call_failure_rate | 0.0 |
| empty_recall_rate | 0.095 |
| final_review_pass_rate | 0.9 |
| human_review_required_rate | 0.1 |
| avg_code_smell_ratio | 0.0 |

## Cases

| ID | Success | Final Review | Human Review | Code Smell | File Hit | Symbol Hit | File Recall | Symbol Recall | Evidence | Tool Calls | Empty Recall | Failed Tools |
| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| complex_auth_001 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 10 | 1 | 0 |
| complex_auth_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 0 | 0 |
| complex_auth_003 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 11 | 3 | 0 |
| complex_auth_004 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 10 | 2 | 0 |
| complex_order_001 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 10 | 0 | 0 |
| complex_order_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 13 | 1 | 0 |
| complex_order_003 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 9 | 0 | 0 |
| complex_order_004 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 11 | 0 | 0 |
| complex_payment_001 | True | True | False | 0.0 (low) | True | True | 0.333 | 0.4 | 12 | 7 | 0 | 0 |
| complex_payment_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 9 | 0 | 0 |
| complex_payment_003 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 1 | 0 |
| complex_payment_004 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 9 | 2 | 0 |
| complex_notify_001 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 7 | 0 | 0 |
| complex_notify_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 0.75 | 12 | 11 | 1 | 0 |
| complex_graph_001 | True | True | False | 0.0 (low) | True | True | 0.333 | 0.333 | 12 | 9 | 2 | 0 |
| complex_graph_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 0.75 | 12 | 11 | 0 | 0 |
| complex_graph_003 | True | True | False | 0.0 (low) | True | True | 0.75 | 0.75 | 12 | 15 | 1 | 0 |
| complex_patch_001 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 1 | 0 |
| complex_patch_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 7 | 0 | 0 |
| complex_patch_003 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 9 | 1 | 0 |
| complex_uncertain_001 | True | False | True | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 15 | 4 | 0 |
| complex_uncertain_002 | True | False | True | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 2 | 0 |
| complex_uncertain_003 | True | False | True | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 15 | 5 | 0 |
| complex_mixed_001 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 13 | 0 | 0 |
| complex_mixed_002 | True | True | False | 0.0 (low) | True | True | 1.0 | 0.667 | 7 | 8 | 0 | 0 |
| complex_mixed_003 | True | True | False | 0.0 (low) | True | True | 1.0 | 0.75 | 12 | 13 | 1 | 0 |
| complex_mixed_004 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 1 | 0 |
| complex_mixed_005 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 0 | 0 |
| complex_mixed_006 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 11 | 0 | 0 |
| complex_mixed_007 | True | True | False | 0.0 (low) | True | True | 1.0 | 1.0 | 12 | 12 | 2 | 0 |
