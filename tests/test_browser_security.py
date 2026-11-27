"""Tests for the Phase 6 external egress gate and session lease."""

# pyright: reportPrivateUsage=false

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest

from ubc_node.browser.browser_security import (
    EgressGate,
    EgressGateError,
    JsonlBoundaryAudit,
    LeaseConfig,
    SessionLease,
    SessionLeaseError,
)
from ubc_node.core.contracts import JsonValue
from ubc_node.demos.demo_fixture import Phase5FixtureSite


class _Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


def test_lease_config_and_lifecycle() -> None:
    for name in (
        "idle_timeout_seconds",
        "absolute_timeout_seconds",
        "takeover_timeout_seconds",
    ):
        values = {
            "idle_timeout_seconds": 1,
            "absolute_timeout_seconds": 2,
            "takeover_timeout_seconds": 1,
            name: 0,
        }
        with pytest.raises(ValueError, match=name):
            LeaseConfig(**values)
    with pytest.raises(ValueError, match="idle timeout"):
        LeaseConfig(idle_timeout_seconds=3, absolute_timeout_seconds=2)

    clock = _Clock()
    lease = SessionLease(
        LeaseConfig(
            idle_timeout_seconds=2,
            absolute_timeout_seconds=5,
            takeover_timeout_seconds=1,
        ),
        clock=clock,
    )
    assert lease.remaining_seconds == 2
    lease.check()
    clock.value = 11
    lease.touch()
    assert lease.deadline == 13
    lease.begin_takeover()
    assert lease.deadline == 12
    clock.value = 12
    assert lease.remaining_seconds == 0
    with pytest.raises(SessionLeaseError, match="expired"):
        lease.check()

    active = SessionLease(LeaseConfig(), clock=clock)
    assert active.revoke("") is True
    assert active.revoke("again") is False
    with pytest.raises(SessionLeaseError, match="unspecified"):
        active.touch()


def test_boundary_audit_appends_and_resumes_sequence(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    audit = JsonlBoundaryAudit(path)
    with pytest.raises(ValueError, match="kind"):
        audit.append(" ", {})
    audit.append("browser_worker_started", {"session_id": "one"})
    JsonlBoundaryAudit(path).append("browser_worker_exited", {"session_id": "one"})
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["sequence"] for record in records] == [1, 2]
    assert records[0]["kind"] == "browser_worker_started"


async def _proxy_request(endpoint: str, value: bytes) -> bytes:
    parsed = urlsplit(endpoint)
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    writer.write(value)
    await writer.drain()
    if not value.endswith(b"\r\n\r\n") and b"Content-Length" not in value:
        writer.write_eof()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


@pytest.mark.asyncio
async def test_egress_gate_allows_fixture_get_and_post_and_redacts_events() -> None:
    events: list[tuple[str, dict[str, JsonValue]]] = []
    with Phase5FixtureSite() as fixture:
        lease = SessionLease(LeaseConfig())
        gate = EgressGate(
            session_id="session",
            allowed_origins=frozenset({fixture.origin}),
            lease=lease,
            event_sink=lambda kind, payload: events.append((kind, dict(payload))),
        )
        with pytest.raises(EgressGateError, match="not started"):
            _ = gate.endpoint
        await gate.start()
        with pytest.raises(EgressGateError, match="already"):
            await gate.start()

        response = await _proxy_request(
            gate.endpoint,
            f"GET {fixture.origin}/source/alpha?secret=hidden HTTP/1.1\r\n"
            f"Host: {urlsplit(fixture.origin).netloc}\r\n"
            "Proxy-Authorization: secret\r\n\r\n".encode(),
        )
        assert b"200 OK" in response
        assert b"12 hectares" in response

        body = b"username=person&password=secret-canary"
        response = await _proxy_request(
            gate.endpoint,
            f"POST {fixture.origin}/login HTTP/1.1\r\n"
            f"Host: {urlsplit(fixture.origin).netloc}\r\n"
            f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body,
        )
        assert b"303 See Other" in response
        await gate.close()
        await gate.close()

    persisted = json.dumps(events)
    assert "secret-canary" not in persisted
    assert "source/alpha" not in persisted
    assert "hidden" not in persisted
    assert events[-1][1]["origin"] == fixture.origin
    assert events[-1][1]["method"] == "POST"
    assert gate.denial_count == 0
    assert gate.last_denial_reason is None


@pytest.mark.asyncio
async def test_egress_gate_denies_before_contact_and_enforces_method_and_lease() -> None:
    events: list[tuple[str, dict[str, JsonValue]]] = []
    with Phase5FixtureSite() as fixture:
        lease = SessionLease(LeaseConfig())
        gate = EgressGate(
            session_id="session",
            allowed_origins=frozenset({fixture.origin}),
            lease=lease,
            event_sink=lambda kind, payload: events.append((kind, dict(payload))),
        )
        await gate.start()
        denied = await _proxy_request(
            gate.endpoint,
            f"GET {fixture.outside_origin}/ HTTP/1.1\r\nHost: outside\r\n\r\n".encode(),
        )
        assert b"403 Denied" in denied
        assert fixture.outside_request_count == 0

        denied_method = await _proxy_request(
            gate.endpoint,
            f"DELETE {fixture.origin}/ HTTP/1.1\r\nHost: fixture\r\n\r\n".encode(),
        )
        assert b"405 Denied" in denied_method

        lease.revoke("test")
        expired = await _proxy_request(
            gate.endpoint,
            f"GET {fixture.origin}/ HTTP/1.1\r\nHost: fixture\r\n\r\n".encode(),
        )
        assert b"session is not active" in expired
        await gate.close()

    assert gate.denial_count == 3
    assert gate.last_denial_reason == "browser session revoked: test"
    assert all(kind == "egress_decided" for kind, _payload in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_request", "status"),
    [
        (b"garbage\r\n\r\n", b"403 Denied"),
        (b"GET /relative HTTP/1.1\r\nHost: fixture\r\n\r\n", b"403 Denied"),
        (b"CONNECT example.test:443 HTTP/1.1\r\n\r\n", b"403 Denied"),
        (b"GET http://example.test/ HTTP/2\r\n\r\n", b"403 Denied"),
        (b"GET http://example.test/ HTTP/1.1\r\nBroken\r\n\r\n", b"403 Denied"),
        (
            b"POST http://example.test/ HTTP/1.1\r\nContent-Length: 1\r\n"
            b"Content-Length: 1\r\n\r\nx",
            b"403 Denied",
        ),
        (
            b"POST http://example.test/ HTTP/1.1\r\nContent-Length: -1\r\n\r\n",
            b"403 Denied",
        ),
        (b"\xff\r\n\r\n", b"403 Denied"),
        (b"GET http://example.test/ HTTP/1.1", b"400 Denied"),
    ],
)
async def test_egress_gate_fails_closed_for_unsupported_requests(
    raw_request: bytes, status: bytes
) -> None:
    events: list[tuple[str, dict[str, JsonValue]]] = []
    gate = EgressGate(
        session_id="session",
        allowed_origins=frozenset({"http://127.0.0.1:1"}),
        lease=SessionLease(LeaseConfig()),
        event_sink=lambda kind, payload: events.append((kind, dict(payload))),
    )
    await gate.start()
    response = await _proxy_request(gate.endpoint, raw_request)
    await gate.close()
    assert status in response
    assert events[-1][1]["decision"] == "deny"


@pytest.mark.asyncio
async def test_egress_gate_reports_upstream_failure_and_constructor_errors() -> None:
    with pytest.raises(ValueError, match="session_id"):
        EgressGate(
            session_id=" ",
            allowed_origins=frozenset({"http://127.0.0.1:1"}),
            lease=SessionLease(LeaseConfig()),
            event_sink=lambda _kind, _payload: None,
        )
    with pytest.raises(ValueError, match="origin"):
        EgressGate(
            session_id="session",
            allowed_origins=frozenset(),
            lease=SessionLease(LeaseConfig()),
            event_sink=lambda _kind, _payload: None,
        )

    events: list[tuple[str, dict[str, JsonValue]]] = []
    gate = EgressGate(
        session_id="session",
        allowed_origins=frozenset({"http://127.0.0.1:1"}),
        lease=SessionLease(LeaseConfig()),
        event_sink=lambda kind, payload: events.append((kind, dict(payload))),
    )
    await gate.start()
    response = await _proxy_request(
        gate.endpoint,
        b"GET http://127.0.0.1:1/ HTTP/1.1\r\nHost: 127.0.0.1:1\r\n\r\n",
    )
    await gate.close()
    assert b"502 Denied" in response
    assert events[-1][1]["decision"] == "error"


def test_egress_gate_parsing_helpers_reject_body_overflow() -> None:
    with pytest.raises(EgressGateError, match="multiple"):
        EgressGate._content_length([("Content-Length", "1"), ("content-length", "1")])
    with pytest.raises(EgressGateError, match="body"):
        EgressGate._content_length([("Content-Length", str(3 * 1024 * 1024))])
    assert EgressGate._content_length([]) == 0
    assert EgressGate._destination("GET", "http://example.test")[3] == "/"


@pytest.mark.asyncio
async def test_egress_gate_rejects_oversized_headers_and_skips_closed_denial_writer() -> None:
    events: list[tuple[str, dict[str, JsonValue]]] = []
    gate = EgressGate(
        session_id="session",
        allowed_origins=frozenset({"http://fixture.test"}),
        lease=SessionLease(LeaseConfig()),
        event_sink=lambda kind, payload: events.append((kind, dict(payload))),
    )

    class HugeReader:
        async def readuntil(self, _separator: bytes) -> bytes:
            return b"x" * (64 * 1024 + 1)

    class ClosingWriter:
        def is_closing(self) -> bool:
            return True

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    writer = ClosingWriter()
    await gate._handle(cast(Any, HugeReader()), cast(Any, writer))
    await gate._deny(cast(Any, writer), 403, "denied")
    assert events[-1][1]["reason"] == "request headers exceeded the gate limit"
