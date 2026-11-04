# Design notes

## The failure being tested

A request to create a support ticket can succeed at the remote service while its caller fails to receive confirmation. Restarting the caller can repeat the request. A workflow that appears to recover successfully can therefore leave two tickets behind.

The harness treats those as separate observations:

1. The workflow attempted a provider call or a write.
2. The fake service committed a business effect.
3. The workflow reached a checkpoint where it may be interrupted.
4. A supervisor optionally invoked the workflow again.
5. The final report compared cumulative observations with explicit expectations.

The observation in step 2 survives the restart in step 4. That is the essential distinction from counting only successful tool returns.

## Components

`Scenario` declares a sequence of provider outcomes, named interruption points, a maximum number of restarts, and a cooperative operation limit. A scenario is test data; it does not contain an application's provider retry policy.

The scripted provider returns `Reply` values or raises a `TransientProviderError` for a `Fail`. Each call counts as an attempt, including a call that fails. Exhausting the script is a harness error: otherwise an accidental extra retry could silently pass.

The effect ledger acts as a fake external service. `commit(kind, operation_id=..., idempotency_key=...)` records an effect when it is first committed. Reusing a stable idempotency key for the same logical operation returns the recorded effect. Omitting a key allows another effect to be committed. The ledger's idempotency behavior is a model for a service that honors keys, not behavior supplied to real services by this library.

`Context.checkpoint(name)` marks a boundary at which a scenario can interrupt a workflow. `Interruption(name, occurrence=n)` targets the nth visit to that checkpoint across the scenario. An interruption ends the current invocation. The supervisor starts another invocation if the configured restart budget permits it.

`Expectations` defines what the test accepts. The expected effect counts are keyed by both effect kind and logical operation ID. Duplicate detection uses those business identifiers independently of the implementation's idempotency keys.

The report includes cumulative observations, violations, and a verdict. Its assertion method makes the report usable inside an existing test suite without a test-runner plugin.

## What survives a restart

The provider script position, checkpoint visit counts, trace, committed effects, and cumulative attempt counts belong to the scenario and survive its workflow restarts. This models an external service and test observer that remain available when a worker fails.

The harness calls the workflow again. Local variables created inside that function are created again. It does not reset module globals, closure state, files, or objects captured by the workflow. Tests that need fresh application objects should create them inside each invocation or deliberately supply their own recovery state.

Each call to `run` or `arun` creates a new scenario execution. No effect should carry over from a previous test execution.

## Why operation IDs and idempotency keys are separate

The operation ID answers: “Which business action was requested?” The idempotency key answers: “How does this implementation ask the service to suppress repeat writes?”

For one incoming request, `operation_id="request-42"` stays stable across every attempt. If the implementation generates a new idempotency key on each attempt, the ledger will commit repeated effects for that same operation. The duplicate assertion catches that mistake.

If a test changes the logical operation ID on every attempt, it has changed what it observes. Strict expected-effect checks help identify additional operations, but instrumentation still needs to express the application's real business identity.

## Failure and budget semantics

The application owns retries for `TransientProviderError`. The runner owns only the simulated supervisor restarts after checkpoint interruptions. `max_restarts=1` allows at most two invocations: the original invocation and one restart.

Expectation budgets count the entire scenario, including all restarts. A workflow that individually stays within a per-invocation retry budget may still violate the total provider-attempt budget.

An injected interruption inherits from `BaseException` so an application's ordinary `except Exception` retry handler does not turn a simulated worker stop into a recoverable provider error. The runner handles that harness signal. Application code should not catch `BaseException` broadly.

These are cooperative Python interruptions: `finally` blocks and context-manager cleanup still run, unlike a hard process kill. Suppressing or replacing an interruption before it reaches the supervisor is an integrity failure, even if the workflow returns successfully. Use a process-level integration test for actual crash semantics.

`require_completion=False` requires the workflow to fail, for example after exhausting its retry policy during a provider outage. Set `expected_error=TransientProviderError` to also assert the expected application error type. This option is valid only when `require_completion=False`. The report still checks attempt limits, expected and unexpected effects, duplicates, exercised faults, and harness integrity. A passing report in this case means the failure was expected and contained, not that the workflow completed.

`report.error` contains the exception type and message, or `None` if there was no error. Exception messages can contain application data; review them before sharing reports. Each violation includes a code and a message so callers can inspect a failed invariant without parsing the human-readable verdict.

Faults declared but never reached are rejected by default. This protects against tests that pass after a checkpoint is renamed or a code path stops using the provider. This check can be disabled explicitly for tests that intentionally exercise only a subset of a scenario.

The operation limit is a separate cooperative safety fuse. It counts calls into the harness, not CPU instructions or elapsed time. An infinite loop that never calls the provider, ledger, or checkpoint is outside its reach.

## Determinism and async workflows

Outcomes are scripted; there is no random fault probability, real backoff sleep, external model, or network call. This keeps failures reproducible and allows the core suite to run using only the Python standard library.

`arun` lets an asynchronous workflow use the same observations and assertions. It does not model scheduling races, multiple concurrent workers, simultaneous commits, task cancellation, or real asynchronous provider transports. The synchronous fake operations are deliberate boundaries for this MVP.

## Limits of the evidence

A passing test shows that the instrumented workflow satisfied the declared expectations for the selected script and interruption points. It does not prove exactly-once processing, correct database transactions, durable checkpoint recovery, semantic accuracy of a model, or the idempotency behavior of a production API.

Use a real integration test to verify the production service's write behavior and idempotency contract. Use transport-level fault injection to test SDK retry and streaming behavior. This harness contributes an explicit check of the resulting business effects and cumulative budgets in a controlled model.
