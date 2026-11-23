"""Tests for local one-time Phase 4 approval."""

import pytest

from ubc_node.approval import CliActionApprover
from ubc_node.contracts import (
    ActionCapability,
    ActionControl,
    ActionProposal,
    ActionRisk,
    ApprovalOutcome,
    PolicyDecision,
    PolicyOutcome,
    TaskSpec,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "outcome", "reason"),
    [
        (["invalid", "a"], ApprovalOutcome.APPROVE, "approved by local operator"),
        (["D"], ApprovalOutcome.DENY, "denied by local operator"),
    ],
)
async def test_cli_approver_requires_explicit_choice_and_redacts_fill_values(
    responses: list[str],
    outcome: ApprovalOutcome,
    reason: str,
) -> None:
    pending = iter(responses)
    output: list[str] = []
    approver = CliActionApprover(
        input_fn=lambda _prompt: next(pending),
        output_fn=output.append,
    )
    proposal = ActionProposal.browser(
        "Fill consequential form",
        {"operation": "fill", "value": "never-log-me"},
    )
    policy = PolicyDecision(
        PolicyOutcome.REQUIRE_APPROVAL,
        "approval needed",
        risk=ActionRisk.R2,
        rule_id="test.r2",
        capability=ActionCapability.SUBMIT_FORM,
        control=ActionControl.REQUIRE_APPROVAL,
    )

    decision = await approver.approve(
        "action-1",
        TaskSpec("task-1", "Test approval"),
        proposal,
        policy,
    )

    assert decision.outcome is outcome
    assert decision.reason == reason
    rendered = "\n".join(output)
    assert "[REDACTED]" in rendered
    assert "submit_form" in rendered
    assert "never-log-me" not in rendered
    if len(responses) > 1:
        assert "Please explicitly enter" in rendered


@pytest.mark.asyncio
async def test_cli_approver_renders_unclassified_policy_without_error() -> None:
    output: list[str] = []
    approver = CliActionApprover(input_fn=lambda _prompt: "deny", output_fn=output.append)

    await approver.approve(
        "action-2",
        TaskSpec("task-2", "Legacy policy"),
        ActionProposal.browser("Click", {"operation": "click"}),
        PolicyDecision(PolicyOutcome.ALLOW, "legacy decision"),
    )

    assert "unclassified" in output[0]
