"""External browser egress policy and revocable session leases."""

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import cast
from urllib.parse import urlsplit

from ubc_node.browser.browser_contracts import BrowserContractError, origin_for_url
from ubc_node.core.contracts import JsonValue

type BoundaryEventSink = Callable[[str, Mapping[str, JsonValue]], None]
type Clock = Callable[[], float]

_MAX_HEADER_BYTES = 64 * 1024
_MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024


class SessionLeaseError(RuntimeError):
    """Raised when a browser session lease no longer grants authority."""


class EgressGateError(RuntimeError):
    """Raised when the external browser egress gate cannot operate safely."""


@dataclass(frozen=True, slots=True)
class LeaseConfig:
    """Monotonic lifetime limits for one supervised browser session."""

    idle_timeout_seconds: float = 300
    absolute_timeout_seconds: float = 1_800
    takeover_timeout_seconds: float = 300

    def __post_init__(self) -> None:
        for name in (
            "idle_timeout_seconds",
            "absolute_timeout_seconds",
            "takeover_timeout_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.idle_timeout_seconds > self.absolute_timeout_seconds:
            raise ValueError("idle timeout cannot exceed absolute timeout")


class SessionLease:
    """Short-lived authority shared by command and egress boundaries."""

    def __init__(self, config: LeaseConfig, *, clock: Clock = monotonic) -> None:
        self._config = config
        self._clock = clock
        now = clock()
        self._absolute_deadline = now + config.absolute_timeout_seconds
        self._idle_deadline = min(now + config.idle_timeout_seconds, self._absolute_deadline)
        self._revoked_reason: str | None = None

    @property
    def deadline(self) -> float:
        """Return the next effective monotonic deadline."""
        return min(self._idle_deadline, self._absolute_deadline)

    @property
    def remaining_seconds(self) -> float:
        """Return non-negative time remaining without changing lease state."""
        return max(0.0, self.deadline - self._clock())

    def check(self) -> None:
        """Fail when revoked or expired."""
        if self._revoked_reason is not None:
            raise SessionLeaseError(f"browser session revoked: {self._revoked_reason}")
        if self._clock() >= self.deadline:
            raise SessionLeaseError("browser session expired")

    def touch(self) -> None:
        """Refresh the idle deadline after an accepted parent command."""
        self.check()
        self._idle_deadline = min(
            self._clock() + self._config.idle_timeout_seconds,
            self._absolute_deadline,
        )

    def begin_takeover(self) -> None:
        """Give human takeover a fixed interval that network traffic cannot refresh."""
        self.check()
        self._idle_deadline = min(
            self._clock() + self._config.takeover_timeout_seconds,
            self._absolute_deadline,
        )

    def revoke(self, reason: str) -> bool:
        """Revoke idempotently and return whether this call changed state."""
        if self._revoked_reason is not None:
            return False
        self._revoked_reason = reason.strip() or "unspecified"
        return True


class JsonlBoundaryAudit:
    """Append bounded browser-boundary facts to a dedicated local journal."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._sequence = 0
        if self.path.exists():
            self._sequence = len(self.path.read_text(encoding="utf-8").splitlines())

    def append(self, kind: str, payload: Mapping[str, JsonValue]) -> None:
        if not kind.strip():
            raise ValueError("boundary event kind must not be empty")
        self._sequence += 1
        record = {
            "sequence": self._sequence,
            "occurred_at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "payload": dict(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, allow_nan=False, separators=(",", ":"), sort_keys=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{line}\n")
            stream.flush()
            os.fsync(stream.fileno())


class EgressGate:
    """Minimal HTTP forward proxy that decides before opening an upstream socket."""

    def __init__(
        self,
        *,
        session_id: str,
        allowed_origins: frozenset[str],
        lease: SessionLease,
        event_sink: BoundaryEventSink,
        allowed_methods: frozenset[str] = frozenset({"GET", "HEAD", "POST"}),
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not allowed_origins:
            raise ValueError("egress gate requires at least one allowed origin")
        self._session_id = session_id
        self._allowed_origins = allowed_origins
        self._lease = lease
        self._event_sink = event_sink
        self._allowed_methods = frozenset(method.upper() for method in allowed_methods)
        self._server: asyncio.Server | None = None
        self._denial_count = 0
        self._last_denial_reason: str | None = None

    @property
    def denial_count(self) -> int:
        """Return the number of policy or protocol denials observed so far."""
        return self._denial_count

    @property
    def last_denial_reason(self) -> str | None:
        return self._last_denial_reason

    @property
    def endpoint(self) -> str:
        """Return the loopback proxy endpoint after start."""
        if self._server is None or not self._server.sockets:
            raise EgressGateError("egress gate has not started")
        port = cast(tuple[str, int], self._server.sockets[0].getsockname())[1]
        return f"http://127.0.0.1:{port}"

    async def start(self) -> None:
        if self._server is not None:
            raise EgressGateError("egress gate is already started")
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            request_head = await reader.readuntil(b"\r\n\r\n")
            if len(request_head) > _MAX_HEADER_BYTES:
                raise EgressGateError("request headers exceeded the gate limit")
            method, target, version, headers = self._parse_request(request_head)
            origin, host, port, upstream_target = self._destination(method, target)
            try:
                self._lease.check()
            except SessionLeaseError as error:
                self._record_decision("deny", origin, method, str(error))
                await self._deny(writer, 403, "browser session is not active")
                return
            if method not in self._allowed_methods:
                self._record_decision("deny", origin, method, "HTTP method is not allowed")
                await self._deny(writer, 405, "HTTP method is not allowed")
                return
            if origin not in self._allowed_origins:
                self._record_decision("deny", origin, method, "origin is not approved")
                await self._deny(writer, 403, "origin is not approved")
                return
            length = self._content_length(headers)
            body = await reader.readexactly(length) if length else b""
            self._record_decision("allow", origin, method, "origin and method approved")
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
            forwarded = self._forward_head(method, upstream_target, version, headers)
            upstream_writer.write(forwarded + body)
            await upstream_writer.drain()
            while chunk := await upstream_reader.read(64 * 1024):
                writer.write(chunk)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError, ValueError):
            self._record_decision("deny", None, None, "malformed proxy request")
            await self._deny(writer, 400, "malformed proxy request")
        except (BrowserContractError, EgressGateError) as error:
            self._record_decision("deny", None, None, str(error))
            await self._deny(writer, 403, str(error))
        except OSError:
            self._record_decision("error", None, None, "upstream connection failed")
            await self._deny(writer, 502, "upstream connection failed")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            writer.close()
            await writer.wait_closed()

    @staticmethod
    def _parse_request(
        value: bytes,
    ) -> tuple[str, str, str, list[tuple[str, str]]]:
        lines = value.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            raise EgressGateError("malformed proxy request line")
        method, target, version = parts
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise EgressGateError("unsupported HTTP version")
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line:
                continue
            name, separator, raw_value = line.partition(":")
            if not separator or not name.strip():
                raise EgressGateError("malformed proxy header")
            headers.append((name.strip(), raw_value.strip()))
        return method.upper(), target, version, headers

    @staticmethod
    def _destination(method: str, target: str) -> tuple[str, str, int, str]:
        if method == "CONNECT":
            raise EgressGateError("HTTPS CONNECT is not supported by this prototype gate")
        parsed = urlsplit(target)
        if parsed.scheme != "http" or parsed.hostname is None:
            raise EgressGateError("the prototype gate accepts absolute HTTP URLs only")
        origin = origin_for_url(target)
        port = parsed.port or 80
        upstream_target = parsed.path or "/"
        if parsed.query:
            upstream_target = f"{upstream_target}?{parsed.query}"
        return origin, parsed.hostname, port, upstream_target

    @staticmethod
    def _content_length(headers: list[tuple[str, str]]) -> int:
        values = [value for name, value in headers if name.lower() == "content-length"]
        if len(values) > 1:
            raise EgressGateError("multiple content-length headers are not supported")
        if not values:
            return 0
        length = int(values[0])
        if length < 0 or length > _MAX_REQUEST_BODY_BYTES:
            raise EgressGateError("request body exceeded the gate limit")
        return length

    @staticmethod
    def _forward_head(
        method: str,
        target: str,
        version: str,
        headers: list[tuple[str, str]],
    ) -> bytes:
        blocked = {"connection", "proxy-authorization", "proxy-connection"}
        forwarded = [f"{method} {target} {version}"]
        forwarded.extend(
            f"{name}: {value}" for name, value in headers if name.lower() not in blocked
        )
        forwarded.append("Connection: close")
        return ("\r\n".join(forwarded) + "\r\n\r\n").encode("iso-8859-1")

    async def _deny(self, writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        if writer.is_closing():
            return
        body = reason.encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} Denied\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()

    def _record_decision(
        self,
        decision: str,
        origin: str | None,
        method: str | None,
        reason: str,
    ) -> None:
        if decision == "deny":
            self._denial_count += 1
            self._last_denial_reason = reason
        payload: dict[str, JsonValue] = {
            "session_id": self._session_id,
            "decision": decision,
            "rule_id": "browser_egress_v1",
            "reason": reason,
        }
        if origin is not None:
            payload["origin"] = origin
        if method is not None:
            payload["method"] = method
        self._event_sink("egress_decided", payload)
