#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m unittest discover -s tests -v
"$PYTHON_BIN" -m agent_recovery.demo --strategy safe
if "$PYTHON_BIN" -m agent_recovery.demo --strategy unsafe --scenario after-commit; then
  echo "Expected unsafe strategy to fail, but it succeeded." >&2
  exit 1
else
  status=$?
  if [ "$status" -ne 1 ]; then
    echo "Expected exit code 1 from unsafe strategy; got $status." >&2
    exit 1
  fi
fi
echo "Verification passed, including the expected unsafe-strategy failure."
