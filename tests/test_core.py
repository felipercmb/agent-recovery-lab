"""Recovery contracts tested through the public API, without network access."""

import json
import unittest

from agent_recovery import (
    ExpectedEffect,
    Expectations,
    Fail,
    Interruption,
    Reply,
    Scenario,
    TransientProviderError,
    run,
)


def retry_provider(ctx, attempts=3):
    for attempt in range(attempts):
        try:
            return ctx.provider.call()
        except TransientProviderError:
            if attempt == attempts - 1:
                raise


def codes(report):
    return {violation.code for violation in report.violations}


class RecoveryTests(unittest.TestCase):
    def test_repeated_scenario_runs_are_isolated_and_deterministic(self):
        scenario = Scenario("repeatable", (Fail("timeout"), Reply("summary")))

        def workflow(ctx):
            retry_provider(ctx)
            return ctx.effects.commit("ticket", operation_id="request-42")

        expectations = Expectations(2, 1, effects=(ExpectedEffect("ticket", "request-42"),))
        first = run(scenario, workflow, expectations)
        second = run(scenario, workflow, expectations)

        first.assert_ok()
        second.assert_ok()
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.result, first.effects[0])
        self.assertEqual(2, first.provider_attempts)
        self.assertEqual(1, len(first.effects))

    def test_all_transient_failure_types_are_retryable_by_application(self):
        for kind in ("rate_limit", "timeout", "unavailable"):
            with self.subTest(kind=kind):
                report = run(
                    Scenario(kind, (Fail(kind), Reply("recovered"))),
                    retry_provider,
                    Expectations(2, 1),
                )
                report.assert_ok()
                self.assertEqual("recovered", report.result)
                self.assertEqual(2, report.provider_attempts)

    def test_provider_budget_accumulates_across_workflow_restarts(self):
        scenario = Scenario(
            "cumulative-budget",
            (Fail("timeout"), Reply("first"), Fail("rate_limit"), Reply("second")),
            interruptions=(Interruption("after_model"),),
            max_restarts=1,
        )

        def workflow(ctx):
            result = retry_provider(ctx, attempts=2)
            ctx.checkpoint("after_model")
            return result

        report = run(scenario, workflow, Expectations(3, 2))
        self.assertTrue(report.completed)
        self.assertEqual(4, report.provider_attempts)
        self.assertEqual(2, report.workflow_runs)
        self.assertEqual({"provider_attempts"}, codes(report))
        with self.assertRaisesRegex(AssertionError, "across all runs"):
            report.assert_ok()

    def test_exhausted_outage_is_an_expected_noncompletion_without_effects(self):
        report = run(
            Scenario("outage", (Fail("unavailable"),) * 3),
            retry_provider,
            Expectations(3, 1, require_completion=False, expected_error=TransientProviderError),
        )
        report.assert_ok()
        self.assertFalse(report.completed)
        self.assertEqual(3, report.provider_attempts)
        self.assertEqual((), report.effects)
        self.assertIn("TransientProviderError", report.error)

    def test_unrelated_failure_does_not_satisfy_expected_outage(self):
        def workflow(ctx):
            raise ValueError("application bug")

        report = run(
            Scenario("wrong-error"), workflow,
            Expectations(0, 1, require_completion=False, expected_error=TransientProviderError),
        )
        self.assertEqual({"expected_error"}, codes(report))

    def test_noncompletion_expectation_rejects_an_unexpected_success(self):
        report = run(
            Scenario("unexpected-success"), lambda ctx: "done",
            Expectations(0, 1, require_completion=False),
        )
        self.assertIn("completion", codes(report))

    def test_provider_failure_does_not_trigger_supervisor_restart(self):
        report = run(
            Scenario("no-hidden-retry", (Fail("rate_limit"),), max_restarts=5),
            lambda ctx: ctx.provider.call(),
            Expectations(1, 1, require_completion=False, expected_error=TransientProviderError),
        )
        report.assert_ok()
        self.assertEqual(1, report.workflow_runs)
        self.assertEqual(1, report.provider_attempts)

    def test_after_commit_interruption_exposes_duplicate_business_effect(self):
        def workflow(ctx):
            effect = ctx.effects.commit("ticket", operation_id="request-42")
            ctx.checkpoint("after_commit")
            return effect.id

        report = run(
            Scenario("lost-ack", interruptions=(Interruption("after_commit"),), max_restarts=1),
            workflow,
            Expectations(0, 2, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        self.assertTrue(report.completed)
        self.assertEqual(2, len(report.effects))
        self.assertEqual(2, report.effect_attempts)
        self.assertEqual({"duplicate_effect", "effect_count"}, codes(report))

    def test_stable_idempotency_reuses_committed_result_after_interruption(self):
        attempted_ids = []

        def workflow(ctx):
            effect = ctx.effects.commit(
                "ticket", operation_id="request-42", idempotency_key="request-42"
            )
            attempted_ids.append(effect.id)
            try:
                ctx.checkpoint("after_commit")
            except Exception:
                self.fail("A simulated interruption must bypass application Exception handlers")
            return effect.id

        report = run(
            Scenario("safe-retry", interruptions=(Interruption("after_commit"),), max_restarts=1),
            workflow,
            Expectations(0, 2, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        report.assert_ok()
        self.assertEqual(2, report.effect_attempts)
        self.assertEqual(1, len(report.effects))
        self.assertEqual(attempted_ids[0], attempted_ids[1])
        self.assertEqual(report.effects[0].id, report.result)

    def test_rotating_idempotency_keys_cannot_hide_duplicate_operation(self):
        run_number = 0

        def workflow(ctx):
            nonlocal run_number
            run_number += 1
            ctx.effects.commit(
                "ticket", operation_id="request-42", idempotency_key=f"retry-{run_number}"
            )
            ctx.checkpoint("after_commit")

        report = run(
            Scenario("unstable-key", interruptions=(Interruption("after_commit"),), max_restarts=1),
            workflow,
            Expectations(0, 2, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        self.assertIn("duplicate_effect", codes(report))
        self.assertEqual(2, len(report.effects))

    def test_interruption_before_commit_leaves_no_phantom_effect(self):
        def workflow(ctx):
            ctx.checkpoint("before_commit")
            ctx.effects.commit("ticket", operation_id="request-42")

        report = run(
            Scenario("before-commit", interruptions=(Interruption("before_commit"),), max_restarts=1),
            workflow,
            Expectations(0, 2, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        report.assert_ok()
        self.assertEqual(1, report.effect_attempts)

    def test_occurrence_numbers_and_workflow_budgets_are_global(self):
        report = run(
            Scenario(
                "two-restarts",
                interruptions=(Interruption("boundary", 1), Interruption("boundary", 2)),
                max_restarts=2,
            ),
            lambda ctx: ctx.checkpoint("boundary"),
            Expectations(0, 2),
        )
        self.assertTrue(report.completed)
        self.assertEqual(3, report.workflow_runs)
        self.assertEqual({"workflow_runs"}, codes(report))

    def test_restart_limit_leaves_workflow_incomplete(self):
        report = run(
            Scenario("no-restart", interruptions=(Interruption("boundary"),)),
            lambda ctx: ctx.checkpoint("boundary"),
            Expectations(0, 1),
        )
        self.assertFalse(report.completed)
        self.assertEqual(1, report.workflow_runs)
        self.assertEqual({"completion"}, codes(report))

    def test_unused_faults_and_missing_checkpoint_fail_instead_of_false_pass(self):
        report = run(
            Scenario(
                "unreached",
                (Reply("first"), Fail("timeout")),
                interruptions=(Interruption("after_commit"),),
            ),
            lambda ctx: ctx.provider.call(),
            Expectations(1, 1),
        )
        self.assertTrue(report.completed)
        self.assertEqual({"unused_provider_fault", "unused_interruption"}, codes(report))

    def test_fault_exercise_check_can_be_explicitly_disabled(self):
        report = run(
            Scenario("unused-is-intentional", (Fail("timeout"),), (Interruption("unused"),)),
            lambda ctx: None,
            Expectations(0, 1, require_faults_exercised=False),
        )
        report.assert_ok()

    def test_missing_expected_effect_fails_even_when_workflow_returns(self):
        report = run(
            Scenario("no-op"), lambda ctx: "claimed success",
            Expectations(0, 1, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        self.assertEqual({"effect_count"}, codes(report))

    def test_unexpected_effect_is_rejected_even_when_expected_one_exists(self):
        def workflow(ctx):
            ctx.effects.commit("ticket", operation_id="request-42")
            ctx.effects.commit("email", operation_id="unrequested-email")

        report = run(
            Scenario("extra-side-effect"), workflow,
            Expectations(0, 1, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        self.assertEqual({"unexpected_effect"}, codes(report))

    def test_intentionally_allowed_extra_effects_still_cannot_be_duplicated(self):
        def workflow(ctx):
            ctx.effects.commit("email", operation_id="request-42")
            ctx.effects.commit("email", operation_id="request-42")

        report = run(
            Scenario("extra-duplicate"), workflow,
            Expectations(0, 1, allow_unexpected_effects=True),
        )
        self.assertEqual({"duplicate_effect"}, codes(report))

    def test_effect_kind_scopes_logical_identity_and_idempotency(self):
        def workflow(ctx):
            for kind in ("ticket", "email"):
                ctx.effects.commit(kind, operation_id="request-42", idempotency_key="shared-key")

        report = run(
            Scenario("distinct-kinds"), workflow,
            Expectations(0, 1, effects=(ExpectedEffect("ticket", "request-42"), ExpectedEffect("email", "request-42"))),
        )
        report.assert_ok()
        self.assertEqual(2, len(report.effects))

    def test_caught_idempotency_conflict_remains_a_failure(self):
        def workflow(ctx):
            ctx.effects.commit("ticket", operation_id="request-42", idempotency_key="same-key")
            try:
                ctx.effects.commit("ticket", operation_id="request-99", idempotency_key="same-key")
            except RuntimeError:
                pass

        report = run(
            Scenario("bad-key-reuse"), workflow,
            Expectations(0, 1, effects=(ExpectedEffect("ticket", "request-42"),)),
        )
        self.assertTrue(report.completed)
        self.assertEqual(1, len(report.effects))
        self.assertEqual({"idempotency_conflict"}, codes(report))

    def test_caught_script_exhaustion_cannot_report_success(self):
        def workflow(ctx):
            try:
                ctx.provider.call()
            except RuntimeError:
                return "pretend-success"

        report = run(Scenario("short-script"), workflow, Expectations(1, 1))
        self.assertTrue(report.completed)
        self.assertEqual(1, report.provider_attempts)
        self.assertEqual({"script_exhausted"}, codes(report))

    def test_cooperative_guard_stops_unbounded_exception_swallowing_retries(self):
        def workflow(ctx):
            while True:
                try:
                    ctx.provider.call()
                except Exception:
                    continue

        report = run(
            Scenario("unbounded-retry", (Fail("timeout"),) * 3, max_operations=4),
            workflow,
            Expectations(4, 1),
        )
        self.assertFalse(report.completed)
        self.assertEqual(4, report.provider_attempts)
        self.assertIn("operation_limit", codes(report))

    def test_invalid_instrumentation_is_sticky_when_application_catches_error(self):
        def workflow(ctx):
            for operation in (
                lambda: ctx.effects.commit("ticket", operation_id=""),
                lambda: ctx.checkpoint(" "),
            ):
                try:
                    operation()
                except RuntimeError:
                    pass

        report = run(Scenario("invalid-instrumentation"), workflow, Expectations(0, 1))
        self.assertTrue(report.completed)
        self.assertEqual({"invalid_effect", "invalid_checkpoint"}, codes(report))
        self.assertEqual((), report.effects)

    def test_json_report_does_not_serialize_arbitrary_provider_or_result_values(self):
        private_value = object()
        report = run(
            Scenario("opaque-result", (Reply(private_value),)),
            lambda ctx: ctx.provider.call(),
            Expectations(1, 1),
        )
        report.assert_ok()
        self.assertIs(private_value, report.result)
        payload = report.to_dict()
        self.assertNotIn("result", payload)
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_sync_runner_rejects_async_workflow_without_unawaited_warning(self):
        async def workflow(ctx):
            return "done"

        report = run(Scenario("wrong-runner"), workflow, Expectations(0, 1))
        self.assertFalse(report.completed)
        self.assertIn("async_workflow", codes(report))
        self.assertIn("arun", report.error)

    def test_process_control_exceptions_are_not_converted_to_reports(self):
        for error_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type.__name__):
                def workflow(ctx):
                    raise error_type()

                with self.assertRaises(error_type):
                    run(Scenario("process-control"), workflow, Expectations(0, 1))


class ConfigurationTests(unittest.TestCase):
    def test_invalid_configuration_fails_before_execution(self):
        cases = (
            lambda: Scenario(" "),
            lambda: Scenario("bad", max_restarts=-1),
            lambda: Scenario("bad", max_restarts=True),
            lambda: Scenario("bad", max_operations=0),
            lambda: Scenario("bad", provider_outcomes=("raw value",)),
            lambda: Scenario("bad", interruptions=("checkpoint",)),
            lambda: Scenario("bad", interruptions=(Interruption("x"), Interruption("x"))),
            lambda: Interruption("x", occurrence=0),
            lambda: Interruption(""),
            lambda: Fail("unknown"),
            lambda: ExpectedEffect("ticket", "", 1),
            lambda: ExpectedEffect("ticket", "request", -1),
            lambda: Expectations(-1, 1),
            lambda: Expectations(True, 1),
            lambda: Expectations(0, 0),
            lambda: Expectations(0, 1, require_completion="yes"),
            lambda: Expectations(0, 1, expected_error=ValueError),
            lambda: Expectations(0, 1, require_completion=False, expected_error=KeyboardInterrupt),
            lambda: Expectations(0, 1, effects=("ticket",)),
            lambda: Expectations(0, 1, effects=(ExpectedEffect("ticket", "x"), ExpectedEffect("ticket", "x"))),
        )
        for index, construct in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises((TypeError, ValueError)):
                    construct()


if __name__ == "__main__":
    unittest.main()
