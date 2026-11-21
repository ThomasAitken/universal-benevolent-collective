"""Deterministic test doubles for the Phase 0 contracts."""

from collections import deque
from collections.abc import Callable, Mapping, Sequence

from ubc_node.contracts import (
    ActionProposal,
    HumanHandoff,
    JsonValue,
    PolicyDecision,
    PolicyOutcome,
    RunEvent,
    TaskSpec,
)


class ScriptedAgentBackend:
    """Return a fixed sequence of proposals without calling a model."""

    def __init__(
        self,
        proposals: Sequence[ActionProposal],
        *,
        on_propose: Callable[[int], None] | None = None,
    ) -> None:
        self._proposals = deque(proposals)
        self._on_propose = on_propose
        self.calls = 0

    async def propose(
        self,
        task: TaskSpec,
        history: Sequence[RunEvent],
    ) -> ActionProposal:
        """Return the next scripted proposal."""
        del task, history
        self.calls += 1
        if self._on_propose is not None:
            self._on_propose(self.calls)
        if not self._proposals:
            raise RuntimeError("scripted agent has no proposal remaining")
        return self._proposals.popleft()


class FakeBrowserRuntime:
    """Record browser calls and return fixed, JSON-compatible observations."""

    def __init__(
        self,
        *,
        action_results: Sequence[Mapping[str, JsonValue]] = (),
        handoff_results: Sequence[Mapping[str, JsonValue]] = (),
        on_execute: Callable[[int], None] | None = None,
        on_handoff: Callable[[int], None] | None = None,
    ) -> None:
        self._action_results = deque(dict(result) for result in action_results)
        self._handoff_results = deque(dict(result) for result in handoff_results)
        self._on_execute = on_execute
        self._on_handoff = on_handoff
        self.actions: list[ActionProposal] = []
        self.handoffs: list[HumanHandoff] = []

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
        """Record an authorized action and return its scripted observation."""
        self.actions.append(proposal)
        if self._on_execute is not None:
            self._on_execute(len(self.actions))
        if self._action_results:
            return self._action_results.popleft()
        return {"status": "ok"}

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
        """Record a handoff and immediately simulate return of control."""
        self.handoffs.append(request)
        if self._on_handoff is not None:
            self._on_handoff(len(self.handoffs))
        if self._handoff_results:
            return self._handoff_results.popleft()
        return {"status": "returned"}


class ScriptedPolicyEvaluator:
    """Return scripted policy decisions, then a configurable default."""

    def __init__(
        self,
        decisions: Sequence[PolicyDecision] = (),
        *,
        default: PolicyDecision | None = None,
    ) -> None:
        self._decisions = deque(decisions)
        self._default = default or PolicyDecision(PolicyOutcome.ALLOW, "allowed by fake policy")
        self.proposals: list[ActionProposal] = []

    async def evaluate(self, task: TaskSpec, proposal: ActionProposal) -> PolicyDecision:
        """Record a proposal and return the next decision."""
        del task
        self.proposals.append(proposal)
        if self._decisions:
            return self._decisions.popleft()
        return self._default


class MemoryEventSink:
    """Collect events in memory while preserving append order."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def append(self, event: RunEvent) -> None:
        """Append one event to the public test history."""
        self.events.append(event)
