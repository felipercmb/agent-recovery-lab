# Local validation

Validated on 2026-11-05 using CPython 3.12.13 and 3.14.6 on macOS.

## Results

- 63 standard-library unit tests passed from the source checkout on Python 3.12 and 3.14.
- The same 63 tests passed against the installed wheel on Python 3.14 from outside the source directory, with `PYTHONPATH` unset.
- The wheel built and installed successfully into a local virtual environment.
- The installed `agent-recovery-demo` console command ran successfully, and the wheel included its `py.typed` marker.
- The safe example passed all five scenarios.
- The intentionally unsafe after-commit example exited with code 1 and reported two committed tickets.
- JSON output was identical across repeated executions of the example.
- Both Python examples in the README ran successfully.

The duplicate-ticket comparison produced:

```text
PASS after-commit: provider_attempts=2 runs=2 effect_attempts=2 committed_effects=1 completed=True
FAIL after-commit: provider_attempts=2 runs=2 effect_attempts=2 committed_effects=2 completed=True
  duplicate_effect: ticket/support-request-42 committed 2 times
  effect_count: ticket/support-request-42: expected 1 commits; got 2
```

Regression tests also cover suppressed interruptions, incorrect sync/async runner use, missing checkpoints, exhausted scripts, changing idempotency keys, cumulative budgets, and cancellation propagation.

The added controls reject unexecuted generator workflows, contradictory duplicate expectations, and excessive idempotent write attempts. The write-budget regression produces three attempts but only one committed effect: it fails only the `effect_attempts` invariant. Its paired control passes with a sufficient budget, including across async restarts.

## Reproduce

From the project directory:

```bash
bash scripts/verify.sh
```

This command runs the tests, runs all safe scenarios, and verifies that the unsafe after-commit scenario exits with exactly code 1 and reports `duplicate_effect`. It saves safe and unsafe JSON reports to a temporary directory printed at completion. Set `REPORT_DIR` to choose a destination. It needs Python 3.11+ and no network or runtime dependencies. Set `PYTHON_BIN` if your interpreter has a different executable name.

To verify packaging separately:

```bash
python3 -m venv .venv
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
.venv/bin/python -m pip install --no-index dist/agent_recovery_lab-0.1.0-py3-none-any.whl
PYTHON_BIN="$PWD/.venv/bin/python" bash scripts/verify.sh --installed
```

Building may download setuptools; running the source and tests does not require it.

Installed mode unsets `PYTHONPATH`, checks the installed typing marker, and invokes the installed console entry point. CI uses this mode for Python 3.11–3.14, applies a five-minute job timeout, and uploads `safe.json` and `unsafe.json` as `recovery-reports-python-<version>` artifacts, retained for 14 days.

## Evidence boundaries

Local checks executed Python 3.12.13 and 3.14.6. See the repository's [Actions page](https://github.com/felipercmb/agent-recovery-lab/actions/workflows/ci.yml) for commit-specific remote results on Python 3.11–3.14 and downloadable reports. No package has been published to PyPI; package-name availability there has not been reserved or verified.

These tests use a fake provider and an in-memory service ledger. They do not validate a real model, provider SDK, database, hard process crash, concurrent workers, or production idempotency behavior. See [design notes](design.md) for the model's limitations.
