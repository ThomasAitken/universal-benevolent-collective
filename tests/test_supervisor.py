"""Deterministic scenarios for the Phase 0 run supervisor."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tests.fakes import (
    FakeBrowserRuntime,
    MemoryEventSink,
    ScriptedAgentBackend,
    ScriptedPolicyEvaluator,
)
from ubc_node.contracts import (
    ActionProposal,
    EventKind,
    HumanHandoff,
    PolicyDecision,
    PolicyOutcome,
    RunBudget,
    RunState,
    TaskSpec,
)
from ubc_node.supervisor import CancellationToken, RunSupervisor

NOW = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)
HANDOFF = HumanHandoff(
    reason="Authentication required",
    origin="https://example.test",
    instructions="Sign in and explicitly return control.",
)


def _task(*, budget: RunBudget | None = None) -> TaskSpec:
    return TaskSpec(
        "task-1",
        "Research the example site",
        budget=budget or RunBudget(),
    )


def _supervisor(
    proposals: Sequence[ActionProposal],
    *,
    browser: FakeBrowserRuntime | None = None,
    policy: ScriptedPolicyEvaluator | None = None,
    cancellation: CancellationToken | None = None,
    cancel_on_propose: bool = False,
) -> tuple[RunSupervisor, FakeBrowserRuntime, MemoryEventSink, CancellationToken]:
    token = cancellation or CancellationToken()
    fake_browser = browser or FakeBrowserRuntime()
    sink = MemoryEventSink()
    callback: Callable[[int], None] | None = (
        (lambda _call_number: token.cancel()) if cancel_on_propose else None
    )
    supervisor = RunSupervisor(
        agent=ScriptedAgentBackend(proposals, on_propose=callback),
        browser=fake_browser,
        policy=policy or ScriptedPolicyEvaluator(),
        event_sink=sink,
        clock=lambda: NOW,
        run_id_factory=lambda: "run-1",
    )
    return supervisor, fake_browser, sink, token


@pytest.mark.asyncio
async def test_complete_run_records_ordered_terminal_history() -> None:
    supervisor, browser, sink, token = _supervisor([ActionProposal.complete("answer")])
    assert token.cancelled is False

    result = await supervisor.run(_task(), cancellation=token)

    assert result.state is RunState.COMPLETED
    assert result.output == "answer"
    assert result.error is None
    assert result.usage.steps == 1
    assert result.usage.model_calls == 1
    assert result.usage.browser_actions == 0
    assert result.usage.handoffs == 0
    assert result.event_count == len(sink.events)
    assert browser.actions == []
    assert [event.sequence for event in sink.events] == list(range(1, len(sink.events) + 1))
    assert [event.kind for event in sink.events] == [
        EventKind.RUN_CREATED,
        EventKind.STATE_CHANGED,
        EventKind.ACTION_PROPOSED,
        EventKind.POLICY_DECIDED,
        EventKind.STATE_CHANGED,
        EventKind.RUN_FINISHED,
    ]
    assert sink.events[-1].payload == {"state": "completed", "output": "answer"}


@pytest.mark.asyncio
async def test_browser_observation_is_recorded_before_next_proposal() -> None:
    action = ActionProposal.browser("Read the page", {"url": "https://example.test"})
    browser = FakeBrowserRuntime(action_results=[{"title": "Example Domain"}])
    supervisor, browser, sink, _ = _supervisor(
        [action, ActionProposal.complete("Example Domain")],
        browser=browser,
    )

    result = await supervisor.run(_task())

    assert result.state is RunState.COMPLETED
    assert result.usage.browser_actions == 1
    assert browser.actions == [action]
    observation = next(
        event for event in sink.events if event.kind is EventKind.BROWSER_ACTION_COMPLETED
    )
    assert observation.payload == {"observation": {"title": "Example Domain"}}


@pytest.mark.asyncio
async def test_agent_handoff_pauses_then_resumes_run() -> None:
    request = ActionProposal.request_handoff("Ask the owner to authenticate", HANDOFF)
    browser = FakeBrowserRuntime(handoff_results=[{"status": "authenticated"}])
    supervisor, browser, sink, _ = _supervisor(
        [request, ActionProposal.complete("authenticated")],
        browser=browser,
    )

    result = await supervisor.run(_task())

    assert result.state is RunState.COMPLETED
    assert result.usage.handoffs == 1
    assert browser.handoffs == [HANDOFF]
    transitions = [event.payload for event in sink.events if event.kind is EventKind.STATE_CHANGED]
    assert transitions == [
        {"from": "created", "to": "running"},
        {"from": "running", "to": "awaiting_human"},
        {"from": "awaiting_human", "to": "running"},
        {"from": "running", "to": "completed"},
    ]


@pytest.mark.asyncio
async def test_policy_can_require_handoff_instead_of_browser_execution() -> None:
    action = ActionProposal.browser("Open a protected account")
    policy = ScriptedPolicyEvaluator(
        [PolicyDecision(PolicyOutcome.REQUIRE_HUMAN, "authentication boundary", HANDOFF)]
    )
    supervisor, browser, _, _ = _supervisor(
        [action, ActionProposal.complete("owner authenticated")],
        policy=policy,
    )

    result = await supervisor.run(_task())

    assert result.state is RunState.COMPLETED
    assert browser.actions == []
    assert browser.handoffs == [HANDOFF]


@pytest.mark.asyncio
async def test_policy_denial_fails_without_executing_action() -> None:
    policy = ScriptedPolicyEvaluator([PolicyDecision(PolicyOutcome.DENY, "origin is not allowed")])
    supervisor, browser, sink, _ = _supervisor(
        [ActionProposal.browser("Open an unapproved origin")],
        policy=policy,
    )

    result = await supervisor.run(_task())

    assert result.state is RunState.FAILED
    assert result.error == "policy denied action: origin is not allowed"
    assert browser.actions == []
    assert sink.events[-1].payload["error"] == result.error


@pytest.mark.asyncio
async def test_preexisting_cancellation_starts_then_cancels_run() -> None:
    token = CancellationToken()
    token.cancel()
    supervisor, _, sink, token = _supervisor([], cancellation=token)
    assert token.cancelled is True

    result = await supervisor.run(_task(), cancellation=token)

    assert result.state is RunState.CANCELLED
    assert result.usage.model_calls == 0
    assert [event.state for event in sink.events] == [
        RunState.CREATED,
        RunState.RUNNING,
        RunState.CANCELLED,
        RunState.CANCELLED,
    ]


@pytest.mark.asyncio
async def test_cancellation_after_model_call_prevents_policy_and_execution() -> None:
    supervisor, browser, _, token = _supervisor(
        [ActionProposal.browser("Do not execute")],
        cancel_on_propose=True,
    )

    result = await supervisor.run(_task(), cancellation=token)

    assert result.state is RunState.CANCELLED
    assert result.usage.model_calls == 1
    assert browser.actions == []


@pytest.mark.asyncio
async def test_cancellation_during_handoff_finishes_from_awaiting_state() -> None:
    token = CancellationToken()
    browser = FakeBrowserRuntime(on_handoff=lambda _: token.cancel())
    supervisor, _, sink, _ = _supervisor(
        [ActionProposal.request_handoff("Authenticate", HANDOFF)],
        browser=browser,
        cancellation=token,
    )

    result = await supervisor.run(_task(), cancellation=token)

    assert result.state is RunState.CANCELLED
    assert result.usage.handoffs == 1
    assert sink.events[-2].payload == {
        "from": "awaiting_human",
        "to": "cancelled",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "proposals", "expected_error"),
    [
        (
            RunBudget(max_steps=1, max_model_calls=2, max_browser_actions=2),
            [ActionProposal.browser("First action")],
            "step budget exhausted",
        ),
        (
            RunBudget(max_steps=2, max_model_calls=1, max_browser_actions=2),
            [ActionProposal.browser("First action")],
            "model-call budget exhausted",
        ),
        (
            RunBudget(max_steps=2, max_model_calls=2, max_browser_actions=1),
            [ActionProposal.browser("First action"), ActionProposal.browser("Second action")],
            "browser-action budget exhausted",
        ),
        (
            RunBudget(max_handoffs=0),
            [ActionProposal.request_handoff("Authenticate", HANDOFF)],
            "human-handoff budget exhausted",
        ),
    ],
)
async def test_each_budget_is_enforced_deterministically(
    budget: RunBudget,
    proposals: Sequence[ActionProposal],
    expected_error: str,
) -> None:
    supervisor, _, _, _ = _supervisor(proposals)

    result = await supervisor.run(_task(budget=budget))

    assert result.state is RunState.FAILED
    assert result.error == expected_error


@pytest.mark.asyncio
async def test_backend_failure_becomes_failed_result_and_event() -> None:
    supervisor, _, sink, _ = _supervisor([])

    result = await supervisor.run(_task())

    assert result.state is RunState.FAILED
    assert result.error == "RuntimeError: scripted agent has no proposal remaining"
    assert sink.events[-1].payload["error"] == result.error


@pytest.mark.asyncio
async def test_default_clock_and_run_id_factories_produce_valid_metadata() -> None:
    sink = MemoryEventSink()
    supervisor = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.complete("done")]),
        browser=FakeBrowserRuntime(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=sink,
    )

    result = await supervisor.run(_task())

    assert UUID(result.run_id).version == 4
    assert all(event.occurred_at.tzinfo is not None for event in sink.events)
