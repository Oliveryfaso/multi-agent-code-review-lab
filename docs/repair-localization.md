# Finding candidate files from an issue

`repair-locate` fills the preparation gap between an issue and a scoped repair
input. It proposes source files using local static evidence; it does not run a
model, generate a patch, execute tests or confirm a root cause.

```sh
python3 -I -B cli/agent_review.py repair-locate \
  --repo /path/to/authorized/discovery-source \
  --issue-file /path/to/public-issue.txt \
  --exclude 'tests/**' --exclude 'scoring/**' \
  --max-candidates 8 --out /separate/private-parent/new-localization
```

The discovery root is a separate, explicit local read scope. It can be larger
than the files later given to a model. Use only source authorized for discovery;
private scoring material must be outside that root or excluded before reading.
The command cannot identify unmarked answer content by meaning. It does not
expand an existing task-v3 manifest or follow paths mentioned in issue text.

## Evidence and selection

The command captures one sanitized Python-only snapshot and builds the existing
qualified index. Non-Python text/config files, known private/secret paths,
symlinks and caller exclusions are filtered before their content is read. The
same profile is used for snapshot inventory. Limits are 256 Python files,
512 KiB/file, 4 MiB total and 65,536 lines. Exceeding them rejects the discovery;
these are input bounds, not an OS execution sandbox.

Candidate ranking uses bounded lexical issue anchors, split identifier names,
symbols/signatures and actual source/error-text matches. Evidence records the
captured path, location, matched terms and full-file identity. Repeated words
are not repeated evidence. At most one hop of explicitly resolved static call
relations can add candidates; unresolved references are not treated as calls
that ran. No learned semantics, embedding service, old benchmark-specific
translation table or alphabetical fallback is used.

`candidate_algorithm: lexical-static-path-anchor-v2` also recognizes case-sensitive
`.py` path mentions made from word characters, dots and hyphens, with `/` or `\`
separators. Exact captured relative paths and unique bare basenames receive one
per-file boost above the bounded term/phrase score. Windows paths containing `\`
may match a unique captured relative suffix containing a directory; POSIX prefixes
are not stripped. Qualified unknown paths never fall back to a basename.
Traversal components and extension continuations such as `.pyc` are rejected.
Ambiguous basenames/suffixes expose gaps without a boost. Repetition and alternate
spellings do not multiply the boost. Each anchor records the original issue text,
SHA-256, one-based line/column and zero-based half-open character span, resolution
kind and captured file identity. These mentions remain lexical evidence: they
grant no read authority and establish no runtime frame or diagnosis. Existing
term/phrase scores, call-hop rules and selection budgets remain in effect.

No matching evidence returns `inconclusive`. Parse/index/read gaps remain
visible, and no source inventory is offered as complete when discovery is
incomplete. Ties, bounded result truncation and generation-size limits remain
visible. An oversized top candidate is not truncated or silently replaced with
a smaller file merely to make a repair input pass.

The matching is lexical: a paraphrase with no shared identifiers, source terms
or quoted error may remain inconclusive. A match can point at healthy code or an
unrelated cause. Static call relations do not establish a runtime call chain.

## Handoff to the existing repair entry

`proposal.json` contains candidate files/evidence, snapshot identity, coverage,
gaps and a proposed `source_files` hash map when that whole selected set fits
the existing generation source contract. It always has `task_ready: false`:
the locator does not choose or grant `editable_paths`, public `test_paths`, test
selectors or model/network authority, and does not create a ready task file.

After reviewing candidates and explicitly choosing those roles, use their
full-file hashes in a normal [task-v3 input](repair-source-scope.md), together
with the independently selected public tests and issue. The normal `repair`
loader checks the live bytes against those commitments again. Adding tests may
exceed the combined source limits and must pass that normal loader; the locator
does not bypass it. A changed checkout is not silently accepted.

For fair comparisons, freeze that same confirmed view and permissions for all
repair arms. Localization preparation itself is not evidence of Debugger repair
gain; comparing different views would measure retrieval as well as repair policy.
The existing repair `hypothesis_id` remains an issue binding, not an endorsed
root-cause diagnosis.

## Local artifacts and execution boundaries

`--windowed-input` additionally proposes [task-v4 line windows](repair-windowed-source.md)
for bounded large originals. It preserves complete source hashes and explicit
omissions, and leaves the existing strict `source_files` result unchanged.
Public tests and edit authority are still separate; `task_ready` stays false.

The new output is exclusive and private (0700 directories, 0600 files), outside
the source root. It retains the complete authorized discovery snapshot, index,
claim, scope summary and proposal. It may therefore contain more local source
than the final candidates. Nothing is uploaded, deleted or dispatched. Partial
failed output stays failed and cannot be silently overwritten or resumed.

The index uses an explicit cache root bound to its own snapshot store; there is
no fallback into the shared global cache on failure. Existing default index and
repair behavior remain unchanged. Real verification is still unavailable without
the existing separately qualified backend. No model autonomy, isolation, test
success or quality/cost gain is established by this preparation command.
