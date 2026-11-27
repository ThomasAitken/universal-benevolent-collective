"""Contracts shared by the trusted node and an isolated browser runtime.

The values in this module are capabilities and boundary-local names.  They do
not expose host filesystem paths, container-engine access, Chromium debugging
addresses, node keys, or an append handle for the core audit journal.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from ubc_node.browser.browser_contracts import (
    BrowserContractError,
    BrowserSession,
    normalize_origin,
)
from ubc_node.core.contracts import JsonValue

_HEX_DIGITS = frozenset("0123456789abcdef")
_MAX_IDENTIFIER_LENGTH = 128


def _empty_payload() -> Mapping[str, JsonValue]:
    return {}


class IsolationContractError(ValueError):
    """Raised when an isolated-runtime request is unsafe or malformed."""


class WritableArea(StrEnum):
    """Named writable areas provisioned inside the browser boundary."""

    RUNTIME = "runtime"
    PROFILE = "profile"
    QUARANTINE = "quarantine"


class BoundaryChannelKind(StrEnum):
    """Authenticated channels available to the isolated runtime."""

    CONTROL = "control"
    EGRESS = "egress"


@dataclass(frozen=True, slots=True)
class ChannelCapability:
    """Opaque authority for one already-provisioned boundary channel."""

    channel_id: str
    kind: BoundaryChannelKind
    protocol_version: int = 1

    def __post_init__(self) -> None:
        _require_identifier(self.channel_id, "channel_id")
        if self.protocol_version < 1:
            raise IsolationContractError("protocol_version must be at least 1")


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """Hard limits that the isolation backend must apply outside the worker."""

    memory_bytes: int = 1024 * 1024 * 1024
    cpu_count: float = 1.0
    process_count: int = 256
    wall_time_seconds: int = 1800

    def __post_init__(self) -> None:
        if self.memory_bytes < 128 * 1024 * 1024:
            raise IsolationContractError("memory_bytes must be at least 128 MiB")
        if not 0 < self.cpu_count <= 64:
            raise IsolationContractError("cpu_count must be greater than 0 and at most 64")
        if self.process_count < 16:
            raise IsolationContractError("process_count must be at least 16")
        if self.wall_time_seconds < 1:
            raise IsolationContractError("wall_time_seconds must be at least 1")


@dataclass(frozen=True, slots=True)
class IsolatedBrowserLaunchSpec:
    """Serializable least-authority request for an isolated browser session."""

    session_id: str
    browser_image_digest: str
    allowed_origins: frozenset[str]
    control_channel: ChannelCapability
    egress_channel: ChannelCapability
    writable_areas: frozenset[WritableArea] = field(default_factory=lambda: frozenset(WritableArea))
    limits: RuntimeLimits = field(default_factory=RuntimeLimits)
    takeover_enabled: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.session_id, "session_id")
        _require_sha256(self.browser_image_digest, "browser_image_digest")
        if not self.allowed_origins:
            raise IsolationContractError("at least one allowed origin is required")
        try:
            normalized_origins = frozenset(
                normalize_origin(origin) for origin in self.allowed_origins
            )
        except BrowserContractError as error:
            raise IsolationContractError(str(error)) from error
        object.__setattr__(self, "allowed_origins", normalized_origins)
        if self.control_channel.kind is not BoundaryChannelKind.CONTROL:
            raise IsolationContractError("control_channel has the wrong channel kind")
        if self.egress_channel.kind is not BoundaryChannelKind.EGRESS:
            raise IsolationContractError("egress_channel has the wrong channel kind")
        if self.control_channel.channel_id == self.egress_channel.channel_id:
            raise IsolationContractError("control and egress channels must be distinct")
        if self.writable_areas != frozenset(WritableArea):
            raise IsolationContractError("the runtime requires exactly the named writable areas")
        if self.takeover_enabled:
            raise IsolationContractError("takeover is disabled in the isolated launch contract")


@dataclass(frozen=True, slots=True)
class BoundaryEvent:
    """Untrusted boundary fact for the trusted audit writer to wrap and append."""

    sequence: int
    kind: str
    payload: Mapping[str, JsonValue] = field(default_factory=_empty_payload)

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise IsolationContractError("boundary event sequence must be positive")
        if not self.kind.strip():
            raise IsolationContractError("boundary event kind must not be empty")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class IsolatedBrowserRuntime(Protocol):
    """Trusted-side launcher that preserves the existing typed browser surface."""

    async def launch(self, spec: IsolatedBrowserLaunchSpec) -> BrowserSession:
        """Launch one isolated session or fail without returning partial authority."""
        ...

    async def next_event(self) -> BoundaryEvent:
        """Receive a boundary fact; this does not grant core audit append authority."""
        ...


def _require_identifier(value: str, name: str) -> None:
    if (
        not value
        or len(value) > _MAX_IDENTIFIER_LENGTH
        or not all(
            character.isascii() and (character.isalnum() or character in {"-", "_"})
            for character in value
        )
    ):
        raise IsolationContractError(
            f"{name} contains unsafe characters or exceeds {_MAX_IDENTIFIER_LENGTH} characters"
        )


def _require_sha256(value: str, name: str) -> None:
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or len(digest) != 64
        or any(character not in _HEX_DIGITS for character in digest)
    ):
        raise IsolationContractError(f"{name} must be an OCI sha256 digest")
