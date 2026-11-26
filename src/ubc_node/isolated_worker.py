"""Chromium entry point for the disconnected C02 container."""

import asyncio
import socket
import sys
from collections.abc import Mapping
from pathlib import Path

from ubc_node.browser_contracts import BrowserSessionConfig, BrowserSessionState
from ubc_node.browser_security import EgressGate, LeaseConfig, SessionLease
from ubc_node.contracts import JsonValue
from ubc_node.demo_fixture import Phase5FixtureSite
from ubc_node.isolated_browser import FIXTURE_OPERATIONS, FIXTURE_ORIGIN, FIXTURE_OUTSIDE_PORT
from ubc_node.stagehand_browser import NavigationPolicyError, StagehandBrowserAdapter
from ubc_node.supervised_browser import (
    WORKER_PROTOCOL_VERSION,
    BrowserWorkerError,
    BrowserWorkerService,
    available_loopback_port,
    read_worker_frame,
    write_worker_frame,
)


class _FixtureWorkerService(BrowserWorkerService):
    def __init__(
        self,
        browser: StagehandBrowserAdapter,
        lease: SessionLease,
        gate: EgressGate,
        fixture: Phase5FixtureSite,
    ) -> None:
        super().__init__(browser, lease)
        self._gate = gate
        self._fixture = fixture

    async def _dispatch(
        self, operation: str, parameters: Mapping[str, object]
    ) -> Mapping[str, JsonValue]:
        if operation not in FIXTURE_OPERATIONS:
            raise BrowserWorkerError("operation is disabled in the isolated fixture")
        denials_before = self._gate.denial_count
        try:
            return await super()._dispatch(operation, parameters)
        finally:
            if self._fixture.outside_request_count:
                raise BrowserWorkerError("fixture unexpectedly contacted the denied origin")
            if self._gate.denial_count != denials_before:
                raise NavigationPolicyError("isolated fixture navigation denied")


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        init = await read_worker_frame(reader)
        session_id = init.get("session_id")
        if (
            type(init.get("version")) is not int
            or init["version"] != WORKER_PROTOCOL_VERSION
            or not isinstance(session_id, str)
        ):
            raise BrowserWorkerError("invalid isolated startup frame")
        research_port = int(FIXTURE_ORIGIN.rsplit(":", 1)[1])
        with Phase5FixtureSite(
            research_port=research_port, outside_port=FIXTURE_OUTSIDE_PORT
        ) as fixture:
            lease = SessionLease(LeaseConfig())
            gate = EgressGate(
                session_id=session_id,
                allowed_origins=frozenset({FIXTURE_ORIGIN}),
                lease=lease,
                event_sink=lambda _kind, _payload: None,
            )
            await gate.start()
            try:
                debugging_port = available_loopback_port()
                browser = await StagehandBrowserAdapter.launch_local(
                    BrowserSessionConfig(
                        workspace=Path("/var/lib/ubc/quarantine"),
                        allowed_origins=frozenset({FIXTURE_ORIGIN}),
                    ),
                    temporary_profile_path=Path("/var/lib/ubc/profile/chromium"),
                    proxy_server=gate.endpoint,
                    proxy_bypass=(
                        f"<-loopback>;http://127.0.0.1:{debugging_port};"
                        f"ws://127.0.0.1:{debugging_port}"
                    ),
                    debugging_port=debugging_port,
                    chromium_sandbox=False,
                    accept_downloads=False,
                )
                try:
                    await write_worker_frame(
                        writer, {"version": WORKER_PROTOCOL_VERSION, "status": "ready"}
                    )
                    await _FixtureWorkerService(browser, lease, gate, fixture).serve(reader, writer)
                finally:
                    if browser.state not in {
                        BrowserSessionState.CLOSED,
                        BrowserSessionState.REVOKED,
                    }:
                        await browser.revoke()
            finally:
                await gate.close()
    finally:
        writer.close()


async def _main(fd: int) -> None:  # pragma: no cover - exercised by the Linux isolation gate
    with socket.socket(fileno=fd) as channel:
        channel.set_inheritable(False)
        reader, writer = await asyncio.open_connection(sock=channel)
        await _serve(reader, writer)


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1])))
