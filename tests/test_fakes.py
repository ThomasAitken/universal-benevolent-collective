"""Direct tests for deterministic fake components."""

from datetime import UTC, datetime

import pytest

from tests.fakes import (
    FakeBrowserRuntime,
    MemoryEventSink,
    ScriptedAgentBackend,
    ScriptedPolicyEvaluator,
)
from ubc_node.core.contracts import (
    ActionProposal,
    EventKind,
    HumanHandoff,
    PolicyDecision,
    PolicyOutcome,
    RunEvent,
    RunState,
    TaskSpec,
)

TASK = TaskSpec("task-1", "Test deterministic components")
HANDOFF = HumanHandoff("Login", "https://example.test", "Complete login")


@pytest.mark.asyncio
async def test_scripted_agent_runs_hook_and_exhausts_script() -> None:
    calls: list[int] = []
    proposal = ActionProposal.complete("done")
    agent = ScriptedAgentBackend([proposal], on_propose=calls.append)

    assert await agent.propose(TASK, ()) is proposal
    assert calls == [1]
    assert agent.calls == 1
    with pytest.raises(RuntimeError, match="no proposal"):
        await agent.propose(TASK, ())


@pytest.mark.asyncio
async def test_fake_browser_returns_scripted_then_default_results() -> None:
    action_calls: list[int] = []
    handoff_calls: list[int] = []
    browser = FakeBrowserRuntime(
        action_results=[{"title": "Example"}],
        handoff_results=[{"status": "authenticated"}],
        on_execute=action_calls.append,
        on_handoff=handoff_calls.append,
    )
    proposal = ActionProposal.browser("Open example")

    assert await browser.execute(proposal) == {"title": "Example"}
    assert await browser.execute(proposal) == {"status": "ok"}
    assert await browser.handoff(HANDOFF) == {"status": "authenticated"}
    assert await browser.handoff(HANDOFF) == {"status": "returned"}
    assert action_calls == [1, 2]
    assert handoff_calls == [1, 2]


@pytest.mark.asyncio
async def test_scripted_policy_falls_back_to_default() -> None:
    denied = PolicyDecision(PolicyOutcome.DENY, "blocked")
    allowed = PolicyDecision(PolicyOutcome.ALLOW, "default allow")
    policy = ScriptedPolicyEvaluator([denied], default=allowed)
    proposal = ActionProposal.browser("Open example")

    assert await policy.evaluate(TASK, proposal) is denied
    assert await policy.evaluate(TASK, proposal) is allowed
    assert policy.proposals == [proposal, proposal]


def test_memory_event_sink_preserves_order() -> None:
    sink = MemoryEventSink()
    event = RunEvent(
        1,
        "run-1",
        "task-1",
        EventKind.RUN_CREATED,
        RunState.CREATED,
        datetime(2026, 11, 21, tzinfo=UTC),
    )
    sink.append(event)
    assert sink.events == [event]
