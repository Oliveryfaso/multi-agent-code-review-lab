# Explicit pytest inputs

The normal `repair` CLI now accepts `macr.repair.task.v2`. It has the same
fields as task v1, plus this required `test_plan`:

```json
{
  "schema": "macr.repair.test-plan.v1",
  "framework": "pytest",
  "nodeids": [
    "tests/test_example.py::test_value",
    "tests/test_example.py::TestValues::test_boundaries[case-id]"
  ]
}
```

Task v1 keeps its existing unittest behavior. A v2 plan cannot be null or empty,
and never falls back to unittest. Use the existing checkout command with a
hash-bound v2 task and explicit `--proposal-contract v6`; no pytest import or
new dependency is needed for static admission. This does not run pytest.

The plan contains 1–32 unique, exact collection arguments, each at most 1,024
UTF-8 bytes. Only a function or a direct class method can be selected. Its file
must belong to the committed, protected `test_paths`; the plan cannot enlarge
the read/edit scope. Protected helper files may be unselected. Broad file/class
selection, directories, globs, shell flags, `-k` expressions and dynamic names
are not expanded. Normal source and whole-task JSON limits still apply.

The parser checks complete original files and static declarations without
importing the repository, pytest, decorators or fixtures. Known parametrization
requires an explicit caller-frozen `[id]`. The ID is a requested collection
identity, not proof that pytest will generate it. Parameter values, plugins,
fixture dependencies, async support and actual collection need qualification
by a real backend. Dynamic/rebound/ambiguous declarations are rejected.

### Explicit nondefault names

Use the same three fields with `schema: "macr.repair.test-plan.v2"` when the
caller freezes exact nodeids that use different naming conventions, for example
`checks.py::InvoiceCase::testTotal`. Every class/function/method name must still
be an identifier and match a direct, unambiguous declaration in the complete
protected source. Inherited or dynamically generated declarations are not
expanded. Whole-file limits and explicit parameter-ID requirements remain.

This version removes naming-prefix assumptions only. Pytest can use configured
class/function patterns, and `unittest.TestCase` discovery does not use those
patterns; see the official [collection naming documentation](https://docs.pytest.org/en/stable/example/pythoncollection.html).
V2 does not inspect configuration, plugins or base-class behavior and does not
prove the requested names will be collected. A qualified verifier must establish
actual collection; missing or unrun tests cannot count as passes. Bare files,
classes, directories, command flags and multiple commands remain unsupported.
V1 keeps its original naming rules and serialized requests unchanged.

These nodeid forms follow the [pytest collection argument
documentation](https://docs.pytest.org/en/stable/how-to/usage.html#specifying-which-tests-to-run).
The new CheckSpec uses `argv_profile="pytest"` and the exact selectors; it
does not relabel pytest as unittest. No raw shell command or plugin option is
passed through. A future backend still needs its own approved interpreter,
invocation form, environment and isolation profile.

## Binding and fair comparisons

The same plan is bound into the task, durable spend cell, common initial context,
serialized provider request, loop claim, CheckSpec and pending scoring packet.
Reload reconstructs that contract and rejects altered selectors/specs. Historical
candidate selection retains the plan even when its final receipt has no new
check. It stays `not_rerun`; a previous pass never becomes a fresh pass.

All three comparison strategies receive the same complete source and public
test plan under the same model/call/token/dollar ceilings. A no-feedback direct
reference still receives no test outcomes; the strong iterative baseline gets
the same public feedback as Debugger. Freeze the same plan for every arm:
changing selectors changes task/context/spend commitments. Different selected
test sets are not equivalent experiments merely because their source bytes match.

No-plan task/config/request/packet bytes remain on the legacy path. The existing
real-verifier gate and remote-default-off behavior are unchanged. Without a
qualified backend, feedback strategies stop before the spend ledger or Key is
opened; synthetic replay remains explicitly synthetic. This feature establishes
input-contract support, not real pytest execution, repair success or model gains.

## Whole files and benchmark boundaries

Test files are never sliced, truncated, rewritten or replaced by selected test
bodies. The original per-file 16 KiB, AST, total 64 KiB and 4,096-line limits
remain. Selecting one nodeid cannot bypass them. Large-file handling therefore
remains unsupported where those bounds are exceeded; do not advertise those
cases as newly accepted. Request and snapshot verification retain all source
bytes and protect test edits.

BugsInPy's fixed-revision test overlay must stay separate from its buggy-version
probe and from independently hidden final scoring. The v2 public task only
contains tests explicitly authorized for the generation/public-feedback view.
It does not automatically import BugsInPy metadata, fixed commits or hidden tests.
The separate [whole-file scoring overlay](repair-scoring-overlay.md) can carry
complete evaluator files without placing them in model context. It does not
raise these generation limits, select tests or enable a verification backend.
