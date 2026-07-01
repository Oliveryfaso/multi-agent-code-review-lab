#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="${PYTHONPATH:-src}:src"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/private/tmp/macr_pycache}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/macr_demo_ready.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "== Multi-Agent Code Review Lab demo readiness check =="
echo "Repo: $ROOT_DIR"
echo

echo "1/6 Compile Python sources"
python3 -m compileall -q src tests scripts

echo "2/6 Run unit tests"
python3 -m unittest discover -s tests

echo "3/6 Run sample codebase question"
python3 cli/agent_review.py ask \
  --repo sample_repos/sample_python_api \
  "这个接口在哪里鉴权？" \
  > "$TMP_DIR/ask.txt"
grep -q "Multi-Agent Review Result" "$TMP_DIR/ask.txt"
grep -q "Trace:" "$TMP_DIR/ask.txt"

echo "4/6 Run offline eval smoke"
python3 cli/agent_review.py eval \
  --eval-file eval_sets/phase1.jsonl \
  --repo-root . \
  --report "$TMP_DIR/phase1_eval.md" \
  > "$TMP_DIR/eval.json"
grep -q "task_success_rate" "$TMP_DIR/eval.json"
grep -q "file_hit_rate" "$TMP_DIR/eval.json"

echo "5/6 Run patch verification smoke"
python3 cli/agent_review.py patch \
  --repo sample_repos/sample_python_api \
  --test-selector tests \
  --out-dir "$TMP_DIR/patches" \
  "银行卡扣款失败会在哪里返回 402？请给出最小修复" \
  > "$TMP_DIR/patch.txt"
grep -q "Patch:" "$TMP_DIR/patch.txt"
grep -q "Verification:" "$TMP_DIR/patch.txt"

echo "6/6 Run diff review smoke"
cat > "$TMP_DIR/sample.diff" <<'DIFF'
diff --git a/backend/payments.py b/backend/payments.py
--- a/backend/payments.py
+++ b/backend/payments.py
@@ -1,3 +1,6 @@
+def debug_charge(payload):
+    print(payload)
+
 def charge_card(card_token, amount):
     return {"ok": True}
DIFF
python3 cli/agent_review.py review-diff \
  --repo sample_repos/sample_python_api \
  --diff-file "$TMP_DIR/sample.diff" \
  > "$TMP_DIR/diff.txt"
grep -q "Multi-Agent Diff Review" "$TMP_DIR/diff.txt"
grep -q "Review Comments" "$TMP_DIR/diff.txt"

echo
echo "Demo readiness check passed."
echo "Latest trace is available at traces/latest.json."
echo "Start the local viewer with:"
echo "  python3 cli/agent_review.py view --port 8765"
