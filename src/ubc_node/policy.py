"""Fail-closed prototype action policy and audit-safe action rendering."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from ubc_node.browser_contracts import BrowserContractError, normalize_origin, origin_for_url
from ubc_node.contracts import (
    ActionCapability,
    ActionControl,
    ActionKind,
    ActionProposal,
    ActionRisk,
    JsonValue,
    PolicyDecision,
    PolicyOutcome,
    TaskSpec,
)

_PROHIBITED_OPERATIONS = frozenset({"payment", "create_account", "public_post", "recovery_change"})
_PROHIBITED_EFFECTS = frozenset({"payment", "account_creation", "public_post", "recovery_change"})
_KNOWN_EFFECTS = frozenset(
    {
        "navigation",
        "reversible_local",
        "submission",
        "message",
        "account_change",
        "upload",
        *_PROHIBITED_EFFECTS,
    }
)


_OPERATION_CAPABILITIES: Mapping[str, tuple[ActionCapability, ActionRisk]] = MappingProxyType(
    {
        "open": (ActionCapability.NAVIGATE, ActionRisk.R0),
        "inspect": (ActionCapability.READ, ActionRisk.R0),
        "observe": (ActionCapability.READ, ActionRisk.R0),
        "extract": (ActionCapability.READ, ActionRisk.R0),
        "tabs": (ActionCapability.READ, ActionRisk.R0),
        "new_tab": (ActionCapability.NAVIGATE, ActionRisk.R0),
        "switch_tab": (ActionCapability.MANAGE_TABS, ActionRisk.R1),
        "screenshot": (ActionCapability.CAPTURE, ActionRisk.R1),
        "fill": (ActionCapability.EDIT_DRAFT, ActionRisk.R1),
        "close_tab": (ActionCapability.MANAGE_TABS, ActionRisk.R1),
        "download": (ActionCapability.DOWNLOAD, ActionRisk.R1),
    }
)
_EFFECT_CAPABILITIES: Mapping[str, tuple[ActionCapability, ActionRisk]] = MappingProxyType(
    {
        "navigation": (ActionCapability.NAVIGATE, ActionRisk.R0),
        "reversible_local": (ActionCapability.REVERSIBLE_INTERACTION, ActionRisk.R1),
        "submission": (ActionCapability.SUBMIT_FORM, ActionRisk.R2),
        "message": (ActionCapability.SEND_MESSAGE, ActionRisk.R2),
        "account_change": (ActionCapability.CHANGE_ACCOUNT, ActionRisk.R2),
        "upload": (ActionCapability.UPLOAD, ActionRisk.R2),
    }
)


def _default_capability_controls() -> dict[ActionCapability, ActionControl]:
    automatic = {
        ActionCapability.READ,
        ActionCapability.NAVIGATE,
        ActionCapability.CAPTURE,
        ActionCapability.EDIT_DRAFT,
        ActionCapability.MANAGE_TABS,
        ActionCapability.DOWNLOAD,
        ActionCapability.REVERSIBLE_INTERACTION,
    }
    approval = {
        ActionCapability.SUBMIT_FORM,
        ActionCapability.SEND_MESSAGE,
        ActionCapability.CHANGE_ACCOUNT,
        ActionCapability.UPLOAD,
    }
    return {
        **{capability: ActionControl.AUTOMATIC for capability in automatic},
        **{capability: ActionControl.REQUIRE_APPROVAL for capability in approval},
    }


def _empty_capability_controls() -> Mapping[ActionCapability, ActionControl]:
    return {}


@dataclass(frozen=True, slots=True)
class PrototypePolicyConfig:
    """Task-local Phase 4 policy, intentionally smaller than future network policy."""

    allowed_origins: frozenset[str]
    capability_controls: Mapping[ActionCapability, ActionControl] = field(
        default_factory=_empty_capability_controls
    )

    def __post_init__(self) -> None:
        normalized = frozenset(normalize_origin(origin) for origin in self.allowed_origins)
        controls = _default_capability_controls()
        supplied = cast(Mapping[object, object], self.capability_controls)
        for capability, control in supplied.items():
            if not isinstance(capability, ActionCapability):
                raise ValueError("capability control keys must be ActionCapability values")
            if not isinstance(control, ActionControl):
                raise ValueError("capability controls must be ActionControl values")
            controls[capability] = control
        object.__setattr__(self, "allowed_origins", normalized)
        object.__setattr__(
            self,
            "capability_controls",
            MappingProxyType(controls),
        )


class PrototypePolicyEvaluator:
    """Classify the current browser surface as R0/R1/R2 and fail closed."""

    def __init__(self, config: PrototypePolicyConfig) -> None:
        self._config = config

    async def evaluate(self, task: TaskSpec, proposal: ActionProposal) -> PolicyDecision:
        """Evaluate one proposal without relying on its free-form description."""
        del task
        if proposal.kind is ActionKind.COMPLETE:
            return self._allow(ActionRisk.R0, "phase4.complete", "task completion is local")
        if proposal.kind is ActionKind.HUMAN_HANDOFF:
            assert proposal.handoff is not None
            if not self._origin_allowed(proposal.handoff.origin):
                return self._deny("phase4.destination", "handoff origin is outside task scope")
            return self._allow(
                ActionRisk.R0,
                "phase4.handoff",
                "human takeover is allowed at an approved origin",
            )

        operation = proposal.parameters.get("operation")
        if not isinstance(operation, str) or not operation.strip():
            return self._deny("phase4.schema", "browser operation is missing")
        operation = operation.strip()

        if operation in _PROHIBITED_OPERATIONS:
            return self._deny(
                "phase4.prohibited_operation",
                f"{operation} is prohibited during the prototype",
            )

        effect = proposal.parameters.get("interaction_effect")
        if effect is not None:
            if not isinstance(effect, str) or effect not in _KNOWN_EFFECTS:
                return self._deny("phase4.schema", "interaction_effect is invalid")
            if effect in _PROHIBITED_EFFECTS:
                return self._deny(
                    "phase4.prohibited_effect",
                    f"{effect} is prohibited during the prototype",
                )
            if operation != "click":
                return self._deny(
                    "phase4.schema",
                    "interaction_effect is only valid for click operations",
                )

        if operation == "resume_after_handoff":
            origin = proposal.parameters.get("origin")
            if not isinstance(origin, str) or not self._origin_allowed(origin):
                return self._deny(
                    "phase4.destination",
                    "resume origin is outside task scope",
                )
            return self._allow(
                ActionRisk.R0,
                "phase4.resume",
                "resuming at the approved origin is allowed",
            )

        if operation in {"open", "new_tab"}:
            url = proposal.parameters.get("url")
            if not isinstance(url, str) or not self._url_allowed(url):
                return self._deny(
                    "phase4.destination",
                    "navigation destination is outside task scope",
                )

        classification = _OPERATION_CAPABILITIES.get(operation)
        if operation == "click":
            if not isinstance(effect, str):
                return self._deny(
                    "phase4.effect_required",
                    "click requires an explicit interaction_effect",
                )
            candidate_id = proposal.parameters.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                return self._deny(
                    "phase4.schema",
                    "click requires a current candidate_id",
                )
            classification = _EFFECT_CAPABILITIES.get(effect)
        if classification is not None:
            capability, risk = classification
            return self._decision_for(capability, risk)
        return self._deny(
            "phase4.unsupported",
            f"unsupported browser operation: {operation}",
        )

    def _url_allowed(self, url: str) -> bool:
        try:
            return origin_for_url(url) in self._config.allowed_origins
        except BrowserContractError:
            return False

    def _origin_allowed(self, origin: str) -> bool:
        try:
            return normalize_origin(origin) in self._config.allowed_origins
        except BrowserContractError:
            return False

    def _decision_for(
        self,
        capability: ActionCapability,
        risk: ActionRisk,
    ) -> PolicyDecision:
        control = self._config.capability_controls[capability]
        rule_id = f"phase4.capability.{capability.value}"
        if control is ActionControl.AUTOMATIC:
            return PolicyDecision(
                PolicyOutcome.ALLOW,
                f"{capability.value} capability is authorized automatically",
                risk=risk,
                rule_id=rule_id,
                capability=capability,
                control=control,
            )
        if control is ActionControl.REQUIRE_APPROVAL:
            return PolicyDecision(
                PolicyOutcome.REQUIRE_APPROVAL,
                f"{capability.value} capability requires one-time human approval",
                risk=risk,
                rule_id=rule_id,
                capability=capability,
                control=control,
            )
        return PolicyDecision(
            PolicyOutcome.DENY,
            f"{capability.value} capability is denied for this task",
            risk=risk,
            rule_id=rule_id,
            capability=capability,
            control=control,
        )

    @staticmethod
    def _allow(risk: ActionRisk, rule_id: str, reason: str) -> PolicyDecision:
        return PolicyDecision(
            PolicyOutcome.ALLOW,
            reason,
            risk=risk,
            rule_id=rule_id,
        )

    @staticmethod
    def _deny(
        rule_id: str,
        reason: str,
        *,
        risk: ActionRisk | None = None,
    ) -> PolicyDecision:
        return PolicyDecision(
            PolicyOutcome.DENY,
            reason,
            risk=risk,
            rule_id=rule_id,
        )


def audit_safe_parameters(parameters: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return useful action metadata without retaining fill values or URL secrets."""
    safe: dict[str, JsonValue] = {}
    for key, value in parameters.items():
        if key == "value":
            safe[key] = "[REDACTED]"
        elif key in {"url", "origin"} and isinstance(value, str):
            safe[key] = _audit_safe_url(value)
        else:
            safe[key] = value
    return safe


def _audit_safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            return "[REDACTED_URL]"
        port = parsed.port
        host = parsed.hostname.lower()
        default_port = 80 if parsed.scheme == "http" else 443
        netloc = host if port is None or port == default_port else f"{host}:{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"
