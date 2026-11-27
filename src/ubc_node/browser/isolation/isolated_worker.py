"""Chromium worker for a disconnected network namespace with a private egress socket."""

import asyncio
import socket
import sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import cast

from ubc_node.browser.browser_contracts import BrowserSessionConfig, BrowserSessionState
from ubc_node.browser.browser_security import LeaseConfig, SessionLease
from ubc_node.browser.isolation.isolated_browser import ISOLATED_OPERATIONS
from ubc_node.browser.isolation.isolated_egress import tunnel
from ubc_node.browser.stagehand_browser import StagehandBrowserAdapter
from ubc_node.browser.supervised_browser import (
    WORKER_PROTOCOL_VERSION,
    BrowserWorkerError,
    BrowserWorkerService,
    available_loopback_port,
    read_worker_frame,
    write_worker_frame,
)
from ubc_node.core.contracts import JsonValue

_GATE_SOCKET = Path("/run/ubc-egress/proxy.sock")
_CHROMIUM_ARGS = (
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
)


async def _bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    gate_writer: asyncio.StreamWriter | None = None
    try:
        gate_reader, gate_writer = await asyncio.open_unix_connection(_GATE_SOCKET)
        await tunnel((reader, writer), (gate_reader, gate_writer))
    except OSError:
        pass
    finally:
        if gate_writer is not None:
            gate_writer.close()
        writer.close()


@asynccontextmanager
async def _proxy() -> AsyncGenerator[str]:
    server = await asyncio.start_server(_bridge, "127.0.0.1", 0)
    try:
        port = cast(tuple[str, int], server.sockets[0].getsockname())[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        server.abort_clients()
        await server.wait_closed()


class _IsolatedBrowserAdapter(StagehandBrowserAdapter):
    async def _shutdown(self) -> None:
        # Host policy can deny Stagehand's fallback signal to Chromium. The
        # broker retires the whole container when this worker closes its channel.
        with suppress(PermissionError):
            await super()._shutdown()


class _IsolatedWorkerService(BrowserWorkerService):
    async def _dispatch(
        self, operation: str, parameters: Mapping[str, object]
    ) -> Mapping[str, JsonValue]:
        if operation not in ISOLATED_OPERATIONS:
            raise BrowserWorkerError("operation is disabled in the isolated browser")
        return await super()._dispatch(operation, parameters)


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        init = await read_worker_frame(reader)
        origins = init.get("origins")
        if (
            type(init.get("version")) is not int
            or init["version"] != WORKER_PROTOCOL_VERSION
            or not isinstance(init.get("session_id"), str)
            or not isinstance(origins, list)
            or not all(isinstance(origin, str) for origin in cast(list[object], origins))
        ):
            raise BrowserWorkerError("invalid isolated startup frame")

        async with _proxy() as endpoint:
            lease = SessionLease(LeaseConfig())
            debugging_port = available_loopback_port()
            browser = await _IsolatedBrowserAdapter.launch_local(
                BrowserSessionConfig(
                    workspace=Path("/var/lib/ubc/quarantine"),
                    allowed_origins=frozenset(cast(list[str], origins)),
                ),
                temporary_profile_path=Path("/var/lib/ubc/profile/chromium"),
                proxy_server=endpoint,
                proxy_bypass=(
                    f"<-loopback>;http://127.0.0.1:{debugging_port};ws://127.0.0.1:{debugging_port}"
                ),
                debugging_port=debugging_port,
                chromium_sandbox=False,
                accept_downloads=False,
                browser_args=_CHROMIUM_ARGS,
            )
            try:
                await write_worker_frame(
                    writer, {"version": WORKER_PROTOCOL_VERSION, "status": "ready"}
                )
                await _IsolatedWorkerService(browser, lease).serve(reader, writer)
            finally:
                if browser.state not in {BrowserSessionState.CLOSED, BrowserSessionState.REVOKED}:
                    await browser.revoke()
    finally:
        writer.close()


async def _main(fd: int) -> None:  # pragma: no cover - exercised by the Linux isolation gate
    with socket.socket(fileno=fd) as channel:
        channel.set_inheritable(False)
        reader, writer = await asyncio.open_connection(sock=channel)
        await _serve(reader, writer)


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1])))
