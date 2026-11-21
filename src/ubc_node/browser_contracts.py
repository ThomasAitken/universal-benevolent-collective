"""UBC-owned browser contracts that deliberately hide browser SDK internals."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit

from ubc_node.contracts import HumanHandoff, JsonValue


def _empty_json_mapping() -> Mapping[str, JsonValue]:
    return {}


class BrowserContractError(ValueError):
    """Raised when a browser request violates the UBC contract."""


class BrowserSessionState(StrEnum):
    """Lifecycle of one browser capability session."""

    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    REVOKED = "revoked"


class BrowserProfileMode(StrEnum):
    """Whether browser state is disposable or explicitly project-persistent."""

    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class InteractionKind(StrEnum):
    """Deterministic interactions UBC permits after observation."""

    CLICK = "click"
    FILL = "fill"


class ExtractionType(StrEnum):
    """Small schema vocabulary accepted at the browser boundary."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class PersistencePolicyDecision:
    """Trusted policy evidence authorizing one project's persistent profile."""

    project_id: str
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise BrowserContractError("persistence project_id must not be empty")
        if not self.reason.strip():
            raise BrowserContractError("persistence policy reason must not be empty")


@dataclass(frozen=True, slots=True)
class BrowserSessionConfig:
    """Policy-bound configuration for one local browser session."""

    workspace: Path
    allowed_origins: frozenset[str]
    profile_mode: BrowserProfileMode = BrowserProfileMode.TEMPORARY
    project_id: str | None = None
    persistence_policy: PersistencePolicyDecision | None = None
    persistent_profile_root: Path | None = None
    headless: bool = True
    max_inspection_chars: int = 20_000
    max_fill_chars: int = 20_000
    max_download_bytes: int = 25 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.allowed_origins:
            raise BrowserContractError("at least one allowed origin is required")
        normalized = frozenset(normalize_origin(origin) for origin in self.allowed_origins)
        object.__setattr__(self, "allowed_origins", normalized)
        object.__setattr__(self, "workspace", self.workspace.resolve())

        for name in ("max_inspection_chars", "max_fill_chars", "max_download_bytes"):
            if getattr(self, name) < 1:
                raise BrowserContractError(f"{name} must be at least 1")

        if self.profile_mode is BrowserProfileMode.TEMPORARY:
            if any(
                value is not None
                for value in (
                    self.project_id,
                    self.persistence_policy,
                    self.persistent_profile_root,
                )
            ):
                raise BrowserContractError(
                    "temporary profiles must not include persistence configuration"
                )
            return

        if self.project_id is None or not self.project_id.strip():
            raise BrowserContractError("persistent profiles require a project_id")
        if not _is_safe_identifier(self.project_id):
            raise BrowserContractError("project_id contains unsafe characters")
        if self.persistent_profile_root is None:
            raise BrowserContractError("persistent profiles require a profile root")
        if self.persistence_policy is None or not self.persistence_policy.allowed:
            raise BrowserContractError("persistent profiles require an allowing policy decision")
        if self.persistence_policy.project_id != self.project_id:
            raise BrowserContractError("persistence policy project does not match the session")
        object.__setattr__(self, "persistent_profile_root", self.persistent_profile_root.resolve())

    @property
    def profile_path(self) -> Path | None:
        """Return the policy-bound persistent path, or None for a temporary profile."""
        if self.profile_mode is BrowserProfileMode.TEMPORARY:
            return None
        assert self.persistent_profile_root is not None
        assert self.project_id is not None
        return self.persistent_profile_root / self.project_id


@dataclass(frozen=True, slots=True)
class PageInspection:
    """Bounded, explicitly untrusted information about the active page."""

    tab_id: str
    url: str
    title: str
    content: str
    content_truncated: bool
    untrusted: bool = True


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A task-workspace artifact without exposing an arbitrary host path."""

    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class InteractionCandidate:
    """An opaque observed interaction; the Stagehand selector remains private."""

    candidate_id: str
    description: str
    kind: InteractionKind


@dataclass(frozen=True, slots=True)
class ExtractionField:
    """One field in a constrained typed extraction schema."""

    name: str
    type: ExtractionType
    required: bool = True

    def __post_init__(self) -> None:
        if not _is_safe_identifier(self.name):
            raise BrowserContractError("extraction field names must be safe identifiers")


@dataclass(frozen=True, slots=True)
class ExtractionSchema:
    """Provider-neutral typed extraction request."""

    fields: tuple[ExtractionField, ...]

    def __post_init__(self) -> None:
        if not self.fields:
            raise BrowserContractError("an extraction schema requires at least one field")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise BrowserContractError("extraction field names must be unique")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Validated data returned by a typed extraction."""

    data: Mapping[str, JsonValue] = field(default_factory=_empty_json_mapping)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class TabInfo:
    """Safe tab metadata using a UBC token rather than a raw page identifier."""

    tab_id: str
    url: str
    title: str
    active: bool


class BrowserSession(Protocol):
    """Typed capability surface available to trusted UBC orchestration code."""

    @property
    def state(self) -> BrowserSessionState: ...

    async def open_url(self, url: str) -> PageInspection: ...

    async def inspect(self) -> PageInspection: ...

    async def screenshot(self, *, full_page: bool = False) -> ArtifactRef: ...

    async def observe(self, instruction: str) -> tuple[InteractionCandidate, ...]: ...

    async def click(self, candidate_id: str) -> PageInspection: ...

    async def fill(self, candidate_id: str, value: str) -> PageInspection: ...

    async def extract(self, instruction: str, schema: ExtractionSchema) -> ExtractionResult: ...

    async def list_tabs(self) -> tuple[TabInfo, ...]: ...

    async def new_tab(self, url: str) -> TabInfo: ...

    async def switch_tab(self, tab_id: str) -> TabInfo: ...

    async def close_tab(self, tab_id: str) -> None: ...

    async def download(self, candidate_id: str, *, timeout_seconds: float = 10) -> ArtifactRef: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def close(self) -> None: ...

    async def revoke(self) -> None: ...

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]: ...


def normalize_origin(value: str) -> str:
    """Normalize an HTTP(S) origin and reject URL-like privilege tricks."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserContractError("only http and https origins are supported")
    if parsed.hostname is None:
        raise BrowserContractError("origin requires a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserContractError("credentials are not allowed in browser URLs")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise BrowserContractError("allowed origins must not contain a path, query, or fragment")
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = "" if port is None or port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def origin_for_url(value: str) -> str:
    """Extract a normalized origin from a navigable URL."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserContractError("only http and https URLs are navigable")
    if parsed.hostname is None:
        raise BrowserContractError("URL requires a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserContractError("credentials are not allowed in browser URLs")
    host = parsed.hostname.lower()
    port = parsed.port
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = "" if port is None or port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _is_safe_identifier(value: str) -> bool:
    return bool(value) and all(
        character.isalnum() or character in {"-", "_"} for character in value
    )
