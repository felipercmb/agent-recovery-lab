#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--installed]" >&2
  exit 2
fi
case "${1:-}" in
  "")
    export PYTHONPATH=src
    demo=("$PYTHON_BIN" -m agent_recovery.demo)
    ;;
  --installed)
    # Do not let the source checkout shadow the package installed by CI.
    unset PYTHONPATH
    "$PYTHON_BIN" - <<'PY'
from importlib.resources import files

if not files("agent_recovery").joinpath("py.typed").is_file():
    raise SystemExit("Installed package is missing its py.typed marker")
PY
    demo=("$("$PYTHON_BIN" -c 'import sysconfig; print(sysconfig.get_path("scripts") + "/agent-recovery-demo")')")
    ;;
  *)
    echo "Usage: $0 [--installed]" >&2
    exit 2
    ;;
esac
REPORT_DIR="${REPORT_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/agent-recovery-reports.XXXXXX")}"
mkdir -p "$REPORT_DIR"
"$PYTHON_BIN" -m unittest discover -s tests -v
"${demo[@]}" --strategy safe --json > "$REPORT_DIR/safe.json"
if "${demo[@]}" --strategy unsafe --scenario after-commit --json > "$REPORT_DIR/unsafe.json"; then
  echo "Expected unsafe strategy to fail, but it succeeded." >&2
  exit 1
else
  status=$?
  if [ "$status" -ne 1 ]; then
    echo "Expected exit code 1 from unsafe strategy; got $status." >&2
    exit 1
  fi
fi
"$PYTHON_BIN" - "$REPORT_DIR" <<'PY'
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
safe = json.loads((directory / "safe.json").read_text())
unsafe = json.loads((directory / "unsafe.json").read_text())
if safe["passed"] is not True:
    raise SystemExit("Safe example did not report passing invariants")
if unsafe["passed"] is not False or len(unsafe["reports"]) != 1:
    raise SystemExit("Unsafe example did not report one failing scenario")
report = unsafe["reports"][0]
if report["scenario"] != "after-commit" or "duplicate_effect" not in {
    violation["code"] for violation in report["violations"]
}:
    raise SystemExit("Unsafe example failed without the expected duplicate-effect diagnostic")
PY
echo "Verification passed, including the expected unsafe-strategy failure."
echo "JSON reports: $REPORT_DIR"
