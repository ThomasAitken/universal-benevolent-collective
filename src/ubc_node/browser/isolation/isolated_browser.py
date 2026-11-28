"""Trusted client for the Linux browser boundary."""

import asyncio
import re
import socket
import struct
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import cast

from ubc_node.browser.browser_contracts import BrowserSessionState
from ubc_node.browser.isolation.isolation_contracts import (
    BoundaryEvent,
    IsolatedBrowserLaunchSpec,
    RuntimeLimits,
)
from ubc_node.browser.isolation.snapshot import SnapshotContent, encode_snapshot, parse_snapshot
from ubc_node.browser.stagehand_browser import BrowserSessionError, NavigationPolicyError
from ubc_node.browser.supervised_browser import (
    WORKER_PROTOCOL_VERSION,
    BrowserWorkerClient,
    BrowserWorkerError,
    read_worker_frame,
    write_worker_frame,
)
from ubc_node.core.contracts import FatalRunError, JsonValue
from ubc_node.release.release_manifest import load_release_manifest, verify_release_manifest

ISOLATED_OPERATIONS = frozenset(
    {
        "state",
        "observe_state",
        "open",
        "inspect",
        "tabs",
        "new_tab",
        "switch_tab",
        "close_tab",
        "pause",
        "resume",
        "close",
        "revoke",
    }
)
_SNAPSHOT_OPERATIONS = frozenset({"render_snapshot"})
_DEFAULT_SOCKET = Path("/run/ubc-browser/control.sock")
_START_TIMEOUT = 60
_COMMAND_TIMEOUT = 30
_PEER_CREDENTIALS = struct.Struct("iII")


class IsolatedBrowserError(BrowserWorkerError, FatalRunError):
    """The isolated session cannot be used again."""


def _authenticate_broker(writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("socket")
    peer_credential_option = cast(int, getattr(socket, "SO_PEERCRED", 17))
    credentials = peer.getsockopt(socket.SOL_SOCKET, peer_credential_option, _PEER_CREDENTIALS.size)
    _pid, uid, _gid = _PEER_CREDENTIALS.unpack(credentials)
    if uid != 0:
        raise IsolatedBrowserError("browser launcher socket must belong to Linux root")


class PodmanBrowserRuntime:
    """Open one fresh isolated container per authenticated launcher connection."""

    def __init__(
        self,
        root: Path,
        manifest_path: Path,
        *,
        socket_path: Path = _DEFAULT_SOCKET,
    ) -> None:
        manifest = load_release_manifest(manifest_path)
        verify_release_manifest(root, manifest)
        self._release_id = cast(str, manifest["release_id"])
        self._digest = cast(str, cast(dict[str, JsonValue], manifest["browser_image"])["digest"])
        self._socket_path = socket_path
        self._sequence = 0
        self._events = asyncio.Queue[BoundaryEvent](maxsize=64)

    @property
    def image_digest(self) -> str:
        return self._digest

    async def launch(self, spec: IsolatedBrowserLaunchSpec) -> "IsolatedBrowserSession":
        if spec.browser_image_digest != self._digest:
            raise IsolatedBrowserError("browser image digest differs from the verified release")
        if spec.limits != RuntimeLimits():
            raise IsolatedBrowserError("the isolated browser requires fixed runtime limits")
        if (
            spec.control_channel.protocol_version != WORKER_PROTOCOL_VERSION
            or spec.egress_channel.protocol_version != WORKER_PROTOCOL_VERSION
        ):
            raise IsolatedBrowserError("unsupported browser channel version")

        return await self._launch(spec.session_id, sorted(spec.allowed_origins))

    async def launch_snapshot(self, session_id: str) -> "IsolatedBrowserSession":
        """Launch a single-snapshot worker with no egress endpoint."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise IsolatedBrowserError("invalid snapshot session ID")
        return await self._launch(session_id, [])

    async def _launch(self, session_id: str, origins: list[str]) -> "IsolatedBrowserSession":
        if sys.platform != "linux":
            raise IsolatedBrowserError("the isolated browser requires a Linux Podman host")
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(_START_TIMEOUT):
                reader, writer = await asyncio.open_unix_connection(self._socket_path)
                _authenticate_broker(writer)
                broker = await read_worker_frame(reader)
                if (
                    broker
                    != {
                        "status": "broker",
                        "version": WORKER_PROTOCOL_VERSION,
                        "egress": "unix",
                        "image_digest": self._digest,
                        "release_id": self._release_id,
                    }
                    or type(broker["version"]) is not int
                ):
                    raise IsolatedBrowserError("launcher release or protocol does not match")
                await write_worker_frame(
                    writer,
                    {
                        "version": WORKER_PROTOCOL_VERSION,
                        "session_id": session_id,
                        "origins": origins,
                    },
                )
                ready = await read_worker_frame(reader)
                if (
                    ready != {"status": "ready", "version": WORKER_PROTOCOL_VERSION}
                    or type(ready["version"]) is not int
                ):
                    raise IsolatedBrowserError("isolated Chromium failed to start")
        except BaseException as error:
            if writer is not None:
                writer.close()
            if not isinstance(error, IsolatedBrowserError) and isinstance(
                error, (OSError, EOFError, BrowserWorkerError)
            ):
                raise IsolatedBrowserError("isolated browser startup failed") from error
            raise

        self._record(session_id, "isolated_browser_started", "ready")
        return IsolatedBrowserSession(
            reader,
            writer,
            lambda kind, reason: self._record(session_id, kind, reason),
            operations=ISOLATED_OPERATIONS if origins else _SNAPSHOT_OPERATIONS,
        )

    async def next_event(self) -> BoundaryEvent:
        return await self._events.get()

    def _record(self, session_id: str, kind: str, reason: str) -> None:
        self._sequence += 1
        if self._events.full():
            self._events.get_nowait()
        self._events.put_nowait(
            BoundaryEvent(
                self._sequence,
                kind,
                {
                    "session_id": session_id,
                    "release_id": self._release_id,
                    "browser_image_digest": self._digest,
                    "reason": reason,
                },
            )
        )


class IsolatedBrowserSession(BrowserWorkerClient):
    """Typed client with no reconnect or uncertain-command replay."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        record: Callable[[str, str], None],
        *,
        operations: frozenset[str],
    ) -> None:
        self._writer = writer
        self._record = record
        self._operations = operations
        self._state = BrowserSessionState.ACTIVE
        self._lock = asyncio.Lock()
        self._request_id = 0
        self._pending: asyncio.Future[Mapping[str, JsonValue] | BrowserSessionError] | None = None
        self._operation = ""
        self._listener = asyncio.create_task(self._listen(reader))

    @property
    def state(self) -> BrowserSessionState:
        return self._state

    async def render_snapshot(
        self, body: bytes, *, plain_text: bool, encoding: str, link_query: str = ""
    ) -> SnapshotContent:
        parameters = encode_snapshot(
            body, plain_text=plain_text, encoding=encoding, link_query=link_query
        )
        try:
            result = await self._call("render_snapshot", parse_snapshot, parameters)
            self._finish(BrowserSessionState.CLOSED, "snapshot rendered")
            return result
        finally:
            if self._writer.is_closing():
                await self._listener

    async def pause(self) -> None:
        await self._request("pause")

    async def resume(self) -> None:
        await self._request("resume")

    async def close(self) -> None:
        if "close" not in self._operations:
            await self.revoke()
            return
        try:
            if not self._writer.is_closing():
                await self._request("close")
        finally:
            await self._listener

    async def revoke(self) -> None:
        self._finish(BrowserSessionState.REVOKED, "explicit revocation")
        await self._listener

    def _decode_result[T](
        self,
        parse: Callable[[Mapping[str, JsonValue]], T],
        result: Mapping[str, JsonValue],
    ) -> T:
        try:
            return parse(result)
        except (BrowserWorkerError, ValueError):
            self._finish(BrowserSessionState.REVOKED, "invalid worker result")
            raise

    async def _request(
        self, operation: str, parameters: Mapping[str, JsonValue] | None = None
    ) -> Mapping[str, JsonValue]:
        async with self._lock:
            if self._writer.is_closing():
                raise IsolatedBrowserError("isolated browser session ended")
            if operation not in self._operations:
                raise BrowserWorkerError("operation is disabled in the isolated browser")
            self._request_id += 1
            self._operation = operation
            pending = asyncio.get_running_loop().create_future()
            self._pending = pending
            try:
                async with asyncio.timeout(_COMMAND_TIMEOUT):
                    await write_worker_frame(
                        self._writer,
                        {
                            "version": WORKER_PROTOCOL_VERSION,
                            "request_id": self._request_id,
                            "operation": operation,
                            "parameters": dict(parameters or {}),
                        },
                    )
                    result = await pending
                    if isinstance(result, BrowserSessionError):
                        raise result
                    return result
            except NavigationPolicyError:
                raise
            except (TimeoutError, OSError, asyncio.CancelledError) as error:
                pending.cancel()
                self._finish(BrowserSessionState.REVOKED, "command interrupted")
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise IsolatedBrowserError("isolated browser command interrupted") from error
            except (ValueError, TypeError, RecursionError, BrowserWorkerError):
                # A rejected or ambiguous command cannot be followed by another action.
                pending.cancel()
                self._finish(BrowserSessionState.REVOKED, "command rejected")
                raise
            finally:
                self._pending = None

    async def _listen(self, reader: asyncio.StreamReader) -> None:
        try:
            while not self._writer.is_closing():
                self._accept_response(await read_worker_frame(reader))
        except (EOFError, OSError, BrowserWorkerError):
            self._finish(BrowserSessionState.REVOKED, "control connection lost or invalid")
        finally:
            with suppress(OSError):
                await self._writer.wait_closed()

    def _accept_response(self, response: Mapping[str, object]) -> None:
        pending = self._pending
        if (
            pending is None
            or pending.done()
            or type(response.get("version")) is not int
            or response["version"] != WORKER_PROTOCOL_VERSION
            or type(response.get("request_id")) is not int
            or response["request_id"] != self._request_id
            or type(response.get("ok")) is not bool
        ):
            raise IsolatedBrowserError("invalid isolated worker response")
        if not response["ok"]:
            if response.get("error_type") == "NavigationPolicyError":
                pending.set_result(NavigationPolicyError("isolated browser navigation denied"))
                self._finish(BrowserSessionState.REVOKED, "navigation denied")
            else:
                pending.set_result(BrowserWorkerError("isolated browser command rejected"))
                self._finish(BrowserSessionState.REVOKED, "command rejected")
            return
        result = response.get("result")
        if not isinstance(result, dict):
            raise IsolatedBrowserError("invalid isolated worker result")
        # Boundary content is untrusted even when the worker claims otherwise.
        typed_result = cast(dict[str, JsonValue], result)
        if self._operation in {"open", "inspect"}:
            typed_result["untrusted"] = True
        elif self._operation == "observe_state":
            page = typed_result.get("active_page")
            if isinstance(page, dict):
                page["untrusted"] = True
        pending.set_result(typed_result)
        if self._operation == "close":
            self._finish(BrowserSessionState.CLOSED, "closed")
        elif self._operation == "revoke":
            self._finish(BrowserSessionState.REVOKED, "explicit revocation")
        elif self._operation == "pause":
            self._state = BrowserSessionState.PAUSED
        elif self._operation == "resume":
            self._state = BrowserSessionState.ACTIVE

    def _finish(self, state: BrowserSessionState, reason: str) -> None:
        if self._state in {BrowserSessionState.CLOSED, BrowserSessionState.REVOKED}:
            return
        self._state = state
        if state is BrowserSessionState.REVOKED:
            self._writer.transport.abort()
        else:
            self._writer.close()
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(IsolatedBrowserError("isolated browser session ended"))
        self._record("isolated_browser_ended", reason)
