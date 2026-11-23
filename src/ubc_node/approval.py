"""Local one-shot approval for Phase 4 consequential browser actions."""

import asyncio
import json
from collections.abc import Callable

from ubc_node.contracts import (
    ActionProposal,
    ApprovalDecision,
    ApprovalOutcome,
    PolicyDecision,
    TaskSpec,
)
from ubc_node.policy import audit_safe_parameters


class CliActionApprover:
    """Show one exact redacted proposal and require an explicit human decision."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._input = input_fn
        self._output = output_fn

    async def approve(
        self,
        action_id: str,
        task: TaskSpec,
        proposal: ActionProposal,
        policy_decision: PolicyDecision,
    ) -> ApprovalDecision:
        """Approve only this action ID; the result cannot authorize later actions."""
        parameters = json.dumps(
            audit_safe_parameters(proposal.parameters),
            ensure_ascii=False,
            sort_keys=True,
        )
        self._output(
            "\n".join(
                [
                    "",
                    "=" * 64,
                    "ONE-TIME ACTION APPROVAL REQUIRED",
                    f"Action:       {action_id}",
                    f"Task:         {task.task_id} — {task.objective}",
                    f"Risk:         {policy_decision.risk or 'unclassified'}",
                    f"Capability:   {policy_decision.capability or 'unclassified'}",
                    f"Description:  {proposal.description}",
                    f"Parameters:   {parameters}",
                    f"Policy:       {policy_decision.reason}",
                    "=" * 64,
                    "Approval applies once to exactly the action shown above.",
                ]
            )
        )
        while True:
            raw = await asyncio.to_thread(self._input, "Action decision [approve/deny]: ")
            choice = raw.strip().lower()
            if choice in {"approve", "a"}:
                return ApprovalDecision(ApprovalOutcome.APPROVE, "approved by local operator")
            if choice in {"deny", "d"}:
                return ApprovalDecision(ApprovalOutcome.DENY, "denied by local operator")
            self._output("Please explicitly enter 'approve' or 'deny'.")
