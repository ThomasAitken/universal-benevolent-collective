"""Explicit lifecycle transitions for one bounded agent run."""

from ubc_node.contracts import RunState


class InvalidTransitionError(ValueError):
    """Raised when code attempts a transition outside the Phase 0 lifecycle."""


_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.RUNNING}),
    RunState.RUNNING: frozenset(
        {
            RunState.AWAITING_HUMAN,
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
        }
    ),
    RunState.AWAITING_HUMAN: frozenset({RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


class RunStateMachine:
    """A minimal state holder that rejects implicit or invalid transitions."""

    def __init__(self) -> None:
        self._state = RunState.CREATED

    @property
    def state(self) -> RunState:
        """Return the current state."""
        return self._state

    def transition(self, target: RunState) -> RunState:
        """Move to an allowed target and return the prior state."""
        previous = self._state
        if target not in _ALLOWED_TRANSITIONS[previous]:
            raise InvalidTransitionError(f"cannot transition from {previous} to {target}")
        self._state = target
        return previous
