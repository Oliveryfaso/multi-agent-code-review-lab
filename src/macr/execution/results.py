"""Normalize observations without inventing test execution or successful repairs."""
from macr.investigation.records import CheckResult, CheckSpec, RunError


def classify_check(observation: dict, check: CheckSpec) -> CheckResult:
    execution = observation.get("execution_status", "blocked")
    allowed = {"completed", "timeout", "resource_limit", "error", "blocked", "cancelled", "outcome_unknown"}
    if execution not in allowed:
        execution = "error"
    exit_code = observation.get("exit_code")
    if exit_code is not None and type(exit_code) is not int:
        exit_code = None
    counts = observation.get("test_counts")
    observed = "unknown"
    if execution == "blocked":
        observed = "not_applicable"
    elif execution == "completed":
        if check.purpose != "test":
            observed = "passed" if exit_code == 0 else "failed" if exit_code is not None else "unknown"
        elif isinstance(counts, dict) and counts and all(type(v) is int and v >= 0 for v in counts.values()):
            actual = sum(counts.get(k, 0) for k in ("passed", "failed", "errors", "xfailed", "xpassed"))
            errors = counts.get("errors", 0) + counts.get("collection_errors", 0)
            total = actual + counts.get("skipped", 0)
            if counts.get("executed", actual) != actual or counts.get("collected", total) < total:
                observed = "unknown"
            elif errors or counts.get("failed", 0) or counts.get("xpassed", 0) or (exit_code not in (None, 0)):
                observed = "failed"
            elif total == 0:
                observed = "zero_tests"
            elif actual == 0:
                observed = "skipped"
            elif exit_code == 0:
                observed = "passed"
        else:
            counts = None
    expectation = "not_requested"
    if check.prediction is not None:
        expected = check.prediction.get("observed_status")
        expectation = "inconclusive" if expected is None or observed in ("unknown", "not_applicable") else "matches" if expected == observed else "contradicts"
    error = None
    if execution in ("blocked", "error", "timeout", "resource_limit", "outcome_unknown"):
        error = RunError("environment_missing" if execution == "blocked" else execution)
    return CheckResult(check.check_id, execution, observed, exit_code=exit_code, test_counts=counts,
                       stdout_ref=observation.get("stdout_ref", ""), stderr_ref=observation.get("stderr_ref", ""),
                       expectation_result=expectation, environment_ref=observation.get("environment_ref", ""),
                       limits_observed=observation.get("limits_observed", {}),
                       output_truncated=bool(observation.get("output_truncated", False)), error=error)
