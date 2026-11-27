"""Tests for the fail-closed Phase 4 prototype policy."""

from collections.abc import Mapping
from typing import cast

import pytest

from ubc_node.browser.browser_contracts import BrowserContractError
from ubc_node.core.contracts import (
    ActionCapability,
    ActionControl,
    ActionProposal,
    ActionRisk,
    HumanHandoff,
    JsonValue,
    PolicyOutcome,
    TaskSpec,
)
from ubc_node.core.policy import (
    PrototypePolicyConfig,
    PrototypePolicyEvaluator,
    audit_safe_parameters,
)

TASK = TaskSpec("task-policy", "Research approved sources")


def _policy(
    *,
    controls: Mapping[ActionCapability, ActionControl] | None = None,
) -> PrototypePolicyEvaluator:
    return PrototypePolicyEvaluator(
        PrototypePolicyConfig(
            frozenset({"https://EXAMPLE.test:443"}),
            controls or {},
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposal", "risk", "capability"),
    [
        (
            ActionProposal.browser("Open", {"operation": "open", "url": "https://example.test/a"}),
            ActionRisk.R0,
            ActionCapability.NAVIGATE,
        ),
        (
            ActionProposal.browser(
                "New tab",
                {"operation": "new_tab", "url": "https://example.test/b"},
            ),
            ActionRisk.R0,
            ActionCapability.NAVIGATE,
        ),
        (
            ActionProposal.browser("Inspect", {"operation": "inspect"}),
            ActionRisk.R0,
            ActionCapability.READ,
        ),
        (
            ActionProposal.browser("Download", {"operation": "download"}),
            ActionRisk.R1,
            ActionCapability.DOWNLOAD,
        ),
        (
            ActionProposal.browser(
                "Follow link",
                {
                    "operation": "click",
                    "candidate_id": "link-1",
                    "interaction_effect": "navigation",
                },
            ),
            ActionRisk.R0,
            ActionCapability.NAVIGATE,
        ),
        (
            ActionProposal.browser(
                "Toggle details",
                {
                    "operation": "click",
                    "candidate_id": "toggle-1",
                    "interaction_effect": "reversible_local",
                },
            ),
            ActionRisk.R1,
            ActionCapability.REVERSIBLE_INTERACTION,
        ),
    ],
)
async def test_allowed_actions_are_classified(
    proposal: ActionProposal,
    risk: ActionRisk,
    capability: ActionCapability,
) -> None:
    decision = await _policy().evaluate(TASK, proposal)

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.risk is risk
    assert decision.capability is capability
    assert decision.control is ActionControl.AUTOMATIC
    assert decision.rule_id == f"phase4.capability.{capability.value}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposal", "rule_id"),
    [
        (ActionProposal.complete("done"), "phase4.complete"),
        (
            ActionProposal.request_handoff(
                "Login",
                HumanHandoff("login", "https://example.test", "take control"),
            ),
            "phase4.handoff",
        ),
        (
            ActionProposal.browser(
                "Resume",
                {"operation": "resume_after_handoff", "origin": "https://example.test"},
            ),
            "phase4.resume",
        ),
    ],
)
async def test_supervisor_lifecycle_actions_remain_separately_classified(
    proposal: ActionProposal,
    rule_id: str,
) -> None:
    decision = await _policy().evaluate(TASK, proposal)
    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.risk is ActionRisk.R0
    assert decision.rule_id == rule_id
    assert decision.capability is None


@pytest.mark.asyncio
async def test_r2_click_requires_exact_one_time_approval() -> None:
    proposal = ActionProposal.browser(
        "Submit form",
        {
            "operation": "click",
            "candidate_id": "candidate-1",
            "interaction_effect": "submission",
        },
    )

    decision = await _policy().evaluate(TASK, proposal)

    assert decision.outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decision.risk is ActionRisk.R2
    assert decision.capability is ActionCapability.SUBMIT_FORM
    assert decision.control is ActionControl.REQUIRE_APPROVAL
    assert decision.rule_id == "phase4.capability.submit_form"


@pytest.mark.asyncio
async def test_capability_control_can_preapprove_r2_or_tighten_r0() -> None:
    submit = ActionProposal.browser(
        "Submit bounded search",
        {
            "operation": "click",
            "candidate_id": "submit-1",
            "interaction_effect": "submission",
        },
    )
    inspect = ActionProposal.browser("Inspect", {"operation": "inspect"})

    preapproved = await _policy(
        controls={ActionCapability.SUBMIT_FORM: ActionControl.AUTOMATIC}
    ).evaluate(TASK, submit)
    denied = await _policy(controls={ActionCapability.READ: ActionControl.DENY}).evaluate(
        TASK, inspect
    )

    assert preapproved.outcome is PolicyOutcome.ALLOW
    assert preapproved.risk is ActionRisk.R2
    assert preapproved.control is ActionControl.AUTOMATIC
    assert denied.outcome is PolicyOutcome.DENY
    assert denied.risk is ActionRisk.R0
    assert denied.control is ActionControl.DENY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposal", "rule_id"),
    [
        (ActionProposal.browser("Missing", {}), "phase4.schema"),
        (ActionProposal.browser("Missing", {"operation": 3}), "phase4.schema"),
        (
            ActionProposal.browser("Ambiguous click", {"operation": "click"}),
            "phase4.effect_required",
        ),
        (
            ActionProposal.browser(
                "Missing candidate",
                {"operation": "click", "interaction_effect": "navigation"},
            ),
            "phase4.schema",
        ),
        (
            ActionProposal.browser(
                "Misplaced effect",
                {"operation": "inspect", "interaction_effect": "navigation"},
            ),
            "phase4.schema",
        ),
        (ActionProposal.browser("Pay", {"operation": "payment"}), "phase4.prohibited_operation"),
        (
            ActionProposal.browser(
                "Create account",
                {"operation": "click", "interaction_effect": "account_creation"},
            ),
            "phase4.prohibited_effect",
        ),
        (
            ActionProposal.browser(
                "Unknown effect",
                {"operation": "click", "interaction_effect": "surprise"},
            ),
            "phase4.schema",
        ),
        (
            ActionProposal.browser(
                "Bad effect type",
                {"operation": "click", "interaction_effect": True},
            ),
            "phase4.schema",
        ),
        (
            ActionProposal.browser(
                "Other origin",
                {"operation": "open", "url": "https://outside.test/"},
            ),
            "phase4.destination",
        ),
        (
            ActionProposal.browser("Bad URL", {"operation": "open", "url": "not-a-url"}),
            "phase4.destination",
        ),
        (
            ActionProposal.browser(
                "Bad URL type",
                {"operation": "new_tab", "url": False},
            ),
            "phase4.destination",
        ),
        (
            ActionProposal.browser(
                "Bad resume",
                {"operation": "resume_after_handoff", "origin": "https://outside.test"},
            ),
            "phase4.destination",
        ),
        (
            ActionProposal.browser(
                "Missing resume origin",
                {"operation": "resume_after_handoff"},
            ),
            "phase4.destination",
        ),
        (ActionProposal.browser("Raw JS", {"operation": "evaluate"}), "phase4.unsupported"),
    ],
)
async def test_disallowed_and_invalid_actions_fail_closed(
    proposal: ActionProposal,
    rule_id: str,
) -> None:
    decision = await _policy().evaluate(TASK, proposal)

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.rule_id == rule_id


@pytest.mark.asyncio
async def test_out_of_scope_handoffs_are_denied() -> None:
    handoff = ActionProposal.request_handoff(
        "Login elsewhere",
        HumanHandoff("login", "https://outside.test", "take control"),
    )
    invalid_handoff = ActionProposal.request_handoff(
        "Bad origin",
        HumanHandoff("login", "not-an-origin", "take control"),
    )
    assert (await _policy().evaluate(TASK, handoff)).outcome is PolicyOutcome.DENY
    assert (await _policy().evaluate(TASK, invalid_handoff)).outcome is PolicyOutcome.DENY


def test_policy_config_normalizes_and_validates_capabilities() -> None:
    config = PrototypePolicyConfig(frozenset({"https://EXAMPLE.test:443"}))
    assert config.allowed_origins == frozenset({"https://example.test"})
    assert config.capability_controls[ActionCapability.DOWNLOAD] is ActionControl.AUTOMATIC
    assert (
        config.capability_controls[ActionCapability.SEND_MESSAGE] is ActionControl.REQUIRE_APPROVAL
    )

    bad_key = cast(
        Mapping[ActionCapability, ActionControl],
        {"not-a-capability": ActionControl.AUTOMATIC},
    )
    with pytest.raises(ValueError, match="keys"):
        PrototypePolicyConfig(frozenset(), bad_key)
    bad_control = cast(
        Mapping[ActionCapability, ActionControl],
        {ActionCapability.READ: "sometimes"},
    )
    with pytest.raises(ValueError, match="ActionControl"):
        PrototypePolicyConfig(frozenset(), bad_control)
    with pytest.raises(BrowserContractError):
        PrototypePolicyConfig(frozenset({"file:///tmp"}))


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        (
            {
                "operation": "fill",
                "value": "secret",
                "url": "https://user:pass@example.test/path?token=secret#fragment",
            },
            {
                "operation": "fill",
                "value": "[REDACTED]",
                "url": "https://example.test/path",
            },
        ),
        ({"url": "file:///secret"}, {"url": "[REDACTED_URL]"}),
        ({"url": "https://example.test:bad/"}, {"url": "[REDACTED_URL]"}),
        ({"origin": "https://example.test:443/"}, {"origin": "https://example.test/"}),
    ],
)
def test_audit_safe_parameters_redacts_values_and_url_secrets(
    parameters: Mapping[str, JsonValue],
    expected: Mapping[str, JsonValue],
) -> None:
    assert audit_safe_parameters(parameters) == expected
