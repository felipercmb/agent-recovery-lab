"""Run offline ticket-agent examples: python -m agent_recovery.demo --json."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from . import (
    Context,
    ExpectedEffect,
    Expectations,
    Fail,
    Interruption,
    Reply,
    Scenario,
    TransientProviderError,
    run,
)

OPERATION_ID = "support-request-42"
SUMMARY = "Investigate the missing payment confirmation"


def ticket_agent(*, safe: bool) -> Callable[[Context], dict[str, str]]:
    """Build an agent with a three-attempt provider policy and optional deduplication.

    In a real integration, persist the operation ID with the incoming request and
    pass its stable idempotency key to the external service. Do not mint a new
    key every time the worker restarts.
    """

    def open_ticket(ctx: Context) -> dict[str, str]:
        for attempt in range(3):
            try:
                summary = ctx.provider.call()
                break
            except TransientProviderError:
                if attempt == 2:
                    raise

        ctx.checkpoint("before_commit")
        ticket = ctx.effects.commit(
            "ticket", operation_id=OPERATION_ID,
            idempotency_key=OPERATION_ID if safe else None,
        )
        # The service committed the ticket, but the worker may stop before the
        # caller receives its ID. The next invocation starts from the beginning.
        ctx.checkpoint("after_commit")
        return {"ticket_id": ticket.id, "summary": summary}

    return open_ticket


def demo_cases() -> dict[str, tuple[Scenario, Expectations]]:
    ticket = (ExpectedEffect("ticket", OPERATION_ID),)
    return {
        "baseline": (
            Scenario("baseline", (Reply(SUMMARY),)),
            Expectations(1, 1, ticket),
        ),
        "rate-limit": (
            Scenario("rate-limit", (Fail("rate_limit"), Reply(SUMMARY))),
            Expectations(2, 1, ticket),
        ),
        "outage": (
            Scenario("outage", (Fail("rate_limit"), Fail("timeout"), Fail("unavailable"))),
            Expectations(3, 1, require_completion=False, expected_error=TransientProviderError),
        ),
        "before-commit": (
            Scenario(
                "before-commit", (Reply(SUMMARY), Reply(SUMMARY)),
                (Interruption("before_commit"),), max_restarts=1,
            ),
            Expectations(2, 2, ticket),
        ),
        "after-commit": (
            Scenario(
                "after-commit", (Reply(SUMMARY), Reply(SUMMARY)),
                (Interruption("after_commit"),), max_restarts=1,
            ),
            Expectations(2, 2, ticket),
        ),
    }


def main(argv: list[str] | None = None) -> int:
    cases = demo_cases()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("safe", "unsafe"), default="safe")
    parser.add_argument("--scenario", choices=("all", *cases), default="all")
    parser.add_argument("--json", action="store_true", help="Print a JSON report (no timestamps or random IDs)")
    args = parser.parse_args(argv)
    chosen = cases if args.scenario == "all" else {args.scenario: cases[args.scenario]}
    reports = [
        run(scenario, ticket_agent(safe=args.strategy == "safe"), checks)
        for scenario, checks in chosen.values()
    ]
    passed = all(report.passed for report in reports)
    if args.json:
        print(json.dumps({
            "strategy": args.strategy,
            "passed": passed,
            "reports": [report.to_dict() for report in reports],
        }, indent=2))
    else:
        for report in reports:
            print(f"{'PASS' if report.passed else 'FAIL'} {report.scenario}: "
                  f"provider_attempts={report.provider_attempts} runs={report.workflow_runs} "
                  f"committed_effects={len(report.effects)} completed={report.completed}")
            for violation in report.violations:
                print(f"  {violation.code}: {violation.message}")
        print(f"Strategy: {args.strategy}. {'All invariants passed.' if passed else 'Recovery invariants failed.'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
