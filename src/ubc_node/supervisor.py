"""Bounded node supervisor built entirely against contracts."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from ubc_node.contracts import (
    ActionApprover,
    ActionKind,
    ActionProposal,
    AgentBackend,
    ApprovalOutcome,
    BrowserRuntime,
    BrowserStateObserver,
    BudgetUsage,
    EventKind,
    EventSink,
    FatalRunError,
    HumanHandoff,
    JsonValue,
    PolicyDecision,
    PolicyEvaluator,
    PolicyOutcome,
    RunEvent,
    RunResult,
    RunState,
    TaskAwareHandoffRuntime,
    TaskSpec,
)
from ubc_node.policy import audit_safe_parameters
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
        approver: ActionApprover | None = None,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._agent = agent
        self._browser = browser
        self._policy = policy
        self._event_sink = event_sink
        self._approver = approver
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
        started_at = self._clock()
        machine = RunStateMachine()
        history: list[RunEvent] = []
        usage: BudgetUsage = BudgetUsage()

        def elapsed_seconds() -> float:
            return max(0.0, (self._clock() - started_at).total_seconds())

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
            nonlocal usage
            usage = replace(usage, duration_seconds=elapsed_seconds())
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
                "max_tokens": task.budget.max_tokens,
                "max_estimated_cost_usd": task.budget.max_estimated_cost_usd,
                "max_duration_seconds": task.budget.max_duration_seconds,
                "max_downloads": task.budget.max_downloads,
                "max_consecutive_failures": task.budget.max_consecutive_failures,
            },
        )
        transition(RunState.RUNNING)

        try:
            async with asyncio.timeout(task.budget.max_duration_seconds):
                while True:
                    if token.cancelled:
                        return finish(RunState.CANCELLED, error="cancellation requested")
                    if elapsed_seconds() >= task.budget.max_duration_seconds:
                        return finish(RunState.FAILED, error="duration budget exhausted")
                    if usage.steps >= task.budget.max_steps:
                        return finish(RunState.FAILED, error="step budget exhausted")
                    if usage.model_calls >= task.budget.max_model_calls:
                        return finish(RunState.FAILED, error="model-call budget exhausted")
                    if usage.total_tokens >= task.budget.max_tokens:
                        return finish(RunState.FAILED, error="token budget exhausted")
                    if usage.estimated_cost_usd >= task.budget.max_estimated_cost_usd:
                        return finish(RunState.FAILED, error="cost budget exhausted")

                    usage = replace(usage, steps=usage.steps + 1)
                    action_id: str | None = None
                    try:
                        if isinstance(self._browser, BrowserStateObserver):
                            browser_state = await self._browser.observe_state()
                            emit(
                                EventKind.BROWSER_STATE_OBSERVED,
                                {"browser": dict(browser_state)},
                            )
                            if token.cancelled:
                                return finish(RunState.CANCELLED, error="cancellation requested")
                            if elapsed_seconds() >= task.budget.max_duration_seconds:
                                return finish(RunState.FAILED, error="duration budget exhausted")

                        usage = replace(usage, model_calls=usage.model_calls + 1)
                        proposal = await self._agent.propose(task, tuple(history))
                        action_id = f"{run_id}:action:{usage.model_calls}"
                        proposal_payload: dict[str, JsonValue] = {
                            "action_id": action_id,
                            "kind": proposal.kind.value,
                            "description": proposal.description,
                            "parameters": audit_safe_parameters(proposal.parameters),
                        }
                        if proposal.usage is not None:
                            usage = replace(
                                usage,
                                input_tokens=usage.input_tokens + proposal.usage.input_tokens,
                                output_tokens=usage.output_tokens + proposal.usage.output_tokens,
                                total_tokens=usage.total_tokens + proposal.usage.total_tokens,
                                estimated_cost_usd=round(
                                    usage.estimated_cost_usd + proposal.usage.estimated_cost_usd,
                                    8,
                                ),
                            )
                            proposal_payload["usage"] = proposal.usage.to_record()
                        emit(EventKind.ACTION_PROPOSED, proposal_payload)
                        if token.cancelled:
                            return finish(RunState.CANCELLED, error="cancellation requested")
                        if elapsed_seconds() >= task.budget.max_duration_seconds:
                            return finish(RunState.FAILED, error="duration budget exhausted")
                        if usage.total_tokens > task.budget.max_tokens:
                            return finish(RunState.FAILED, error="token budget exhausted")
                        if usage.estimated_cost_usd > task.budget.max_estimated_cost_usd:
                            return finish(RunState.FAILED, error="cost budget exhausted")

                        decision = await self._policy.evaluate(task, proposal)
                        decision_payload: dict[str, JsonValue] = {
                            "action_id": action_id,
                            "outcome": decision.outcome.value,
                            "reason": decision.reason,
                        }
                        if decision.risk is not None:
                            decision_payload["risk"] = decision.risk.value
                        if decision.rule_id is not None:
                            decision_payload["rule_id"] = decision.rule_id
                        if decision.capability is not None:
                            decision_payload["capability"] = decision.capability.value
                        if decision.control is not None:
                            decision_payload["control"] = decision.control.value
                        emit(EventKind.POLICY_DECIDED, decision_payload)
                        if token.cancelled:
                            return finish(RunState.CANCELLED, error="cancellation requested")
                        if decision.outcome is PolicyOutcome.DENY:
                            return finish(
                                RunState.FAILED, error=f"policy denied action: {decision.reason}"
                            )
                        if decision.outcome is PolicyOutcome.REQUIRE_APPROVAL:
                            assert decision.risk is not None
                            emit(
                                EventKind.APPROVAL_REQUESTED,
                                {
                                    "action_id": action_id,
                                    "risk": decision.risk.value,
                                    "rule_id": decision.rule_id,
                                    "capability": (
                                        decision.capability.value
                                        if decision.capability is not None
                                        else None
                                    ),
                                    "control": (
                                        decision.control.value
                                        if decision.control is not None
                                        else None
                                    ),
                                    "description": proposal.description,
                                    "parameters": audit_safe_parameters(proposal.parameters),
                                },
                            )
                            if self._approver is None:
                                approval_reason = "no human approver is configured"
                                emit(
                                    EventKind.APPROVAL_DECIDED,
                                    {
                                        "action_id": action_id,
                                        "outcome": ApprovalOutcome.DENY.value,
                                        "reason": approval_reason,
                                    },
                                )
                                return finish(
                                    RunState.FAILED,
                                    error=f"human approval denied action: {approval_reason}",
                                )
                            try:
                                approval = await self._approver.approve(
                                    action_id,
                                    task,
                                    proposal,
                                    decision,
                                )
                            except Exception as error:
                                emit(
                                    EventKind.APPROVAL_DECIDED,
                                    {
                                        "action_id": action_id,
                                        "outcome": "error",
                                        "reason": f"approval handler failed: {type(error).__name__}",
                                    },
                                )
                                return finish(
                                    RunState.FAILED,
                                    error=f"approval failed: {type(error).__name__}: {error}",
                                )
                            emit(
                                EventKind.APPROVAL_DECIDED,
                                {
                                    "action_id": action_id,
                                    "outcome": approval.outcome.value,
                                    "reason": approval.reason,
                                },
                            )
                            if token.cancelled:
                                return finish(RunState.CANCELLED, error="cancellation requested")
                            if elapsed_seconds() >= task.budget.max_duration_seconds:
                                return finish(RunState.FAILED, error="duration budget exhausted")
                            if approval.outcome is ApprovalOutcome.DENY:
                                return finish(
                                    RunState.FAILED,
                                    error=f"human approval denied action: {approval.reason}",
                                )

                        handoff = self._handoff_for(proposal.kind, proposal.handoff, decision)
                        if handoff is not None:
                            if usage.handoffs >= task.budget.max_handoffs:
                                return finish(
                                    RunState.FAILED, error="human-handoff budget exhausted"
                                )
                            usage = replace(usage, handoffs=usage.handoffs + 1)
                            transition(RunState.AWAITING_HUMAN)
                            emit(
                                EventKind.HANDOFF_REQUESTED,
                                {
                                    "action_id": action_id,
                                    "reason": handoff.reason,
                                    "origin": handoff.origin,
                                    "instructions": handoff.instructions,
                                },
                            )
                            browser = self._browser
                            if isinstance(browser, TaskAwareHandoffRuntime):
                                observation = await browser.handoff_for_task(task, handoff)
                            else:
                                observation = await self._browser.handoff(handoff)
                            usage = replace(usage, consecutive_failures=0)
                            emit(
                                EventKind.HANDOFF_COMPLETED,
                                {"action_id": action_id, "observation": dict(observation)},
                            )
                            if observation.get("control_returned") is False:
                                return finish(
                                    RunState.CANCELLED, error="human declined takeover control"
                                )
                            if token.cancelled:
                                return finish(RunState.CANCELLED, error="cancellation requested")
                            if elapsed_seconds() >= task.budget.max_duration_seconds:
                                return finish(RunState.FAILED, error="duration budget exhausted")
                            resume_origin = observation.get("origin")
                            resolved_origin = (
                                resume_origin
                                if isinstance(resume_origin, str) and resume_origin.strip()
                                else handoff.origin
                            )
                            resume_decision = await self._policy.evaluate(
                                task,
                                ActionProposal.browser(
                                    "Resume after human takeover",
                                    {
                                        "operation": "resume_after_handoff",
                                        "origin": resolved_origin,
                                    },
                                ),
                            )
                            emit(
                                EventKind.POLICY_DECIDED,
                                {
                                    "action_id": action_id,
                                    "outcome": resume_decision.outcome.value,
                                    "reason": resume_decision.reason,
                                    "check": "resume_after_handoff",
                                },
                            )
                            if resume_decision.outcome is not PolicyOutcome.ALLOW:
                                return finish(
                                    RunState.FAILED,
                                    error=(
                                        f"policy denied resume after handoff: "
                                        f"{resume_decision.reason}"
                                    ),
                                )
                            transition(RunState.RUNNING)
                            continue

                        if proposal.kind is ActionKind.COMPLETE:
                            usage = replace(usage, consecutive_failures=0)
                            return finish(RunState.COMPLETED, output=proposal.output)

                        if usage.browser_actions >= task.budget.max_browser_actions:
                            return finish(RunState.FAILED, error="browser-action budget exhausted")
                        is_download = proposal.parameters.get("operation") == "download"
                        if is_download and usage.downloads >= task.budget.max_downloads:
                            return finish(RunState.FAILED, error="download budget exhausted")
                        usage = replace(
                            usage,
                            browser_actions=usage.browser_actions + 1,
                            downloads=usage.downloads + (1 if is_download else 0),
                        )
                        emit(
                            EventKind.BROWSER_ACTION_STARTED,
                            {
                                "action_id": action_id,
                                "operation": proposal.parameters.get("operation"),
                                "parameters": audit_safe_parameters(proposal.parameters),
                            },
                        )
                        observation = await self._browser.execute(proposal)
                        usage = replace(usage, consecutive_failures=0)
                        emit(
                            EventKind.BROWSER_ACTION_COMPLETED,
                            {
                                "action_id": action_id,
                                "operation": proposal.parameters.get("operation"),
                                "observation": dict(observation),
                            },
                        )
                    except FatalRunError as error:
                        return finish(
                            RunState.FAILED,
                            error=f"{type(error).__name__}: {error}",
                        )
                    except Exception as error:
                        error_text = f"{type(error).__name__}: {error}"
                        if machine.state is RunState.AWAITING_HUMAN:
                            return finish(RunState.FAILED, error=error_text)
                        usage = replace(usage, consecutive_failures=usage.consecutive_failures + 1)
                        emit(
                            EventKind.STEP_FAILED,
                            (
                                {
                                    "error": error_text,
                                    "consecutive_failures": usage.consecutive_failures,
                                }
                                | ({"action_id": action_id} if action_id is not None else {})
                            ),
                        )
                        if usage.consecutive_failures >= task.budget.max_consecutive_failures:
                            return finish(
                                RunState.FAILED,
                                error=f"consecutive-failure budget exhausted: {error_text}",
                            )
        except TimeoutError:
            return finish(RunState.FAILED, error="duration budget exhausted")
        except asyncio.CancelledError:
            finish(RunState.CANCELLED, error="run interrupted")
            raise

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
