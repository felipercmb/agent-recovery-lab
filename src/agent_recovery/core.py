"""A cooperative recovery harness; application code owns provider retry policy."""

from __future__ import annotations

import inspect
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NoReturn


def _nonnegative(name: str, value: int, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'nonnegative'} integer")


def _nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


@dataclass(frozen=True)
class Reply:
    """One return value from the scripted provider. Values are not copied."""

    value: Any


@dataclass(frozen=True)
class Fail:
    """One temporary provider failure; a fresh exception is raised on every run."""

    kind: Literal["rate_limit", "timeout", "unavailable"]

    def __post_init__(self) -> None:
        if self.kind not in ("rate_limit", "timeout", "unavailable"):
            raise ValueError(f"Unknown provider failure: {self.kind!r}")


class TransientProviderError(Exception):
    """A simulated provider error, intentionally independent of any vendor SDK."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


@dataclass(frozen=True)
class Interruption:
    """Stop the workflow on this global occurrence of a named checkpoint."""

    checkpoint: str
    occurrence: int = 1

    def __post_init__(self) -> None:
        _nonempty("checkpoint", self.checkpoint)
        _nonnegative("occurrence", self.occurrence, positive=True)


@dataclass(frozen=True)
class Scenario:
    name: str
    provider_outcomes: tuple[Reply | Fail, ...] = ()
    interruptions: tuple[Interruption, ...] = ()
    max_restarts: int = 0
    max_operations: int = 1000

    def __post_init__(self) -> None:
        _nonempty("name", self.name)
        _nonnegative("max_restarts", self.max_restarts)
        _nonnegative("max_operations", self.max_operations, positive=True)
        object.__setattr__(self, "provider_outcomes", tuple(self.provider_outcomes))
        object.__setattr__(self, "interruptions", tuple(self.interruptions))
        if not all(isinstance(step, (Reply, Fail)) for step in self.provider_outcomes):
            raise TypeError("provider_outcomes must contain Reply or Fail objects")
        if not all(isinstance(item, Interruption) for item in self.interruptions):
            raise TypeError("interruptions must contain Interruption objects")
        if len(set(self.interruptions)) != len(self.interruptions):
            raise ValueError("Duplicate interruption checkpoint/occurrence")


@dataclass(frozen=True)
class ExpectedEffect:
    kind: str
    operation_id: str
    count: int = 1

    def __post_init__(self) -> None:
        _nonempty("kind", self.kind)
        _nonempty("operation_id", self.operation_id)
        _nonnegative("count", self.count)


@dataclass(frozen=True)
class Expectations:
    max_provider_attempts: int
    max_workflow_runs: int
    effects: tuple[ExpectedEffect, ...] = ()
    require_completion: bool = True
    no_duplicates: bool = True
    require_faults_exercised: bool = True
    allow_unexpected_effects: bool = False
    expected_error: type[Exception] | None = None

    def __post_init__(self) -> None:
        _nonnegative("max_provider_attempts", self.max_provider_attempts)
        _nonnegative("max_workflow_runs", self.max_workflow_runs, positive=True)
        object.__setattr__(self, "effects", tuple(self.effects))
        if not all(isinstance(effect, ExpectedEffect) for effect in self.effects):
            raise TypeError("effects must contain ExpectedEffect objects")
        keys = [(effect.kind, effect.operation_id) for effect in self.effects]
        if len(set(keys)) != len(keys):
            raise ValueError("Expected effects must have unique kind/operation_id pairs")
        for name in ("require_completion", "no_duplicates", "require_faults_exercised", "allow_unexpected_effects"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if self.expected_error is not None:
            if not isinstance(self.expected_error, type) or not issubclass(self.expected_error, Exception):
                raise TypeError("expected_error must be an Exception subclass")
            if self.require_completion:
                raise ValueError("expected_error requires require_completion=False")


@dataclass(frozen=True)
class Effect:
    id: str
    kind: str
    operation_id: str
    idempotency_key: str | None


@dataclass(frozen=True)
class TraceEvent:
    index: int
    run: int
    kind: str
    detail: str


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


@dataclass(frozen=True)
class RunReport:
    scenario: str
    completed: bool
    provider_attempts: int
    workflow_runs: int
    effect_attempts: int
    effects: tuple[Effect, ...]
    trace: tuple[TraceEvent, ...]
    violations: tuple[Violation, ...]
    error: str | None
    result: Any = field(default=None, repr=False)

    @property
    def passed(self) -> bool:
        return not self.violations

    def assert_ok(self) -> None:
        """Raise AssertionError with useful diagnostics for unittest or pytest."""
        if not self.passed:
            details = "; ".join(f"{item.code}: {item.message}" for item in self.violations)
            raise AssertionError(f"Scenario {self.scenario!r} failed: {details}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready report. Omit arbitrary provider values and workflow results."""
        return {
            "schema_version": 1,
            "scenario": self.scenario,
            "passed": self.passed,
            "completed": self.completed,
            "provider_attempts": self.provider_attempts,
            "workflow_runs": self.workflow_runs,
            "effect_attempts": self.effect_attempts,
            "effects": [asdict(effect) for effect in self.effects],
            "trace": [asdict(event) for event in self.trace],
            "violations": [asdict(item) for item in self.violations],
            "error": self.error,
        }


class _WorkflowInterrupted(BaseException):
    """Bypass application `except Exception` as a simulated supervisor stop."""


class _OperationLimitExceeded(BaseException):
    pass


class _HarnessError(RuntimeError):
    pass


class _Session:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.provider_attempts = 0
        self.workflow_runs = 0
        self.effect_attempts = 0
        self.operations = 0
        self.cursor = 0
        self.effects: list[Effect] = []
        self.idempotency: dict[tuple[str, str], Effect] = {}
        self.checkpoints: Counter[str] = Counter()
        self.interrupted: set[Interruption] = set()
        self.pending_interruption: Interruption | None = None
        self.trace: list[TraceEvent] = []
        self.issues: list[Violation] = []

    def event(self, kind: str, detail: str) -> None:
        self.trace.append(TraceEvent(len(self.trace) + 1, self.workflow_runs, kind, detail))

    def guard(self) -> None:
        self.operations += 1
        if self.operations > self.scenario.max_operations:
            self.issues.append(Violation(
                "operation_limit",
                f"Exceeded {self.scenario.max_operations} instrumented operations",
            ))
            raise _OperationLimitExceeded("Instrumented operation limit exceeded")

    def invalid(self, code: str, message: str) -> NoReturn:
        self.issues.append(Violation(code, message))
        self.event("harness_error", message)
        raise _HarnessError(message)


class ScriptedProvider:
    def __init__(self, session: _Session) -> None:
        self._session = session

    def call(self) -> Any:
        """Consume one scripted outcome, counting every call including exhaustion."""
        state = self._session
        state.guard()
        state.provider_attempts += 1
        if state.cursor == len(state.scenario.provider_outcomes):
            state.invalid("script_exhausted", "No provider outcome remains; extend the scenario script")
        outcome = state.scenario.provider_outcomes[state.cursor]
        state.cursor += 1
        if isinstance(outcome, Fail):
            state.event("provider_failure", outcome.kind)
            raise TransientProviderError(outcome.kind)
        state.event("provider_reply", "reply")
        return outcome.value


class EffectLedger:
    """In-memory fake of a remote service, retained across workflow restarts."""

    def __init__(self, session: _Session) -> None:
        self._session = session

    def commit(self, kind: str, *, operation_id: str, idempotency_key: str | None = None) -> Effect:
        """Record an actual fake commit, or return the prior idempotent result.

        operation_id identifies the intended business action independently of
        idempotency_key, so a newly generated key on retry cannot hide duplicates.
        """
        state = self._session
        state.guard()
        state.effect_attempts += 1
        try:
            _nonempty("kind", kind)
            _nonempty("operation_id", operation_id)
            if idempotency_key is not None:
                _nonempty("idempotency_key", idempotency_key)
        except ValueError as exc:
            state.invalid("invalid_effect", str(exc))
        if idempotency_key is not None:
            existing = state.idempotency.get((kind, idempotency_key))
            if existing is not None:
                if existing.operation_id != operation_id:
                    state.invalid(
                        "idempotency_conflict",
                        "Idempotency key was reused for a different logical operation",
                    )
                state.event("effect_reused", existing.id)
                return existing
        effect = Effect(f"effect-{len(state.effects) + 1}", kind, operation_id, idempotency_key)
        state.effects.append(effect)
        if idempotency_key is not None:
            state.idempotency[(kind, idempotency_key)] = effect
        state.event("effect_committed", effect.id)
        return effect


class Context:
    def __init__(self, session: _Session) -> None:
        self._session = session
        self.provider = ScriptedProvider(session)
        self.effects = EffectLedger(session)

    def checkpoint(self, name: str) -> None:
        """Visit a named boundary. Occurrences are cumulative across restarts."""
        state = self._session
        state.guard()
        try:
            _nonempty("checkpoint", name)
        except ValueError as exc:
            state.invalid("invalid_checkpoint", str(exc))
        state.checkpoints[name] += 1
        state.event("checkpoint", name)
        interruption = Interruption(name, state.checkpoints[name])
        if interruption in state.scenario.interruptions:
            if state.pending_interruption is not None:
                state.issues.append(Violation(
                    "swallowed_interruption",
                    "An earlier interruption did not reach the supervisor",
                ))
            state.interrupted.add(interruption)
            state.pending_interruption = interruption
            state.event("interrupted", name)
            raise _WorkflowInterrupted(name)


def _report(
    state: _Session,
    expectations: Expectations,
    completed: bool,
    result: Any,
    error: BaseException | None,
) -> RunReport:
    violations = list(state.issues)

    def check(ok: bool, code: str, message: str) -> None:
        if not ok:
            violations.append(Violation(code, message))

    check(
        state.pending_interruption is None,
        "swallowed_interruption",
        "The workflow suppressed or replaced an injected interruption before the supervisor received it",
    )
    check(
        completed == expectations.require_completion,
        "completion",
        f"Expected completed={expectations.require_completion}; got {completed}",
    )
    if expectations.expected_error is not None:
        check(
            isinstance(error, expectations.expected_error),
            "expected_error",
            f"Expected {expectations.expected_error.__name__}; got {type(error).__name__ if error else 'no error'}",
        )
    check(
        state.provider_attempts <= expectations.max_provider_attempts,
        "provider_attempts",
        f"Expected at most {expectations.max_provider_attempts}; got {state.provider_attempts} across all runs",
    )
    check(
        state.workflow_runs <= expectations.max_workflow_runs,
        "workflow_runs",
        f"Expected at most {expectations.max_workflow_runs}; got {state.workflow_runs}",
    )
    counts = Counter((effect.kind, effect.operation_id) for effect in state.effects)
    if expectations.no_duplicates:
        for (kind, operation_id), count in sorted(counts.items()):
            check(count <= 1, "duplicate_effect", f"{kind}/{operation_id} committed {count} times")
    expected_keys = set()
    for effect in expectations.effects:
        key = (effect.kind, effect.operation_id)
        expected_keys.add(key)
        check(
            counts[key] == effect.count,
            "effect_count",
            f"{effect.kind}/{effect.operation_id}: expected {effect.count} commits; got {counts[key]}",
        )
    if not expectations.allow_unexpected_effects:
        for kind, operation_id in sorted(counts.keys() - expected_keys):
            check(False, "unexpected_effect", f"Unexpected commit: {kind}/{operation_id}")
    if expectations.require_faults_exercised:
        remaining_failures = sum(isinstance(step, Fail) for step in state.scenario.provider_outcomes[state.cursor:])
        check(
            remaining_failures == 0,
            "unused_provider_fault",
            f"{remaining_failures} provider failures were not exercised",
        )
        for interruption in state.scenario.interruptions:
            check(
                interruption in state.interrupted,
                "unused_interruption",
                f"Checkpoint {interruption.checkpoint!r} occurrence {interruption.occurrence} was not interrupted",
            )
    return RunReport(
        scenario=state.scenario.name, completed=completed,
        provider_attempts=state.provider_attempts, workflow_runs=state.workflow_runs,
        effect_attempts=state.effect_attempts, effects=tuple(state.effects),
        trace=tuple(state.trace), violations=tuple(violations),
        error=f"{type(error).__name__}: {error}" if error is not None else None,
        result=result,
    )


def run(scenario: Scenario, workflow: Callable[[Context], Any], expectations: Expectations) -> RunReport:
    """Run a sync workflow, restarting only on explicitly injected interruptions.

    The harness does not retry provider failures. Implement that policy in the
    workflow being tested. All counters and the fake service survive restarts.
    """
    state = _Session(scenario)
    for _ in range(scenario.max_restarts + 1):
        state.workflow_runs += 1
        state.event("workflow_started", "start")
        try:
            result = workflow(Context(state))
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                state.invalid("async_workflow", "Use arun() for an async workflow")
            state.event("workflow_completed", "complete")
            return _report(state, expectations, True, result, None)
        except _WorkflowInterrupted as exc:
            state.pending_interruption = None
            error: BaseException = exc
        except (_OperationLimitExceeded, Exception) as exc:
            state.event("workflow_failed", type(exc).__name__)
            return _report(state, expectations, False, None, exc)
    return _report(state, expectations, False, None, error)


async def arun(
    scenario: Scenario,
    workflow: Callable[[Context], Awaitable[Any]],
    expectations: Expectations,
) -> RunReport:
    """Async equivalent of run(); cancellation and process-control signals propagate."""
    state = _Session(scenario)
    for _ in range(scenario.max_restarts + 1):
        state.workflow_runs += 1
        state.event("workflow_started", "start")
        try:
            pending = workflow(Context(state))
            if not inspect.isawaitable(pending):
                state.invalid("sync_workflow", "Use run() for a synchronous workflow")
            result = await pending
            state.event("workflow_completed", "complete")
            return _report(state, expectations, True, result, None)
        except _WorkflowInterrupted as exc:
            state.pending_interruption = None
            error: BaseException = exc
        except (_OperationLimitExceeded, Exception) as exc:
            state.event("workflow_failed", type(exc).__name__)
            return _report(state, expectations, False, None, exc)
    return _report(state, expectations, False, None, error)
