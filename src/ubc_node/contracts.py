"""Stable contracts for the Phase 0 node runtime."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def _frozen_mapping(values: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(values))


def _empty_json_mapping() -> Mapping[str, JsonValue]:
    return {}


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


class RunState(StrEnum):
    """States in the deliberately small Phase 0 lifecycle."""

    CREATED = "created"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActionKind(StrEnum):
    """Actions an agent backend can propose to the supervisor."""

    BROWSER = "browser"
    HUMAN_HANDOFF = "human_handoff"
    COMPLETE = "complete"


class PolicyOutcome(StrEnum):
    """Possible deterministic policy outcomes for an action proposal."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HUMAN = "require_human"


class EventKind(StrEnum):
    """Events recorded by the Phase 0 supervisor."""

    RUN_CREATED = "run_created"
    STATE_CHANGED = "state_changed"
    ACTION_PROPOSED = "action_proposed"
    POLICY_DECIDED = "policy_decided"
    BROWSER_ACTION_COMPLETED = "browser_action_completed"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_COMPLETED = "handoff_completed"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Hard upper bounds enforced outside the model and browser."""

    max_steps: int = 10
    max_model_calls: int = 10
    max_browser_actions: int = 10
    max_handoffs: int = 2

    def __post_init__(self) -> None:
        for field_name in ("max_steps", "max_model_calls", "max_browser_actions"):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        if self.max_handoffs < 0:
            raise ValueError("max_handoffs must not be negative")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A bounded unit of work accepted by a node supervisor."""

    task_id: str
    objective: str
    budget: RunBudget = field(default_factory=RunBudget)
    metadata: Mapping[str, JsonValue] = field(default_factory=_empty_json_mapping)

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.objective, "objective")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class HumanHandoff:
    """A structured request for a person to temporarily control the browser."""

    reason: str
    origin: str
    instructions: str

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason")
        _require_text(self.origin, "origin")
        _require_text(self.instructions, "instructions")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """A model proposal; it carries no authority until policy approves it."""

    kind: ActionKind
    description: str
    parameters: Mapping[str, JsonValue] = field(default_factory=_empty_json_mapping)
    handoff: HumanHandoff | None = None
    output: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.description, "description")
        object.__setattr__(self, "parameters", _frozen_mapping(self.parameters))
        if self.kind is ActionKind.HUMAN_HANDOFF and self.handoff is None:
            raise ValueError("a human_handoff proposal requires handoff details")
        if self.kind is not ActionKind.HUMAN_HANDOFF and self.handoff is not None:
            raise ValueError("handoff details are only valid for a human_handoff proposal")
        if self.kind is ActionKind.COMPLETE and self.output is None:
            raise ValueError("a complete proposal requires output")
        if self.kind is not ActionKind.COMPLETE and self.output is not None:
            raise ValueError("output is only valid for a complete proposal")

    @classmethod
    def browser(
        cls,
        description: str,
        parameters: Mapping[str, JsonValue] | None = None,
    ) -> Self:
        """Construct a browser action proposal."""
        return cls(ActionKind.BROWSER, description, parameters or {})

    @classmethod
    def request_handoff(cls, description: str, handoff: HumanHandoff) -> Self:
        """Construct a human handoff proposal."""
        return cls(ActionKind.HUMAN_HANDOFF, description, handoff=handoff)

    @classmethod
    def complete(cls, output: str) -> Self:
        """Construct a successful completion proposal."""
        return cls(ActionKind.COMPLETE, "Complete the task", output=output)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """An external authorization decision for one proposed action."""

    outcome: PolicyOutcome
    reason: str
    handoff: HumanHandoff | None = None

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason")
        if self.outcome is PolicyOutcome.REQUIRE_HUMAN and self.handoff is None:
            raise ValueError("require_human decisions need handoff details")
        if self.outcome is not PolicyOutcome.REQUIRE_HUMAN and self.handoff is not None:
            raise ValueError("handoff details are only valid when a human is required")


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One immutable event in a run's append-only history."""

    sequence: int
    run_id: str
    task_id: str
    kind: EventKind
    state: RunState
    occurred_at: datetime
    payload: Mapping[str, JsonValue] = field(default_factory=_empty_json_mapping)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("sequence must be at least 1")
        _require_text(self.run_id, "run_id")
        _require_text(self.task_id, "task_id")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))

    def to_record(self) -> dict[str, JsonValue]:
        """Convert the event to its stable JSON representation."""
        return {
            "sequence": self.sequence,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, JsonValue]) -> Self:
        """Reconstruct an event from a JSONL record."""
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("event payload must be a JSON object")
        return cls(
            sequence=int(str(record["sequence"])),
            run_id=str(record["run_id"]),
            task_id=str(record["task_id"]),
            kind=EventKind(str(record["kind"])),
            state=RunState(str(record["state"])),
            occurred_at=datetime.fromisoformat(str(record["occurred_at"])),
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Counters consumed during a run."""

    steps: int = 0
    model_calls: int = 0
    browser_actions: int = 0
    handoffs: int = 0


@dataclass(frozen=True, slots=True)
class RunResult:
    """Terminal result returned by the supervisor."""

    run_id: str
    task_id: str
    state: RunState
    usage: BudgetUsage
    event_count: int
    output: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        terminal_states = {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
        if self.state not in terminal_states:
            raise ValueError("a run result must have a terminal state")
        if self.event_count < 1:
            raise ValueError("event_count must be at least 1")


class AgentBackend(Protocol):
    """Provider-neutral model boundary."""

    async def propose(
        self,
        task: TaskSpec,
        history: Sequence[RunEvent],
    ) -> ActionProposal:
        """Propose one action without executing it."""
        ...


class BrowserRuntime(Protocol):
    """Browser capability boundary, independent of any browser SDK."""

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
        """Execute one already-authorized browser proposal."""
        ...

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
        """Yield control to a human and return only after control is returned."""
        ...


class PolicyEvaluator(Protocol):
    """Deterministic policy boundary invoked before every action."""

    async def evaluate(self, task: TaskSpec, proposal: ActionProposal) -> PolicyDecision:
        """Authorize, deny, or require a human for the proposal."""
        ...


class EventSink(Protocol):
    """Append-only destination for run events."""

    def append(self, event: RunEvent) -> None:
        """Durably append one event."""
        ...
