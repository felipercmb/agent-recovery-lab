# Related work

Agent Recovery Lab does not claim to introduce fault injection or agent testing. Its focus is a small, framework-independent test harness for committed business effects and cumulative attempt budgets across repeated workflow executions.

## LLMock

[JulienRabault/LLMock](https://github.com/JulienRabault/LLMock) provides an HTTP mock server, pytest integration, programmable failures, streaming and tool-call support, and resilience assertions. Its documented `assert_resilient()` behavior covers concerns such as retry storms, ignoring `Retry-After`, retrying non-recoverable errors, and accepting truncated streams. It already goes beyond simply returning mocked responses.

That makes LLMock a better starting point for testing an application's actual provider HTTP integration. Agent Recovery Lab does not emulate provider protocols. It models a fake business service whose writes survive a simulated workflow interruption, then checks the number of committed effects after a restart.

There is also a distinct [larsakerlund/llmock](https://github.com/larsakerlund/llmock), implemented in Rust, with provider emulation, streaming, error simulation, and record/replay features. The owner-qualified links matter because these are separate projects with the same name.

## Pydantic AI

[Pydantic AI's testing guide](https://pydantic.dev/docs/ai/guides/testing/) documents `TestModel`, `FunctionModel`, agent overrides, and blocking real model requests during tests. These are useful foundations for deterministic tests of agents and their tools.

[Pydantic AI's usage limits](https://pydantic.dev/docs/ai/core-concepts/agent/#usage-limits) provide request and tool limits. Its documentation discusses the distinction between requests represented in the agent flow and lower-level SDK or transport attempts, as well as carrying usage across resumed executions.

Agent Recovery Lab does not replace these capabilities. It makes the logical business action explicit and checks the committed result after selected interruption boundaries. Its counters measure calls through this harness, not every hidden request inside an SDK. A future framework adapter would need to document precisely which attempts it observes.

## Choosing the right layer

| Need | Relevant approach |
| --- | --- |
| Exercise a real SDK against provider-shaped HTTP responses | A provider mock such as LLMock |
| Substitute model decisions in a Pydantic AI unit test | `TestModel`, `FunctionModel`, or agent overrides |
| Check a selected restart path for duplicate business effects | This harness's ledger, checkpoints, and expectations |
| Prove a real ticketing API honors idempotency keys | An integration test against that service |
| Evaluate answer quality or model reasoning | An evaluation suite with task-specific data and criteria |

These layers can be complementary. This MVP does not ship a direct LLMock or Pydantic AI adapter. Its bundled example is a scripted workflow, not a claim that a real model or a particular framework has passed these checks.
