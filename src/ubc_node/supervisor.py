"""Bounded Phase 0 supervisor built entirely against contracts."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from ubc_node.contracts import (
    ActionKind,
    AgentBackend,
    BrowserRuntime,
    BudgetUsage,
    EventKind,
    EventSink,
    HumanHandoff,
    JsonValue,
    PolicyDecision,
    PolicyEvaluator,
    PolicyOutcome,
    RunEvent,
    RunResult,
    RunState,
    TaskSpec,
)
from ubc_node.state_machine import RunStateMachine


class CancellationToken:
    """A cooperative cancellation flag checked at every supervisor boundary."""

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._cancelled

    def cancel(self) -> None:
        """Request cancellation of the current run."""
        self._cancelled = True


class RunSupervisor:
    """Coordinate one bounded run without trusting either backend."""

    def __init__(
        self,
        *,
        agent: AgentBackend,
        browser: BrowserRuntime,
        policy: PolicyEvaluator,
        event_sink: EventSink,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._agent = agent
        self._browser = browser
        self._policy = policy
        self._event_sink = event_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._run_id_factory = run_id_factory or (lambda: str(uuid4()))

    async def run(
        self,
        task: TaskSpec,
        *,
        cancellation: CancellationToken | None = None,
    ) -> RunResult:
        """Execute a task until it reaches one explicit terminal state."""
        token = cancellation or CancellationToken()
        run_id = self._run_id_factory()
        machine = RunStateMachine()
        history: list[RunEvent] = []
        usage: BudgetUsage = BudgetUsage()

        def emit(kind: EventKind, payload: Mapping[str, JsonValue]) -> None:
            event = RunEvent(
                sequence=len(history) + 1,
                run_id=run_id,
                task_id=task.task_id,
                kind=kind,
                state=machine.state,
                occurred_at=self._clock(),
                payload=payload,
            )
            self._event_sink.append(event)
            history.append(event)

        def transition(target: RunState) -> None:
            previous = machine.transition(target)
            emit(
                EventKind.STATE_CHANGED,
                {"from": previous.value, "to": target.value},
            )

        def finish(
            state: RunState,
            *,
            output: str | None = None,
            error: str | None = None,
        ) -> RunResult:
            transition(state)
            payload: dict[str, JsonValue] = {"state": state.value}
            if output is not None:
                payload["output"] = output
            if error is not None:
                payload["error"] = error
            emit(EventKind.RUN_FINISHED, payload)
            return RunResult(
                run_id=run_id,
                task_id=task.task_id,
                state=state,
                usage=usage,
                event_count=len(history),
                output=output,
                error=error,
            )

        emit(
            EventKind.RUN_CREATED,
            {
                "objective": task.objective,
                "max_steps": task.budget.max_steps,
                "max_model_calls": task.budget.max_model_calls,
                "max_browser_actions": task.budget.max_browser_actions,
                "max_handoffs": task.budget.max_handoffs,
            },
        )
        transition(RunState.RUNNING)

        while True:
            if token.cancelled:
                return finish(RunState.CANCELLED, error="cancellation requested")
            if usage.steps >= task.budget.max_steps:
                return finish(RunState.FAILED, error="step budget exhausted")
            if usage.model_calls >= task.budget.max_model_calls:
                return finish(RunState.FAILED, error="model-call budget exhausted")

            usage = replace(
                usage,
                steps=usage.steps + 1,
                model_calls=usage.model_calls + 1,
            )
            try:
                proposal = await self._agent.propose(task, tuple(history))
                emit(
                    EventKind.ACTION_PROPOSED,
                    {
                        "kind": proposal.kind.value,
                        "description": proposal.description,
                        "parameters": dict(proposal.parameters),
                    },
                )
                if token.cancelled:
                    return finish(RunState.CANCELLED, error="cancellation requested")

                decision = await self._policy.evaluate(task, proposal)
                emit(
                    EventKind.POLICY_DECIDED,
                    {"outcome": decision.outcome.value, "reason": decision.reason},
                )
                if decision.outcome is PolicyOutcome.DENY:
                    return finish(RunState.FAILED, error=f"policy denied action: {decision.reason}")

                handoff = self._handoff_for(proposal.kind, proposal.handoff, decision)
                if handoff is not None:
                    if usage.handoffs >= task.budget.max_handoffs:
                        return finish(RunState.FAILED, error="human-handoff budget exhausted")
                    usage = replace(usage, handoffs=usage.handoffs + 1)
                    transition(RunState.AWAITING_HUMAN)
                    emit(
                        EventKind.HANDOFF_REQUESTED,
                        {
                            "reason": handoff.reason,
                            "origin": handoff.origin,
                            "instructions": handoff.instructions,
                        },
                    )
                    observation = await self._browser.handoff(handoff)
                    emit(EventKind.HANDOFF_COMPLETED, {"observation": dict(observation)})
                    if token.cancelled:
                        return finish(RunState.CANCELLED, error="cancellation requested")
                    transition(RunState.RUNNING)
                    continue

                if proposal.kind is ActionKind.COMPLETE:
                    return finish(RunState.COMPLETED, output=proposal.output)

                if usage.browser_actions >= task.budget.max_browser_actions:
                    return finish(RunState.FAILED, error="browser-action budget exhausted")
                usage = replace(usage, browser_actions=usage.browser_actions + 1)
                observation = await self._browser.execute(proposal)
                emit(
                    EventKind.BROWSER_ACTION_COMPLETED,
                    {"observation": dict(observation)},
                )
            except Exception as error:
                return finish(
                    RunState.FAILED,
                    error=f"{type(error).__name__}: {error}",
                )

    @staticmethod
    def _handoff_for(
        kind: ActionKind,
        proposed_handoff: HumanHandoff | None,
        decision: PolicyDecision,
    ) -> HumanHandoff | None:
        if decision.outcome is PolicyOutcome.REQUIRE_HUMAN:
            return decision.handoff
        if kind is ActionKind.HUMAN_HANDOFF:
            return proposed_handoff
        return None
