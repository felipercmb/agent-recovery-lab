# Contributing

Agent Recovery Lab is a small test harness for recovery behavior. Keep contributions focused on a concrete failure that developers can reproduce and understand.

## Local development

Use Python 3.11 or newer. The runtime and test suite require only the standard library:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m agent_recovery.demo
```

You can optionally install the checkout with `python -m pip install -e .`. Package-building tools may need to be downloaded by pip; running from source does not require them.

Before proposing a change, run the tests and both sides of the duplicate-ticket example:

```bash
PYTHONPATH=src python -m agent_recovery.demo --scenario after-commit --strategy safe
PYTHONPATH=src python -m agent_recovery.demo --scenario after-commit --strategy unsafe
```

The safe command must exit with code 0. The unsafe command must exit with code 1 because it detects the deliberately introduced duplicate. Do not change the unsafe example to pass.

## A useful bug report

Include the Python version, the smallest workflow and scenario that reproduce the problem, the expected invariant, and the actual report. Use synthetic operation IDs and responses. Remove credentials and private business data before sharing any example or report.

For a false positive, explain why the effects and attempt counts should be acceptable. For a false negative, show the business effect that escaped detection. A reproducible small test is more useful than a reliability claim about an entire agent framework.

## Implementation guidelines

- Preserve deterministic behavior: no model requests, random outcomes, real sleeps, or wall-clock assertions in core tests.
- Keep provider attempts, effect attempts, and committed effects distinct.
- Keep counters and committed effects across supervisor restarts within one scenario; isolate separate scenarios.
- Include a failing control when adding a recovery mechanism, so the test demonstrates that it detects the defect it claims to catch.
- Treat a missing checkpoint or an exhausted provider script as a test configuration problem, not a passing recovery result.
- Document limits of any new adapter. Do not imply that a fake service proves a production service is idempotent.
- Avoid runtime dependencies unless they enable a concrete, well-scoped improvement.

Please keep documentation and examples in English. Add focused regression tests for behavioral changes, and update the public examples when the API changes.

## Scope for future work

Potential additions include a real-service ledger adapter, a Pydantic AI example, and execution in a subprocess with a timeout. These are directions to evaluate, not implemented features or release commitments.

By contributing, you agree that your contribution may be distributed under the project's MIT License.
