"""Async recovery behavior and preservation of task-control signals."""

import asyncio
import unittest

from agent_recovery import (
    Expectations,
    ExpectedEffect,
    Fail,
    Interruption,
    Reply,
    Scenario,
    TransientProviderError,
    arun,
)


class AsyncRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_preserves_workflow_result(self):
        async def workflow(ctx):
            await asyncio.sleep(0)
            return ctx.provider.call()

        report = await arun(
            Scenario("async-success", (Reply("summary"),)),
            workflow,
            Expectations(max_provider_attempts=1, max_workflow_runs=1),
        )

        report.assert_ok()
        self.assertEqual(report.result, "summary")
        self.assertEqual(report.provider_attempts, 1)
        self.assertEqual(report.workflow_runs, 1)

    async def test_restart_retains_commit_and_recreates_local_workflow_state(self):
        starts = []
        swallowed = []

        async def workflow(ctx):
            local_steps = []
            starts.append(tuple(local_steps))
            local_steps.append(ctx.provider.call())
            effect = ctx.effects.commit(
                "ticket", operation_id="request-42", idempotency_key="request-42"
            )
            await asyncio.sleep(0)
            try:
                ctx.checkpoint("after_commit")
            except Exception:
                swallowed.append(True)
            return effect.id

        report = await arun(
            Scenario(
                "async-restart",
                (Reply("summary"), Reply("summary")),
                (Interruption("after_commit"),),
                max_restarts=1,
            ),
            workflow,
            Expectations(
                max_provider_attempts=2,
                max_workflow_runs=2,
                effects=(ExpectedEffect("ticket", "request-42"),),
            ),
        )

        report.assert_ok()
        self.assertEqual(starts, [(), ()])
        self.assertEqual(swallowed, [])
        self.assertEqual(report.effect_attempts, 2)
        self.assertEqual(len(report.effects), 1)
        self.assertEqual(report.result, report.effects[0].id)
        self.assertEqual(report.provider_attempts, 2)

    async def test_restart_detects_duplicate_commits_without_idempotency(self):
        async def workflow(ctx):
            effect = ctx.effects.commit("ticket", operation_id="request-42")
            ctx.checkpoint("after_commit")
            return effect.id

        report = await arun(
            Scenario(
                "async-duplicate",
                interruptions=(Interruption("after_commit"),),
                max_restarts=1,
            ),
            workflow,
            Expectations(
                max_provider_attempts=0,
                max_workflow_runs=2,
                effects=(ExpectedEffect("ticket", "request-42"),),
            ),
        )

        self.assertTrue(report.completed)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.effects), 2)
        self.assertIn("duplicate_effect", {item.code for item in report.violations})

    async def test_application_owned_retry_recovers_from_provider_failures(self):
        failures = []

        async def workflow(ctx):
            for attempt in range(3):
                try:
                    return ctx.provider.call()
                except TransientProviderError as exc:
                    failures.append(exc.kind)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0)

        report = await arun(
            Scenario(
                "async-provider-retry",
                (Fail("rate_limit"), Fail("timeout"), Reply("recovered")),
            ),
            workflow,
            Expectations(max_provider_attempts=3, max_workflow_runs=1),
        )

        report.assert_ok()
        self.assertEqual(failures, ["rate_limit", "timeout"])
        self.assertEqual(report.provider_attempts, 3)
        self.assertEqual(report.result, "recovered")

    async def test_provider_failure_is_not_automatically_retried(self):
        async def workflow(ctx):
            return ctx.provider.call()

        report = await arun(
            Scenario(
                "async-outage", (Fail("unavailable"), Reply("unused")), max_restarts=3
            ),
            workflow,
            Expectations(
                max_provider_attempts=1,
                max_workflow_runs=1,
                require_completion=False,
                expected_error=TransientProviderError,
            ),
        )

        report.assert_ok()
        self.assertFalse(report.completed)
        self.assertEqual(report.provider_attempts, 1)
        self.assertEqual(report.workflow_runs, 1)
        self.assertEqual(report.error, "TransientProviderError: unavailable")

    async def test_unexpected_application_error_is_reported_without_restart(self):
        invocations = []

        async def workflow(ctx):
            invocations.append(True)
            raise ValueError("invalid ticket")

        report = await arun(
            Scenario("async-error", max_restarts=4),
            workflow,
            Expectations(max_provider_attempts=0, max_workflow_runs=1),
        )

        self.assertEqual(invocations, [True])
        self.assertFalse(report.passed)
        self.assertFalse(report.completed)
        self.assertEqual(report.error, "ValueError: invalid ticket")
        with self.assertRaisesRegex(AssertionError, "completion"):
            report.assert_ok()

    async def test_task_cancellation_propagates(self):
        started = asyncio.Event()
        never_finished = asyncio.Event()

        async def workflow(ctx):
            started.set()
            await never_finished.wait()

        task = asyncio.create_task(
            arun(
                Scenario("cancelled", max_restarts=2),
                workflow,
                Expectations(max_provider_attempts=0, max_workflow_runs=1),
            )
        )
        await started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())

    async def test_process_control_signals_propagate(self):
        for signal in (KeyboardInterrupt, SystemExit):
            with self.subTest(signal=signal.__name__):
                async def workflow(ctx):
                    raise signal("stop")

                with self.assertRaises(signal):
                    await arun(
                        Scenario("process-control"),
                        workflow,
                        Expectations(max_provider_attempts=0, max_workflow_runs=1),
                    )

    async def test_interruption_without_restart_budget_does_not_complete(self):
        async def workflow(ctx):
            ctx.checkpoint("before_commit")
            ctx.effects.commit("ticket", operation_id="request-42")

        report = await arun(
            Scenario(
                "no-restart", interruptions=(Interruption("before_commit"),)
            ),
            workflow,
            Expectations(max_provider_attempts=0, max_workflow_runs=1),
        )

        self.assertFalse(report.passed)
        self.assertFalse(report.completed)
        self.assertEqual(report.workflow_runs, 1)
        self.assertEqual(report.effects, ())
        self.assertEqual({item.code for item in report.violations}, {"completion"})


if __name__ == "__main__":
    unittest.main()
