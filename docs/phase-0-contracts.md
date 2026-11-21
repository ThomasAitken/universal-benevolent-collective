# Phase 0 runtime contracts

Phase 0 establishes the node supervisor's boundaries before choosing a real
model provider or browser harness. The implementation deliberately uses only
Python standard-library facilities and deterministic fakes.

## Contracts

| Contract | Responsibility |
|---|---|
| `TaskSpec` | Identifies one objective and its hard run budget. |
| `AgentBackend` | Proposes one action at a time; it cannot execute actions. |
| `BrowserRuntime` | Executes an authorized browser action or performs a human handoff. |
| `ActionProposal` | Describes an untrusted browser action, handoff, or completion. |
| `PolicyDecision` | Allows, denies, or replaces an action with a required handoff. |
| `HumanHandoff` | States why, where, and how human control is requested. |
| `RunEvent` | Records one sequenced fact in the run history. |
| `RunResult` | Reports the terminal state, output/error, usage, and event count. |

Supporting contracts include `RunBudget`, `BudgetUsage`, `PolicyEvaluator`, and
`EventSink`. They keep limits, authorization, and persistence outside the model
and browser implementations.

## Lifecycle

The supervisor is the only component allowed to move the lifecycle forward:

```text
created -> running -> awaiting_human -> running
                   \-> completed | failed | cancelled
awaiting_human --------------------> failed | cancelled
```

Every transition is checked by `RunStateMachine`; terminal states cannot be
reopened. Cancellation is cooperative and checked before model work and after
each model, policy, browser, and handoff boundary.

## Event log

`JsonlEventLog` appends one canonical JSON object per line and flushes it before
returning. Events have a run-local sequence number, UTC-capable timestamp,
current lifecycle state, event kind, and JSON payload. The format is intended
to remain directly inspectable during prototyping; it is not yet a concurrent
distributed journal or a substitute for the later control-plane database.

## Deterministic test implementations

The test-only `ScriptedAgentBackend`, `FakeBrowserRuntime`,
`ScriptedPolicyEvaluator`, and `MemoryEventSink` live under `tests/` and allow
complete runs to be exercised without network access, credentials, a model, or
a real browser. Tests cover successful completion, browser observations, human
handoff and return, policy denial, cancellation at different checkpoints,
component failures, every budget, event persistence, and invalid lifecycle
transitions.

## Deferred by design

Phase 0 does not include Stagehand, model-provider SDKs, sandboxing, enrollment,
a control plane, or PostgreSQL/Temporal. Later implementations must conform to
these contracts or change them explicitly with corresponding tests and a
migration note.
