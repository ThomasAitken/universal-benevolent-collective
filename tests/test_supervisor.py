"""Deterministic scenarios for the Phase 0 run supervisor."""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
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
    FatalRunError,
    HumanHandoff,
    JsonValue,
    ModelUsage,
    PolicyDecision,
    PolicyOutcome,
    RunBudget,
    RunEvent,
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

    result = await supervisor.run(_task(budget=RunBudget(max_consecutive_failures=1)))

    assert result.state is RunState.FAILED
    assert (
        result.error
        == "consecutive-failure budget exhausted: RuntimeError: scripted agent has no proposal remaining"
    )
    assert result.usage.consecutive_failures == 1
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


@pytest.mark.asyncio
async def test_token_and_cost_budgets_are_enforced_before_and_after_model_calls() -> None:
    heavy_usage = ModelUsage(input_tokens=60, output_tokens=50, estimated_cost_usd=0.05)
    supervisor, browser, sink, _ = _supervisor(
        [ActionProposal.browser("Browse", usage=heavy_usage)]
    )
    result = await supervisor.run(_task(budget=RunBudget(max_tokens=100)))
    assert result.state is RunState.FAILED
    assert result.error == "token budget exhausted"
    assert result.usage.total_tokens == 110
    assert browser.actions == []
    proposed = next(e for e in sink.events if e.kind is EventKind.ACTION_PROPOSED)
    assert proposed.payload["usage"] == heavy_usage.to_record()

    exact_tokens = ModelUsage(input_tokens=50, output_tokens=50, estimated_cost_usd=0.01)
    supervisor2, _, _, _ = _supervisor(
        [
            ActionProposal.browser("Step 1", usage=exact_tokens),
            ActionProposal.complete("never reached"),
        ]
    )
    result2 = await supervisor2.run(_task(budget=RunBudget(max_tokens=100)))
    assert result2.state is RunState.FAILED
    assert result2.error == "token budget exhausted"
    assert result2.usage.model_calls == 1

    over_cost = ModelUsage(input_tokens=10, output_tokens=10, estimated_cost_usd=0.60)
    supervisor3, _, _, _ = _supervisor([ActionProposal.browser("Browse", usage=over_cost)])
    result3 = await supervisor3.run(_task(budget=RunBudget(max_estimated_cost_usd=0.50)))
    assert result3.state is RunState.FAILED
    assert result3.error == "cost budget exhausted"

    exact_cost = ModelUsage(input_tokens=10, output_tokens=10, estimated_cost_usd=0.50)
    supervisor4, _, _, _ = _supervisor(
        [
            ActionProposal.browser("Step 1", usage=exact_cost),
            ActionProposal.complete("never reached"),
        ]
    )
    result4 = await supervisor4.run(_task(budget=RunBudget(max_estimated_cost_usd=0.50)))
    assert result4.state is RunState.FAILED
    assert result4.error == "cost budget exhausted"
    assert result4.usage.model_calls == 1


@pytest.mark.asyncio
async def test_download_budget_is_enforced_independently() -> None:
    download_action = ActionProposal.browser(
        "Download report", {"operation": "download", "candidate_id": "c-1"}
    )
    supervisor, browser, _, _ = _supervisor([download_action, download_action])
    result = await supervisor.run(_task(budget=RunBudget(max_downloads=1)))

    assert result.state is RunState.FAILED
    assert result.error == "download budget exhausted"
    assert result.usage.downloads == 1
    assert result.usage.browser_actions == 1
    assert len(browser.actions) == 1


@pytest.mark.asyncio
async def test_consecutive_failures_recover_on_success_and_stop_at_limit() -> None:
    class FlakyAgent:
        def __init__(self, responses: Sequence[ActionProposal | Exception]) -> None:
            self._responses = list(responses)

        async def propose(
            self,
            task: TaskSpec,
            history: Sequence[RunEvent],
        ) -> ActionProposal:
            del task, history
            item = self._responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    sink = MemoryEventSink()
    agent = FlakyAgent(
        [
            ValueError("bad proposal 1"),
            ActionProposal.browser("Recovering action", {"operation": "inspect"}),
            ValueError("bad proposal 2"),
            ActionProposal.complete("recovered"),
        ]
    )
    supervisor = RunSupervisor(
        agent=agent,
        browser=FakeBrowserRuntime(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=sink,
        clock=lambda: NOW,
        run_id_factory=lambda: "run-recover",
    )

    result = await supervisor.run(_task(budget=RunBudget(max_consecutive_failures=2)))
    assert result.state is RunState.COMPLETED
    assert result.output == "recovered"
    assert result.usage.consecutive_failures == 0
    step_failures = [e for e in sink.events if e.kind is EventKind.STEP_FAILED]
    assert len(step_failures) == 2


@pytest.mark.asyncio
async def test_fatal_error_and_handoff_error_fail_immediately_without_retry() -> None:
    class FatalAgent:
        async def propose(
            self,
            task: TaskSpec,
            history: Sequence[RunEvent],
        ) -> ActionProposal:
            del task, history
            raise FatalRunError("revoked session")

    sink = MemoryEventSink()
    supervisor = RunSupervisor(
        agent=FatalAgent(),
        browser=FakeBrowserRuntime(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=sink,
        clock=lambda: NOW,
        run_id_factory=lambda: "run-fatal",
    )
    result = await supervisor.run(_task(budget=RunBudget(max_consecutive_failures=3)))
    assert result.state is RunState.FAILED
    assert result.error == "FatalRunError: revoked session"
    assert result.usage.model_calls == 1

    class FailingHandoffBrowser(FakeBrowserRuntime):
        async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
            del request
            raise RuntimeError("human declined control")

    supervisor2, _, _, _ = _supervisor(
        [ActionProposal.request_handoff("Login", HANDOFF)],
        browser=FailingHandoffBrowser(),
    )
    result2 = await supervisor2.run(_task(budget=RunBudget(max_consecutive_failures=3)))
    assert result2.state is RunState.FAILED
    assert result2.error == "RuntimeError: human declined control"
    assert result2.usage.handoffs == 1


@pytest.mark.asyncio
async def test_browser_state_observer_and_duration_budgets_are_enforced() -> None:
    class ObservingBrowser(FakeBrowserRuntime):
        def __init__(self, *, on_observe: Callable[[], None] | None = None) -> None:
            super().__init__()
            self._on_observe = on_observe

        async def observe_state(self) -> Mapping[str, JsonValue]:
            if self._on_observe is not None:
                self._on_observe()
            return {"session_state": "active", "tabs": [], "active_page": None}

    # Normal observation before proposal
    sink = MemoryEventSink()
    supervisor = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.complete("done")]),
        browser=ObservingBrowser(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=sink,
        clock=lambda: NOW,
        run_id_factory=lambda: "run-obs",
    )
    result = await supervisor.run(_task())
    assert result.state is RunState.COMPLETED
    assert any(e.kind is EventKind.BROWSER_STATE_OBSERVED for e in sink.events)

    # Cancellation right after observe_state
    token = CancellationToken()
    supervisor_cancel = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.complete("never")]),
        browser=ObservingBrowser(on_observe=token.cancel),
        policy=ScriptedPolicyEvaluator(),
        event_sink=MemoryEventSink(),
        clock=lambda: NOW,
        run_id_factory=lambda: "run-obs-cancel",
    )
    res_cancel = await supervisor_cancel.run(_task(), cancellation=token)
    assert res_cancel.state is RunState.CANCELLED
    assert res_cancel.usage.model_calls == 0

    # Duration exhausted at loop entry
    times = iter([NOW, NOW, NOW, NOW + timedelta(seconds=10), NOW + timedelta(seconds=10)])
    last_time = NOW

    def advancing_clock() -> datetime:
        nonlocal last_time
        last_time = next(times, last_time)
        return last_time

    supervisor_dur_start = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.complete("never")]),
        browser=FakeBrowserRuntime(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=MemoryEventSink(),
        clock=advancing_clock,
        run_id_factory=lambda: "run-dur-start",
    )
    res_dur_start = await supervisor_dur_start.run(
        _task(budget=RunBudget(max_duration_seconds=5.0))
    )
    assert res_dur_start.state is RunState.FAILED
    assert res_dur_start.error == "duration budget exhausted"
    assert res_dur_start.usage.duration_seconds == 10.0

    # Duration exhausted right after observe_state
    now_ref = [NOW]

    def bump_after_observe() -> None:
        now_ref[0] = NOW + timedelta(seconds=10)

    supervisor_dur_obs = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.complete("never")]),
        browser=ObservingBrowser(on_observe=bump_after_observe),
        policy=ScriptedPolicyEvaluator(),
        event_sink=MemoryEventSink(),
        clock=lambda: now_ref[0],
        run_id_factory=lambda: "run-dur-obs",
    )
    res_dur_obs = await supervisor_dur_obs.run(_task(budget=RunBudget(max_duration_seconds=5.0)))
    assert res_dur_obs.state is RunState.FAILED
    assert res_dur_obs.error == "duration budget exhausted"
    assert res_dur_obs.usage.model_calls == 0

    # Duration exhausted right after model propose
    now_ref2 = [NOW]
    supervisor_dur_prop = RunSupervisor(
        agent=ScriptedAgentBackend(
            [ActionProposal.complete("late")],
            on_propose=lambda _: now_ref2.__setitem__(0, NOW + timedelta(seconds=10)),
        ),
        browser=FakeBrowserRuntime(),
        policy=ScriptedPolicyEvaluator(),
        event_sink=MemoryEventSink(),
        clock=lambda: now_ref2[0],
        run_id_factory=lambda: "run-dur-prop",
    )
    res_dur_prop = await supervisor_dur_prop.run(_task(budget=RunBudget(max_duration_seconds=5.0)))
    assert res_dur_prop.state is RunState.FAILED
    assert res_dur_prop.error == "duration budget exhausted"
    assert res_dur_prop.usage.model_calls == 1

    # Duration exhausted right after handoff
    now_ref3 = [NOW]
    supervisor_dur_ho = RunSupervisor(
        agent=ScriptedAgentBackend([ActionProposal.request_handoff("Login", HANDOFF)]),
        browser=FakeBrowserRuntime(
            on_handoff=lambda _: now_ref3.__setitem__(0, NOW + timedelta(seconds=10))
        ),
        policy=ScriptedPolicyEvaluator(),
        event_sink=MemoryEventSink(),
        clock=lambda: now_ref3[0],
        run_id_factory=lambda: "run-dur-ho",
    )
    res_dur_ho = await supervisor_dur_ho.run(_task(budget=RunBudget(max_duration_seconds=5.0)))
    assert res_dur_ho.state is RunState.FAILED
    assert res_dur_ho.error == "duration budget exhausted"
    assert res_dur_ho.usage.handoffs == 1


@pytest.mark.asyncio
async def test_task_aware_takeover_decline_and_resume_policy_recheck() -> None:
    class TaskAwareBrowser(FakeBrowserRuntime):
        def __init__(self, result: Mapping[str, JsonValue]) -> None:
            super().__init__()
            self._result = result
            self.seen_tasks: list[TaskSpec] = []

        async def handoff_for_task(
            self,
            task: TaskSpec,
            request: HumanHandoff,
        ) -> Mapping[str, JsonValue]:
            self.seen_tasks.append(task)
            self.handoffs.append(request)
            return dict(self._result)

    # Human explicitly declines takeover -> CANCELLED
    declining_browser = TaskAwareBrowser(
        {
            "authentication_completed": False,
            "control_returned": False,
            "origin": "https://example.test",
            "transient_state_cleared": True,
        }
    )
    supervisor, _, sink, _ = _supervisor(
        [ActionProposal.request_handoff("Authenticate", HANDOFF)],
        browser=declining_browser,
    )
    result = await supervisor.run(_task())
    assert result.state is RunState.CANCELLED
    assert result.error == "human declined takeover control"
    assert len(declining_browser.seen_tasks) == 1
    assert sink.events[-2].payload == {"from": "awaiting_human", "to": "cancelled"}

    # Human returns control, but resume policy recheck denies -> FAILED
    returning_browser = TaskAwareBrowser(
        {
            "authentication_completed": True,
            "control_returned": True,
            "origin": "https://example.test",
            "transient_state_cleared": True,
        }
    )
    policy = ScriptedPolicyEvaluator(
        [
            PolicyDecision(PolicyOutcome.ALLOW, "allow handoff"),
            PolicyDecision(PolicyOutcome.DENY, "post-handoff policy revoked"),
        ]
    )
    supervisor2, _, _, _ = _supervisor(
        [ActionProposal.request_handoff("Authenticate", HANDOFF)],
        browser=returning_browser,
        policy=policy,
    )
    result2 = await supervisor2.run(_task())
    assert result2.state is RunState.FAILED
    assert result2.error == "policy denied resume after handoff: post-handoff policy revoked"
    assert policy.proposals[-1].parameters == {
        "operation": "resume_after_handoff",
        "origin": "https://example.test",
    }
