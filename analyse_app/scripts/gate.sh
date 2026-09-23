#!/usr/bin/env bash
# #650 — the identifier gate. Runs the suite, then scans everything it
# printed. Exits non-zero on a hit, so this is a BUILD GATE and not a report
# somebody reads later.
#
# Scans OUTPUT, not source: test files legitimately contain concept guids and
# forbidden-value sentinels. What must never appear is one of them in
# something the code produced.
set -uo pipefail
cd "$(dirname "$0")/.."

OUT=$(mktemp -t analyse-gate)
trap 'rm -f "$OUT"' EXIT

.venv/bin/python -m pytest -q "$@" >"$OUT" 2>&1
STATUS=$?
tail -3 "$OUT"

if [ "$STATUS" -ne 0 ]; then
  echo "gate: tests failed — not scanning output of a failing run" >&2
  exit "$STATUS"
fi

.venv/bin/python - "$OUT" <<'PY'
import sys
from app.testing import scan_paths

findings = scan_paths([sys.argv[1]])
if findings:
    print(f"\ngate: FAIL — {len(findings)} identifier-like value(s) in test output:",
          file=sys.stderr)
    for f in findings[:20]:
        print(f"  {f}", file=sys.stderr)
    sys.exit(1)
print("gate: clean — no identifier-like values in test output")
PY
