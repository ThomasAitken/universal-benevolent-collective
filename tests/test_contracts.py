"""Tests for the Phase 0 runtime contracts."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from ubc_node.contracts import (
    ActionKind,
    ActionProposal,
    BudgetUsage,
    EventKind,
    HumanHandoff,
    JsonValue,
    ModelUsage,
    PolicyDecision,
    PolicyOutcome,
    RunBudget,
    RunEvent,
    RunResult,
    RunState,
    TaskSpec,
)

NOW = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)
HANDOFF = HumanHandoff(
    reason="Sign-in required",
    origin="https://example.test",
    instructions="Sign in, then return control.",
)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_steps", 0),
        ("max_model_calls", 0),
        ("max_browser_actions", 0),
        ("max_handoffs", -1),
        ("max_tokens", 0),
        ("max_estimated_cost_usd", 0.0),
        ("max_estimated_cost_usd", float("inf")),
        ("max_duration_seconds", 0.0),
        ("max_duration_seconds", float("nan")),
        ("max_downloads", -1),
        ("max_consecutive_failures", 0),
    ],
)
def test_run_budget_rejects_invalid_limits(field_name: str, value: int | float) -> None:
    arguments: dict[str, int | float] = {
        "max_steps": 1,
        "max_model_calls": 1,
        "max_browser_actions": 1,
        "max_handoffs": 0,
        "max_tokens": 1,
        "max_estimated_cost_usd": 0.5,
        "max_duration_seconds": 10.0,
        "max_downloads": 0,
        "max_consecutive_failures": 1,
    }
    arguments[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        RunBudget(**arguments)  # pyright: ignore[reportArgumentType]


def test_model_usage_computes_minimum_total_and_validates_fields() -> None:
    computed = ModelUsage(input_tokens=10, output_tokens=5, estimated_cost_usd=0.001)
    assert computed.total_tokens == 15
    assert computed.to_record() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "estimated_cost_usd": 0.001,
    }

    explicit = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=25)
    assert explicit.total_tokens == 25

    with pytest.raises(ValueError, match="input_tokens"):
        ModelUsage(input_tokens=-1)
    with pytest.raises(ValueError, match="output_tokens"):
        ModelUsage(output_tokens=-1)
    with pytest.raises(ValueError, match="total_tokens"):
        ModelUsage(total_tokens=-1)
    with pytest.raises(ValueError, match="estimated_cost_usd"):
        ModelUsage(estimated_cost_usd=-0.01)
    with pytest.raises(ValueError, match="estimated_cost_usd"):
        ModelUsage(estimated_cost_usd=float("inf"))


def test_task_spec_validates_text_and_freezes_metadata() -> None:
    task = TaskSpec("task-1", "Research the example", metadata={"source": "test"})
    assert task.metadata == {"source": "test"}

    with pytest.raises(TypeError):
        cast(dict[str, JsonValue], task.metadata)["source"] = "changed"
    with pytest.raises(ValueError, match="task_id"):
        TaskSpec(" ", "objective")
    with pytest.raises(ValueError, match="objective"):
        TaskSpec("task-1", " ")


@pytest.mark.parametrize("field_name", ["reason", "origin", "instructions"])
def test_handoff_requires_all_text_fields(field_name: str) -> None:
    values = {
        "reason": "reason",
        "origin": "https://example.test",
        "instructions": "instructions",
    }
    values[field_name] = " "
    with pytest.raises(ValueError, match=field_name):
        HumanHandoff(**values)


def test_action_proposal_constructors_and_frozen_parameters() -> None:
    browser = ActionProposal.browser("Open the page", {"url": "https://example.test"})
    handoff = ActionProposal.request_handoff("Ask the owner to sign in", HANDOFF)
    complete = ActionProposal.complete("Finished")

    assert browser.kind is ActionKind.BROWSER
    assert browser.parameters == {"url": "https://example.test"}
    assert handoff.handoff is HANDOFF
    assert complete.output == "Finished"
    with pytest.raises(TypeError):
        cast(dict[str, JsonValue], browser.parameters)["url"] = "changed"


@pytest.mark.parametrize(
    "proposal",
    [
        pytest.param(
            lambda: ActionProposal(ActionKind.BROWSER, " "),
            id="empty-description",
        ),
        pytest.param(
            lambda: ActionProposal(ActionKind.HUMAN_HANDOFF, "handoff"),
            id="missing-handoff",
        ),
        pytest.param(
            lambda: ActionProposal(ActionKind.BROWSER, "browse", handoff=HANDOFF),
            id="unexpected-handoff",
        ),
        pytest.param(
            lambda: ActionProposal(ActionKind.COMPLETE, "complete"),
            id="missing-output",
        ),
        pytest.param(
            lambda: ActionProposal(ActionKind.BROWSER, "browse", output="result"),
            id="unexpected-output",
        ),
    ],
)
def test_action_proposal_rejects_inconsistent_fields(proposal: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        proposal()


def test_policy_decision_validates_handoff_semantics() -> None:
    required = PolicyDecision(PolicyOutcome.REQUIRE_HUMAN, "owner approval", HANDOFF)
    assert required.handoff is HANDOFF

    with pytest.raises(ValueError, match="reason"):
        PolicyDecision(PolicyOutcome.ALLOW, " ")
    with pytest.raises(ValueError, match="need handoff details"):
        PolicyDecision(PolicyOutcome.REQUIRE_HUMAN, "owner approval")
    with pytest.raises(ValueError, match="only valid"):
        PolicyDecision(PolicyOutcome.ALLOW, "allowed", HANDOFF)


def test_run_event_round_trips_through_json_record() -> None:
    event = RunEvent(
        sequence=1,
        run_id="run-1",
        task_id="task-1",
        kind=EventKind.RUN_CREATED,
        state=RunState.CREATED,
        occurred_at=NOW,
        payload={"nested": {"ok": True}},
    )
    restored = RunEvent.from_record(event.to_record())

    assert restored == event
    with pytest.raises(TypeError):
        cast(dict[str, JsonValue], event.payload)["changed"] = True


@pytest.mark.parametrize(
    "event",
    [
        pytest.param(
            lambda: RunEvent(0, "run-1", "task-1", EventKind.RUN_CREATED, RunState.CREATED, NOW),
            id="sequence",
        ),
        pytest.param(
            lambda: RunEvent(1, " ", "task-1", EventKind.RUN_CREATED, RunState.CREATED, NOW),
            id="run-id",
        ),
        pytest.param(
            lambda: RunEvent(1, "run-1", " ", EventKind.RUN_CREATED, RunState.CREATED, NOW),
            id="task-id",
        ),
        pytest.param(
            lambda: RunEvent(
                1,
                "run-1",
                "task-1",
                EventKind.RUN_CREATED,
                RunState.CREATED,
                datetime(2026, 11, 21),
            ),
            id="timezone",
        ),
    ],
)
def test_run_event_rejects_invalid_fields(event: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        event()


def test_run_event_rejects_non_object_payload_record() -> None:
    record = RunEvent(
        1, "run-1", "task-1", EventKind.RUN_CREATED, RunState.CREATED, NOW
    ).to_record()
    record["payload"] = "not-an-object"

    with pytest.raises(ValueError, match="payload"):
        RunEvent.from_record(record)


def test_run_result_requires_terminal_state_and_positive_event_count() -> None:
    result = RunResult("run-1", "task-1", RunState.COMPLETED, BudgetUsage(), 1, output="ok")
    assert result.output == "ok"

    with pytest.raises(ValueError, match="terminal"):
        RunResult("run-1", "task-1", RunState.RUNNING, BudgetUsage(), 1)
    with pytest.raises(ValueError, match="event_count"):
        RunResult("run-1", "task-1", RunState.FAILED, BudgetUsage(), 0)
