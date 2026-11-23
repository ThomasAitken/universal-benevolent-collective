"""Stable contracts for the UBC node runtime."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self, runtime_checkable

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class AgentBackendError(RuntimeError):
    """Raised when a model backend cannot produce a valid structured proposal."""


class FatalRunError(RuntimeError):
    """Base error for unrecoverable failures that must terminate a run immediately."""


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
    REQUIRE_APPROVAL = "require_approval"


class ActionRisk(StrEnum):
    """Prototype action classes enforced outside the model."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"


class ActionCapability(StrEnum):
    """Semantic authority requested by an action, independent of browser mechanics."""

    READ = "read"
    NAVIGATE = "navigate"
    CAPTURE = "capture"
    EDIT_DRAFT = "edit_draft"
    MANAGE_TABS = "manage_tabs"
    DOWNLOAD = "download"
    REVERSIBLE_INTERACTION = "reversible_interaction"
    SUBMIT_FORM = "submit_form"
    SEND_MESSAGE = "send_message"
    CHANGE_ACCOUNT = "change_account"
    UPLOAD = "upload"


class ActionControl(StrEnum):
    """Control applied to a granted semantic capability."""

    AUTOMATIC = "automatic"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ApprovalOutcome(StrEnum):
    """A person's decision about one exact consequential proposal."""

    APPROVE = "approve"
    DENY = "deny"


class EventKind(StrEnum):
    """Events recorded by the node supervisor."""

    RUN_CREATED = "run_created"
    STATE_CHANGED = "state_changed"
    BROWSER_STATE_OBSERVED = "browser_state_observed"
    ACTION_PROPOSED = "action_proposed"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    BROWSER_ACTION_STARTED = "browser_action_started"
    BROWSER_ACTION_COMPLETED = "browser_action_completed"
    STEP_FAILED = "step_failed"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_COMPLETED = "handoff_completed"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Token and estimated cost accounting for one model call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.input_tokens < 0:
            raise ValueError("input_tokens must not be negative")
        if self.output_tokens < 0:
            raise ValueError("output_tokens must not be negative")
        if self.total_tokens < 0:
            raise ValueError("total_tokens must not be negative")
        minimum_total = self.input_tokens + self.output_tokens
        if self.total_tokens < minimum_total:
            object.__setattr__(self, "total_tokens", minimum_total)
        if not math.isfinite(self.estimated_cost_usd) or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be a non-negative finite number")

    def to_record(self) -> dict[str, JsonValue]:
        """Convert usage accounting to a JSON-compatible dictionary."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Hard upper bounds enforced outside the model and browser."""

    max_steps: int = 10
    max_model_calls: int = 10
    max_browser_actions: int = 10
    max_handoffs: int = 2
    max_tokens: int = 100_000
    max_estimated_cost_usd: float = 1.0
    max_duration_seconds: float = 300.0
    max_downloads: int = 5
    max_consecutive_failures: int = 3

    def __post_init__(self) -> None:
        for field_name in (
            "max_steps",
            "max_model_calls",
            "max_browser_actions",
            "max_tokens",
            "max_consecutive_failures",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        for field_name in ("max_handoffs", "max_downloads"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must not be negative")
        for field_name in ("max_estimated_cost_usd", "max_duration_seconds"):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be a positive finite number")


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
    usage: ModelUsage | None = None

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
        *,
        usage: ModelUsage | None = None,
    ) -> Self:
        """Construct a browser action proposal."""
        return cls(ActionKind.BROWSER, description, parameters or {}, usage=usage)

    @classmethod
    def request_handoff(
        cls,
        description: str,
        handoff: HumanHandoff,
        *,
        usage: ModelUsage | None = None,
    ) -> Self:
        """Construct a human handoff proposal."""
        return cls(ActionKind.HUMAN_HANDOFF, description, handoff=handoff, usage=usage)

    @classmethod
    def complete(cls, output: str, *, usage: ModelUsage | None = None) -> Self:
        """Construct a successful completion proposal."""
        return cls(ActionKind.COMPLETE, "Complete the task", output=output, usage=usage)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """An external authorization decision for one proposed action."""

    outcome: PolicyOutcome
    reason: str
    handoff: HumanHandoff | None = None
    risk: ActionRisk | None = None
    rule_id: str | None = None
    capability: ActionCapability | None = None
    control: ActionControl | None = None

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason")
        if self.outcome is PolicyOutcome.REQUIRE_HUMAN and self.handoff is None:
            raise ValueError("require_human decisions need handoff details")
        if self.outcome is not PolicyOutcome.REQUIRE_HUMAN and self.handoff is not None:
            raise ValueError("handoff details are only valid when a human is required")
        if self.outcome is PolicyOutcome.REQUIRE_APPROVAL and self.risk is None:
            raise ValueError("require_approval decisions must classify the action risk")
        if self.rule_id is not None:
            _require_text(self.rule_id, "rule_id")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Human authorization result for one proposal; it carries no reusable grant."""

    outcome: ApprovalOutcome
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.reason, "reason")


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
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    downloads: int = 0
    consecutive_failures: int = 0


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


@runtime_checkable
class BrowserStateObserver(Protocol):
    """Optional capability for observing current browser state before proposing."""

    async def observe_state(self) -> Mapping[str, JsonValue]:
        """Return a bounded, JSON-compatible snapshot of current browser state."""
        ...


@runtime_checkable
class TaskAwareHandoffRuntime(Protocol):
    """Optional handoff capability that receives the requesting task context."""

    async def handoff_for_task(
        self,
        task: TaskSpec,
        request: HumanHandoff,
    ) -> Mapping[str, JsonValue]:
        """Yield control to a human with task context and return redacted status."""
        ...


class PolicyEvaluator(Protocol):
    """Deterministic policy boundary invoked before every action."""

    async def evaluate(self, task: TaskSpec, proposal: ActionProposal) -> PolicyDecision:
        """Authorize, deny, or require a human for the proposal."""
        ...


class ActionApprover(Protocol):
    """Human approval boundary for a single policy-classified proposal."""

    async def approve(
        self,
        action_id: str,
        task: TaskSpec,
        proposal: ActionProposal,
        policy_decision: PolicyDecision,
    ) -> ApprovalDecision:
        """Approve or deny the exact proposal identified by ``action_id``."""
        ...


class EventSink(Protocol):
    """Append-only destination for run events."""

    def append(self, event: RunEvent) -> None:
        """Durably append one event."""
        ...
