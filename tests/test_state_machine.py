"""Tests for the explicit Phase 0 lifecycle."""

import pytest

from ubc_node.contracts import RunState
from ubc_node.state_machine import InvalidTransitionError, RunStateMachine


def test_handoff_lifecycle_can_resume_and_complete() -> None:
    machine = RunStateMachine()
    assert machine.state is RunState.CREATED
    assert machine.transition(RunState.RUNNING) is RunState.CREATED
    assert machine.transition(RunState.AWAITING_HUMAN) is RunState.RUNNING
    assert machine.transition(RunState.RUNNING) is RunState.AWAITING_HUMAN
    assert machine.transition(RunState.COMPLETED) is RunState.RUNNING
    assert machine.state is RunState.COMPLETED


@pytest.mark.parametrize("terminal", [RunState.FAILED, RunState.CANCELLED])
def test_running_can_reach_other_terminal_states(terminal: RunState) -> None:
    machine = RunStateMachine()
    machine.transition(RunState.RUNNING)
    machine.transition(terminal)
    assert machine.state is terminal


@pytest.mark.parametrize("terminal", [RunState.FAILED, RunState.CANCELLED])
def test_handoff_can_fail_or_be_cancelled(terminal: RunState) -> None:
    machine = RunStateMachine()
    machine.transition(RunState.RUNNING)
    machine.transition(RunState.AWAITING_HUMAN)
    machine.transition(terminal)
    assert machine.state is terminal


def test_invalid_transition_is_rejected() -> None:
    machine = RunStateMachine()
    with pytest.raises(InvalidTransitionError, match="created"):
        machine.transition(RunState.COMPLETED)
