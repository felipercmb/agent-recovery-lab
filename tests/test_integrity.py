"""Regression tests for plausible false passes at runner boundaries."""

import unittest

from agent_recovery import Expectations, Interruption, Scenario, arun, run


def suppressed_stop(ctx):
    try:
        ctx.checkpoint("stop")
    except BaseException:
        return "incorrectly recovered"


class IntegrityTests(unittest.TestCase):
    def test_later_stop_cannot_hide_an_earlier_suppressed_stop(self):
        def workflow(ctx):
            suppressed_stop(ctx)
            ctx.checkpoint("second_stop")

        report = run(
            Scenario(
                "partially swallowed stops",
                interruptions=(Interruption("stop"), Interruption("second_stop")),
                max_restarts=1,
            ),
            workflow, Expectations(0, 2),
        )
        self.assertFalse(report.passed)
        self.assertIn("swallowed_interruption", [v.code for v in report.violations])

    def test_suppressed_stop_cannot_pass_as_success(self):
        report = run(
            Scenario("swallowed stop", interruptions=(Interruption("stop"),), max_restarts=1),
            suppressed_stop, Expectations(0, 2),
        )
        self.assertFalse(report.passed)
        self.assertIn("swallowed_interruption", [v.code for v in report.violations])

    def test_replaced_stop_cannot_pass_as_expected_failure(self):
        def workflow(ctx):
            try:
                ctx.checkpoint("stop")
            finally:
                raise ValueError("cleanup replaced the stop")

        report = run(
            Scenario("replaced stop", interruptions=(Interruption("stop"),)),
            workflow, Expectations(0, 1, require_completion=False, expected_error=ValueError),
        )
        self.assertFalse(report.passed)
        self.assertIn("swallowed_interruption", [v.code for v in report.violations])


class AsyncIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_workflow_is_not_an_expected_type_error(self):
        report = await arun(
            Scenario("wrong runner"), lambda ctx: 42,
            Expectations(0, 1, require_completion=False, expected_error=TypeError),
        )
        self.assertFalse(report.passed)
        self.assertIn("sync_workflow", [v.code for v in report.violations])

    async def test_suppressed_stop_cannot_pass_as_async_success(self):
        async def workflow(ctx):
            return suppressed_stop(ctx)

        report = await arun(
            Scenario("swallowed async stop", interruptions=(Interruption("stop"),), max_restarts=1),
            workflow, Expectations(0, 2),
        )
        self.assertFalse(report.passed)
        self.assertIn("swallowed_interruption", [v.code for v in report.violations])
