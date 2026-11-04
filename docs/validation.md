# Local validation

Validated on 2026-11-04 using CPython 3.14.6 on macOS.

## Results

- 46 standard-library unit tests passed from the source checkout.
- The same 46 tests passed against the installed wheel from outside the source directory, with `PYTHONPATH` unset.
- The wheel built and installed successfully into a local virtual environment.
- The installed `agent-recovery-demo` console command ran successfully.
- The safe example passed all five scenarios.
- The intentionally unsafe after-commit example exited with code 1 and reported two committed tickets.
- JSON output was identical across repeated executions of the example.

The duplicate-ticket comparison produced:

```text
PASS after-commit: provider_attempts=2 runs=2 committed_effects=1 completed=True
FAIL after-commit: provider_attempts=2 runs=2 committed_effects=2 completed=True
  duplicate_effect: ticket/support-request-42 committed 2 times
  effect_count: ticket/support-request-42: expected 1 commits; got 2
```

Regression tests also cover suppressed interruptions, incorrect sync/async runner use, missing checkpoints, exhausted scripts, changing idempotency keys, cumulative budgets, and cancellation propagation.

## Reproduce

From the project directory:

```bash
bash scripts/verify.sh
```

This command runs the tests, runs all safe scenarios, and verifies that the unsafe after-commit scenario exits with exactly code 1. It needs Python 3.11+ and no network or runtime dependencies. Set `PYTHON_BIN` if your interpreter has a different executable name.

To verify packaging separately:

```bash
python3 -m venv .venv
.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .
.venv/bin/python -m pip install --no-index dist/agent_recovery_lab-0.1.0-py3-none-any.whl
.venv/bin/agent-recovery-demo --json
```

Building may download setuptools; running the source and tests does not require it.

## Evidence boundaries

At this initial local validation, only Python 3.14.6 had been executed and the GitHub Actions matrix for Python 3.11–3.14 had not run yet. See the repository's Actions page for subsequent remote results. No package has been published to PyPI; package-name availability there has not been reserved or verified.

These tests use a fake provider and an in-memory service ledger. They do not validate a real model, provider SDK, database, hard process crash, concurrent workers, or production idempotency behavior. See [design notes](design.md) for the model's limitations.
