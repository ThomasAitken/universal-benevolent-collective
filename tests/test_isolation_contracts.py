"""Conformance tests for the isolated-browser foundation."""

import inspect
import json
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints

import pytest

from ubc_node.browser.isolation import isolation_contracts as isolation_module
from ubc_node.browser.isolation.isolation_contracts import (
    BoundaryChannelKind,
    BoundaryEvent,
    ChannelCapability,
    IsolatedBrowserLaunchSpec,
    IsolatedBrowserRuntime,
    IsolationContractError,
    RuntimeLimits,
    WritableArea,
)

_ROOT = Path(__file__).parents[1]
_DIGEST = f"sha256:{'a' * 64}"


def _channels() -> tuple[ChannelCapability, ChannelCapability]:
    return (
        ChannelCapability("control_one", BoundaryChannelKind.CONTROL),
        ChannelCapability("egress_one", BoundaryChannelKind.EGRESS),
    )


def test_launch_spec_normalizes_origins_and_preserves_least_authority() -> None:
    control, egress = _channels()
    spec = IsolatedBrowserLaunchSpec(
        session_id="session-1",
        browser_image_digest=_DIGEST,
        allowed_origins=frozenset({"HTTPS://EXAMPLE.COM:443"}),
        control_channel=control,
        egress_channel=egress,
    )

    assert spec.allowed_origins == frozenset({"https://example.com"})
    assert spec.writable_areas == frozenset(WritableArea)
    assert spec.takeover_enabled is False

    forbidden = {"path", "node_key", "debug", "audit", "socket", "endpoint"}
    field_names = {field.name for field in fields(IsolatedBrowserLaunchSpec)}
    assert all(not any(word in name for word in forbidden) for name in field_names)

    hints = get_type_hints(IsolatedBrowserRuntime.launch)
    assert Path not in hints.values()
    source = inspect.getsource(isolation_module)
    assert "ubc_node.release.node_keys" not in source
    assert "EventSink" not in source


@pytest.mark.parametrize("channel_id", ["", "unsafe/value", "session-ß", "a" * 129])
def test_channel_capability_rejects_unsafe_ids(channel_id: str) -> None:
    with pytest.raises(IsolationContractError, match="unsafe characters"):
        ChannelCapability(channel_id, BoundaryChannelKind.CONTROL)


def test_channel_capability_requires_positive_protocol_version() -> None:
    with pytest.raises(IsolationContractError, match="at least 1"):
        ChannelCapability("control", BoundaryChannelKind.CONTROL, 0)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"memory_bytes": 1}, "memory_bytes"),
        ({"cpu_count": 0}, "cpu_count"),
        ({"cpu_count": 65}, "cpu_count"),
        ({"process_count": 15}, "process_count"),
        ({"wall_time_seconds": 0}, "wall_time_seconds"),
    ],
)
def test_runtime_limits_fail_closed(overrides: dict[str, int], message: str) -> None:
    with pytest.raises(IsolationContractError, match=message):
        RuntimeLimits(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "digest",
    [
        "sha256:short",
        f"sha256:{'z' * 64}",
        f"sha256:{'A' * 64}",
        f"sha256:0x{'a' * 62}",
        f"sha256:{'a' * 31}_{'a' * 32}",
    ],
)
def test_launch_spec_rejects_malformed_digest(digest: str) -> None:
    control, egress = _channels()
    with pytest.raises(IsolationContractError, match="OCI sha256"):
        IsolatedBrowserLaunchSpec(
            "session",
            digest,
            frozenset({"https://example.com"}),
            control,
            egress,
        )


def test_launch_spec_rejects_malformed_origin_and_enabled_takeover() -> None:
    control, egress = _channels()
    with pytest.raises(IsolationContractError):
        IsolatedBrowserLaunchSpec(
            "session",
            _DIGEST,
            frozenset({"not-an-origin"}),
            control,
            egress,
        )
    with pytest.raises(IsolationContractError, match="takeover is disabled"):
        IsolatedBrowserLaunchSpec(
            "session",
            _DIGEST,
            frozenset({"https://example.com"}),
            control,
            egress,
            takeover_enabled=True,
        )


def test_launch_spec_requires_origins_and_correct_distinct_channels() -> None:
    control, egress = _channels()
    with pytest.raises(IsolationContractError, match="allowed origin"):
        IsolatedBrowserLaunchSpec("session", _DIGEST, frozenset(), control, egress)
    with pytest.raises(IsolationContractError, match="control_channel"):
        IsolatedBrowserLaunchSpec(
            "session", _DIGEST, frozenset({"https://example.com"}), egress, egress
        )
    with pytest.raises(IsolationContractError, match="egress_channel"):
        IsolatedBrowserLaunchSpec(
            "session", _DIGEST, frozenset({"https://example.com"}), control, control
        )
    duplicate = ChannelCapability("control_one", BoundaryChannelKind.EGRESS)
    with pytest.raises(IsolationContractError, match="must be distinct"):
        IsolatedBrowserLaunchSpec(
            "session", _DIGEST, frozenset({"https://example.com"}), control, duplicate
        )


def test_launch_spec_requires_exact_named_writable_areas() -> None:
    control, egress = _channels()
    with pytest.raises(IsolationContractError, match="exactly the named"):
        IsolatedBrowserLaunchSpec(
            "session",
            _DIGEST,
            frozenset({"https://example.com"}),
            control,
            egress,
            frozenset({WritableArea.RUNTIME}),
        )


def test_boundary_event_is_immutable_and_validated() -> None:
    payload = {"decision": "allow"}
    event = BoundaryEvent(1, "egress_decided", payload)
    payload["decision"] = "deny"
    assert event.payload == {"decision": "allow"}
    with pytest.raises(TypeError):
        event.payload["decision"] = "deny"  # type: ignore[index]
    with pytest.raises(IsolationContractError, match="positive"):
        BoundaryEvent(0, "event")
    with pytest.raises(IsolationContractError, match="must not be empty"):
        BoundaryEvent(1, " ")


def test_fixture_topology_has_no_browser_host_port_or_privileged_mount() -> None:
    topology = json.loads((_ROOT / "browser-runtime/fixture-topology.json").read_text())
    browser = topology["roles"]["browser"]

    assert topology["version"] == 1
    assert browser["host_ports"] == []
    assert browser["read_only_base"] is True
    assert set(browser["writable_areas"]) == {area.value for area in WritableArea}
    assert topology["networks"]["egress"]["members"] == ["browser", "egress_gate"]


def test_browser_image_has_pinned_inputs_and_no_published_privileged_surface() -> None:
    dockerfile = (_ROOT / "browser-runtime/Dockerfile").read_text()

    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert all("@sha256:" in line for line in from_lines)
    assert "ARG CHROMIUM_VERSION=" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER 65532:65532" in dockerfile
    assert "HOME=/run/ubc" in dockerfile
    assert "TMPDIR=/run/ubc" in dockerfile
    assert "EXPOSE " not in dockerfile
    assert "docker.sock" not in dockerfile
