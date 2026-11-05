# Agent Recovery Lab

[![Tests](https://github.com/felipercmb/agent-recovery-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/felipercmb/agent-recovery-lab/actions/workflows/ci.yml)

Deterministic tests for agent workflows that retry, restart, and write to external systems.

A support agent creates a ticket. Its worker stops before receiving confirmation. When the worker restarts, does it create a second ticket? Agent Recovery Lab makes that failure reproducible and checks the committed business effects alongside the total number of attempts.

This is a small, framework-independent Python test harness. It has no runtime dependencies, makes no model requests, and runs without API keys.

## Try the failure, then the fix

Requires Python 3.11 or newer. Clone the repository, then run the examples without installing a package:

```bash
git clone https://github.com/felipercmb/agent-recovery-lab.git
cd agent-recovery-lab

# Run the example's recovery scenarios with stable idempotency keys.
PYTHONPATH=src python -m agent_recovery.demo

# Deliberately reproduce a duplicate ticket. This command should exit with code 1.
PYTHONPATH=src python -m agent_recovery.demo --strategy unsafe --scenario after-commit

# Export the safe example's machine-readable results.
PYTHONPATH=src python -m agent_recovery.demo --json > report.json

# Run the test suite.
PYTHONPATH=src python -m unittest discover -s tests -v
```

The unsafe strategy commits a ticket twice after an interruption. The safe strategy sends the same idempotency key on both attempts, so the simulated service returns the previously committed ticket. Both strategies can call the service more than once; the assertion concerns how many business effects were committed.

For use in another local project:

```bash
python -m pip install -e .
```

This installs the local checkout. The project does not require a published package or a hosted service.

## Write a recovery test

```python
from agent_recovery import (
    ExpectedEffect,
    Expectations,
    Interruption,
    Reply,
    Scenario,
    run,
)


def open_ticket(ctx):
    decision = ctx.provider.call()
    if decision != "open_ticket":
        raise ValueError("Unexpected decision")

    ticket = ctx.effects.commit(
        "ticket",
        operation_id="request-42",
        idempotency_key="request-42",
    )
    ctx.checkpoint("after_commit")
    return ticket.id


report = run(
    Scenario(
        name="restart after a committed ticket",
        provider_outcomes=(Reply("open_ticket"), Reply("open_ticket")),
        interruptions=(Interruption("after_commit"),),
        max_restarts=1,
    ),
    open_ticket,
    Expectations(
        max_provider_attempts=2,
        max_workflow_runs=2,
        effects=(ExpectedEffect("ticket", "request-42"),),
        max_effect_attempts=2,
    ),
)

report.assert_ok()
assert report.provider_attempts == 2
assert report.workflow_runs == 2
assert len(report.effects) == 1
```

Remove `idempotency_key` and the same test fails: two effects were committed for the same logical operation. A random key generated on every restart has the same problem. The `operation_id` describes the business action under test; it must remain stable even when the implementation uses a bad idempotency key.

Use `await arun(scenario, workflow, expectations)` for an asynchronous workflow. The deterministic provider, ledger, and checkpoints remain synchronous in both runners.

Workflows must execute their work before returning: generator and async-generator functions are rejected with `generator_workflow`, because calling them does not execute their bodies. Consume a generator inside a regular or async workflow if needed.

## Bound write retries, even with idempotency

A stable key prevents duplicate commits in the fake service, but a retry loop can still send too many write requests. Set `max_effect_attempts` to check every ledger call across the entire scenario, including restarts and idempotent reuse:

```python
from agent_recovery import ExpectedEffect, Expectations, Scenario, run


def excessive_retries(ctx):
    for _ in range(3):
        ctx.effects.commit("ticket", operation_id="request-42", idempotency_key="request-42")


report = run(
    Scenario("too many write attempts"),
    excessive_retries,
    Expectations(0, 1, effects=(ExpectedEffect("ticket", "request-42"),), max_effect_attempts=2),
)

assert len(report.effects) == 1
assert report.effect_attempts == 3
assert not report.passed
assert [violation.code for violation in report.violations] == ["effect_attempts"]
```

Omitting this optional budget preserves the existing behavior. Use `0` for a workflow that must not attempt writes. Like the provider and workflow budgets, it is a post-run assertion; it does not stop execution. `Scenario.max_operations` remains the separate cooperative execution limit.

## What the harness checks

| Concern | What is measured |
| --- | --- |
| Completion | Whether the workflow returned successfully after any configured restarts |
| Attempt limits | Total provider calls, workflow invocations, and optionally write attempts across the entire scenario |
| Expected effects | The committed count for each `(kind, operation_id)` pair |
| Duplicates | More than one committed effect for the same logical operation |
| Unexpected effects | Effects absent from the declared expectations, rejected by default |
| Exercised faults | Whether the configured failures and interruptions were reached |
| Harness integrity | Script exhaustion, invalid instrumentation, and the cooperative operation limit |

`report.passed` is a Boolean verdict. `report.assert_ok()` raises an assertion failure suitable for `unittest` or a normal pytest test. `report.to_dict()` provides a JSON-compatible report for CI artifacts. Reports describe only the scenario that ran; they are not a production reliability score.

The CLI text shows `effect_attempts` separately from `committed_effects`. GitHub Actions checks the safe and deliberately unsafe examples on Python 3.11–3.14 and attaches their JSON reports to each run. See [validation instructions](docs/validation.md) to reproduce the checks locally.

## Deterministic scenarios

The bundled ticket example covers:

| CLI scenario | Injection | Expected safe behavior |
| --- | --- | --- |
| `baseline` | None | Complete and commit one ticket |
| `rate-limit` | A transient provider failure | Retry within the declared budget |
| `outage` | Repeated provider failures | Stop within the budget without committing a ticket |
| `before-commit` | An interruption before the write | Restart and commit one ticket |
| `after-commit` | An interruption after the write | Restart and reuse the existing ticket |

Provider scripts use `Reply(value)` and `Fail(kind)`, where `kind` is `rate_limit`, `timeout`, or `unavailable`. Failures raise `TransientProviderError`. Your workflow owns provider retry decisions; the harness does not add those retries for you. Its supervisor restarts an invocation only after a configured checkpoint interruption, up to `Scenario.max_restarts`.

An outage can be an expected outcome: use `require_completion=False`, `expected_error=TransientProviderError`, and explicit effect and attempt expectations. This requires the workflow to fail with the expected error while staying within its budget. The report can therefore pass even though the workflow deliberately did not complete. Harness integrity checks still apply.

## Scope and tradeoffs

- The ledger is an in-memory fake service retained across workflow invocations in a single scenario. It is not a database or a production idempotency implementation.
- Checkpoints model selected interruption boundaries. Python cleanup still runs; they do not kill a process, drop a real network packet, or reproduce every crash timing.
- Logical operation IDs and checkpoint placement are part of the test. Incorrect instrumentation can hide a real defect.
- The cooperative `max_operations` limit bounds instrumented calls. It cannot stop arbitrary Python code that hangs without calling the harness; use a separate process timeout for untrusted or potentially hanging workflows.
- There is no SDK interception, real provider, backoff timing verification, streaming simulation, concurrency model, or durable persistence in this MVP.
- A passing scenario does not prove exactly-once execution or correctness under all production failures.

For transport-level fault injection, [JulienRabault/LLMock](https://github.com/JulienRabault/LLMock) already includes failure scenarios and resilience assertions. Pydantic AI provides [model substitutes and testing overrides](https://pydantic.dev/docs/ai/guides/testing/). This project's narrower focus is the relationship between attempts, restarts, and committed business effects. See [related work](docs/related-work.md) and the [design notes](docs/design.md).

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md). Useful contributions include a minimal failing recovery case, a clearer invariant, or a small integration backed by an executable example. Claims of production guarantees need evidence beyond this deterministic harness.

Released under the [MIT License](LICENSE).
