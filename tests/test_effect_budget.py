"""Write-attempt budgets count retries even when the service deduplicates them."""

import unittest

from agent_recovery import ExpectedEffect, Expectations, Interruption, Scenario, arun, run


TICKET = (ExpectedEffect("ticket", "request-42"),)


def commit_ticket(ctx):
    return ctx.effects.commit(
        "ticket", operation_id="request-42", idempotency_key="request-42"
    )


class EffectBudgetTests(unittest.TestCase):
    def test_exact_budget_accepts_deduplicated_attempts(self):
        def workflow(ctx):
            first = commit_ticket(ctx)
            second = commit_ticket(ctx)
            self.assertEqual(first.id, second.id)

        report = run(
            Scenario("exact-write-budget"), workflow,
            Expectations(0, 1, TICKET, max_effect_attempts=2),
        )

        report.assert_ok()
        self.assertEqual(report.effect_attempts, 2)
        self.assertEqual(len(report.effects), 1)

    def test_excess_deduplicated_attempts_fail_without_duplicate_effects(self):
        def workflow(ctx):
            for _ in range(3):
                commit_ticket(ctx)

        report = run(
            Scenario("excess-write-attempts"), workflow,
            Expectations(0, 1, TICKET, max_effect_attempts=2),
        )

        self.assertTrue(report.completed)
        self.assertEqual(report.effect_attempts, 3)
        self.assertEqual(len(report.effects), 1)
        self.assertEqual({v.code for v in report.violations}, {"effect_attempts"})
        with self.assertRaisesRegex(AssertionError, "Expected at most 2; got 3 across all runs"):
            report.assert_ok()
        self.assertEqual(report.to_dict()["violations"][0]["code"], "effect_attempts")

    def test_omitting_budget_preserves_existing_behavior(self):
        def workflow(ctx):
            for _ in range(3):
                commit_ticket(ctx)

        report = run(
            Scenario("optional-write-budget"), workflow,
            Expectations(0, 1, TICKET),
        )

        report.assert_ok()
        self.assertEqual(report.effect_attempts, 3)
        self.assertEqual(len(report.effects), 1)

    def test_existing_positional_arguments_keep_their_meanings(self):
        def workflow(ctx):
            raise ValueError("expected failure")

        expectations = Expectations(0, 1, (), False, True, True, False, ValueError)
        report = run(Scenario("positional-compatibility"), workflow, expectations)

        report.assert_ok()
        self.assertIs(expectations.expected_error, ValueError)
        self.assertIsNone(expectations.max_effect_attempts)

    def test_zero_budget_accepts_no_writes(self):
        report = run(
            Scenario("read-only"), lambda ctx: "done",
            Expectations(0, 1, max_effect_attempts=0),
        )

        report.assert_ok()
        self.assertEqual(report.effect_attempts, 0)

    def test_zero_budget_rejects_first_write(self):
        report = run(
            Scenario("unexpected-write-attempt"), commit_ticket,
            Expectations(0, 1, TICKET, max_effect_attempts=0),
        )

        self.assertTrue(report.completed)
        self.assertEqual(report.effect_attempts, 1)
        self.assertEqual({v.code for v in report.violations}, {"effect_attempts"})

    def test_budget_accumulates_across_restarts(self):
        def workflow(ctx):
            commit_ticket(ctx)
            ctx.checkpoint("after_commit")

        scenario = Scenario(
            "cumulative-write-budget",
            interruptions=(Interruption("after_commit"),), max_restarts=1,
        )
        for budget, passed in ((1, False), (2, True)):
            with self.subTest(budget=budget):
                report = run(
                    scenario, workflow,
                    Expectations(0, 2, TICKET, max_effect_attempts=budget),
                )

                self.assertEqual(report.passed, passed)
                self.assertEqual(report.workflow_runs, 2)
                self.assertEqual(report.effect_attempts, 2)
                self.assertEqual(len(report.effects), 1)
                self.assertEqual(
                    {v.code for v in report.violations},
                    set() if passed else {"effect_attempts"},
                )

    def test_invalid_write_attempts_still_consume_budget(self):
        def workflow(ctx):
            try:
                ctx.effects.commit("ticket", operation_id="")
            except RuntimeError:
                pass

        report = run(
            Scenario("invalid-write-attempt"), workflow,
            Expectations(0, 1, max_effect_attempts=0),
        )

        self.assertTrue(report.completed)
        self.assertEqual(report.effect_attempts, 1)
        self.assertEqual(report.effects, ())
        self.assertEqual(
            {v.code for v in report.violations}, {"invalid_effect", "effect_attempts"}
        )

    def test_budget_remains_enforced_for_expected_application_failure(self):
        def workflow(ctx):
            commit_ticket(ctx)
            commit_ticket(ctx)
            raise ValueError("expected failure")

        report = run(
            Scenario("failed-after-writes"), workflow,
            Expectations(
                0, 1, TICKET, require_completion=False,
                expected_error=ValueError, max_effect_attempts=1,
            ),
        )

        self.assertFalse(report.completed)
        self.assertEqual(report.effect_attempts, 2)
        self.assertEqual({v.code for v in report.violations}, {"effect_attempts"})

    def test_budget_rejects_invalid_values(self):
        for value in (-1, True, False, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_effect_attempts"):
                    Expectations(0, 1, max_effect_attempts=value)


class AsyncEffectBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_budget_counts_idempotent_attempts_across_restarts(self):
        async def workflow(ctx):
            effect = commit_ticket(ctx)
            ctx.checkpoint("after_commit")
            return effect.id

        scenario = Scenario(
            "async-write-budget",
            interruptions=(Interruption("after_commit"),), max_restarts=1,
        )
        for budget, passed in ((1, False), (2, True)):
            with self.subTest(budget=budget):
                report = await arun(
                    scenario, workflow,
                    Expectations(0, 2, TICKET, max_effect_attempts=budget),
                )

                self.assertEqual(report.passed, passed)
                self.assertTrue(report.completed)
                self.assertEqual(report.workflow_runs, 2)
                self.assertEqual(report.effect_attempts, 2)
                self.assertEqual(len(report.effects), 1)
                self.assertEqual(report.result, report.effects[0].id)
                self.assertEqual(
                    {v.code for v in report.violations},
                    set() if passed else {"effect_attempts"},
                )


if __name__ == "__main__":
    unittest.main()
