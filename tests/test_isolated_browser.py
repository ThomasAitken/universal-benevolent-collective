"""Deterministic tests for the isolated browser's client and worker boundary."""

# pyright: reportPrivateUsage=false

import asyncio
import importlib.util
import socket
import struct
import subprocess
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from ubc_node import isolated_browser, isolated_worker
from ubc_node.browser_contracts import BrowserSessionState
from ubc_node.contracts import ActionProposal
from ubc_node.isolated_browser import FIXTURE_ORIGIN, IsolatedBrowserError, PodmanBrowserRuntime
from ubc_node.isolation_contracts import (
    BoundaryChannelKind,
    ChannelCapability,
    IsolatedBrowserLaunchSpec,
    RuntimeLimits,
)
from ubc_node.release_manifest import create_release_manifest, write_release_manifest
from ubc_node.stagehand_browser import NavigationPolicyError
from ubc_node.supervised_browser import BrowserWorkerError, read_worker_frame, write_worker_frame

ROOT = Path(__file__).parents[1]
DIGEST = "sha256:" + "a" * 64
SPEC = IsolatedBrowserLaunchSpec(
    "fixture",
    DIGEST,
    frozenset({FIXTURE_ORIGIN}),
    ChannelCapability("control", BoundaryChannelKind.CONTROL),
    ChannelCapability("egress", BoundaryChannelKind.EGRESS),
)
BROKER = {"status": "broker", "version": 1, "image_digest": DIGEST, "release_id": "c02"}
READY = {"status": "ready", "version": 1}
INSPECTION = {
    "tab_id": "local-tab",
    "url": FIXTURE_ORIGIN,
    "title": "Fixture",
    "content": "Fixture page",
    "content_truncated": False,
    "untrusted": False,
}


@asynccontextmanager
async def _channel() -> AsyncGenerator[
    tuple[asyncio.StreamReader, asyncio.StreamWriter, asyncio.StreamReader, asyncio.StreamWriter]
]:
    left, right = socket.socketpair()
    client_reader, client_writer = await asyncio.open_connection(sock=left)
    worker_reader, worker_writer = await asyncio.open_connection(sock=right)
    try:
        yield client_reader, client_writer, worker_reader, worker_writer
    finally:
        client_writer.close()
        worker_writer.close()
        await asyncio.gather(client_writer.wait_closed(), worker_writer.wait_closed())


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PodmanBrowserRuntime:
    manifest = tmp_path / "release.json"
    write_release_manifest(
        manifest,
        create_release_manifest(
            ROOT,
            release_id="c02",
            constitution_version="0.1-draft",
            browser_image_reference="localhost/browser",
            browser_image_digest=DIGEST,
        ),
    )
    monkeypatch.setattr(isolated_browser, "sys", SimpleNamespace(platform="linux"))
    return PodmanBrowserRuntime(ROOT, manifest, socket_path=tmp_path / "launcher.sock")


def _mock_connection(
    monkeypatch: pytest.MonkeyPatch, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    monkeypatch.setattr(asyncio, "open_unix_connection", AsyncMock(return_value=(reader, writer)))
    monkeypatch.setattr(isolated_browser, "_authenticate_broker", Mock())


async def _launch(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    worker_reader: asyncio.StreamReader,
    worker_writer: asyncio.StreamWriter,
) -> isolated_browser.IsolatedBrowserSession:
    _mock_connection(monkeypatch, client_reader, client_writer)
    await write_worker_frame(worker_writer, BROKER)
    launch = asyncio.create_task(runtime.launch(SPEC))
    assert await read_worker_frame(worker_reader) == {"version": 1, "session_id": "fixture"}
    await write_worker_frame(worker_writer, READY)
    return await launch


@pytest.mark.parametrize(
    ("spec", "error"),
    [
        (replace(SPEC, browser_image_digest="sha256:" + "b" * 64), "verified release"),
        (replace(SPEC, allowed_origins=frozenset({"http://fixture.test"})), "offline fixture"),
        (replace(SPEC, limits=RuntimeLimits(process_count=32)), "fixed limits"),
        (
            replace(SPEC, control_channel=replace(SPEC.control_channel, protocol_version=2)),
            "version",
        ),
        (replace(SPEC, egress_channel=replace(SPEC.egress_channel, protocol_version=2)), "version"),
    ],
)
async def test_launch_rejects_unsupported_spec(
    runtime: PodmanBrowserRuntime, spec: IsolatedBrowserLaunchSpec, error: str
) -> None:
    with pytest.raises(IsolatedBrowserError, match=error):
        await runtime.launch(spec)


async def test_launch_unsupported_host_and_missing_socket(
    runtime: PodmanBrowserRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(IsolatedBrowserError, match="startup failed"):
        await runtime.launch(SPEC)
    monkeypatch.setattr(isolated_browser.sys, "platform", "darwin")
    with pytest.raises(IsolatedBrowserError, match="Linux Podman"):
        await runtime.launch(SPEC)


@pytest.mark.parametrize("uid", [0, 1001])
def test_launcher_must_be_root(uid: int) -> None:
    peer = Mock()
    peer.getsockopt.return_value = struct.pack("iII", 1, uid, 42)
    writer = Mock()
    writer.get_extra_info.return_value = peer
    if uid == 0:
        isolated_browser._authenticate_broker(writer)
    else:
        with pytest.raises(IsolatedBrowserError, match="must belong to Linux root"):
            isolated_browser._authenticate_broker(writer)


@pytest.mark.parametrize(
    ("broker", "ready", "error"),
    [
        ({**BROKER, "image_digest": "wrong"}, None, "launcher release"),
        ({**BROKER, "version": True}, None, "launcher release"),
        (BROKER, {"status": "failed"}, "failed to start"),
        (BROKER, {**READY, "version": True}, "failed to start"),
        (BROKER, None, "startup failed"),
    ],
)
async def test_launch_rejects_unverified_or_incomplete_handshake(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
    broker: Mapping[str, object],
    ready: Mapping[str, object] | None,
    error: str,
) -> None:
    async with _channel() as (client_reader, client_writer, worker_reader, worker_writer):
        _mock_connection(monkeypatch, client_reader, client_writer)
        await write_worker_frame(worker_writer, broker)
        launch = asyncio.create_task(runtime.launch(SPEC))
        if broker is BROKER:
            await read_worker_frame(worker_reader)
            if ready is None:
                worker_writer.close()
            else:
                await write_worker_frame(worker_writer, ready)
        with pytest.raises(IsolatedBrowserError, match=error):
            await launch
        assert client_writer.is_closing()


async def test_cancel_launch_closes_connection(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _channel() as (client_reader, client_writer, worker_reader, worker_writer):
        _mock_connection(monkeypatch, client_reader, client_writer)
        await write_worker_frame(worker_writer, BROKER)
        launch = asyncio.create_task(runtime.launch(SPEC))
        await read_worker_frame(worker_reader)
        launch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await launch
        assert await worker_reader.read() == b""


async def _response(
    worker_reader: asyncio.StreamReader,
    worker_writer: asyncio.StreamWriter,
    operation: str,
    result: Mapping[str, object],
) -> None:
    command = await read_worker_frame(worker_reader)
    assert command["operation"] == operation
    await write_worker_frame(
        worker_writer,
        {"version": 1, "request_id": command["request_id"], "ok": True, "result": result},
    )


async def test_typed_browser_methods_and_lifecycle(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _channel() as streams:
        session = await _launch(runtime, monkeypatch, *streams)
        event = await runtime.next_event()
        assert event.sequence == 1 and event.kind == "isolated_browser_started"
        assert event.payload["browser_image_digest"] == DIGEST
        assert event.payload["release_id"] == "c02"

        with pytest.raises(BrowserWorkerError, match="disabled"):
            await session.screenshot()
        inspection = asyncio.create_task(session.open_url(FIXTURE_ORIGIN))
        await _response(*streams[2:], "open", INSPECTION)
        assert (await inspection).untrusted
        for page in (None, INSPECTION):
            observation = asyncio.create_task(session.observe_state())
            await _response(*streams[2:], "observe_state", {"active_page": page})
            observed = (await observation)["active_page"]
            assert (
                observed is None if page is None else cast(dict[str, object], observed)["untrusted"]
            )
        for action, state in (
            (session.pause, BrowserSessionState.PAUSED),
            (session.resume, BrowserSessionState.ACTIVE),
            (session.close, BrowserSessionState.CLOSED),
        ):
            command = asyncio.create_task(action())
            await _response(*streams[2:], action.__name__, {"state": state.value})
            await command
            assert session.state is state
        await session.close()
        await session.revoke()
        with pytest.raises(IsolatedBrowserError, match="ended"):
            await session.inspect()
        ended = await runtime.next_event()
        assert ended.sequence == 2 and ended.payload["reason"] == "closed"


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        ({"version": True}, IsolatedBrowserError, "ended"),
        ({"version": 2}, IsolatedBrowserError, "ended"),
        ({"request_id": True}, IsolatedBrowserError, "ended"),
        ({"request_id": 2}, IsolatedBrowserError, "ended"),
        ({"ok": "true"}, IsolatedBrowserError, "ended"),
        ({"result": []}, IsolatedBrowserError, "ended"),
        ({"ok": False, "error": "/private/cdp"}, BrowserWorkerError, "command rejected"),
        ({"ok": False, "error_type": "NavigationPolicyError"}, NavigationPolicyError, "denied"),
        (None, IsolatedBrowserError, "ended"),
    ],
)
async def test_inflight_failures_are_terminal(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
    response: Mapping[str, object] | None,
    error: type[Exception],
    message: str,
) -> None:
    async with _channel() as (client_reader, client_writer, worker_reader, worker_writer):
        session = await _launch(
            runtime, monkeypatch, client_reader, client_writer, worker_reader, worker_writer
        )
        action = asyncio.create_task(session.inspect())
        request = await read_worker_frame(worker_reader)
        if response is None:
            worker_writer.close()
        else:
            await write_worker_frame(
                worker_writer,
                {
                    "version": 1,
                    "request_id": request["request_id"],
                    "ok": True,
                    "result": {},
                    **response,
                },
            )
        with pytest.raises(error, match=message):
            await action
        assert session.state is BrowserSessionState.REVOKED
        await session.close()


@pytest.mark.parametrize("failure", ["cancel", "timeout", "write", "idle", "revoke", "unsolicited"])
async def test_control_loss_cancellation_and_timeout(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    async with _channel() as streams:
        session = await _launch(runtime, monkeypatch, *streams)
        _, client_writer, worker_reader, worker_writer = streams
        await runtime.next_event()

        if failure == "idle":
            worker_writer.close()
        elif failure == "revoke":
            revoke = asyncio.create_task(
                session.execute(ActionProposal.browser("revoke", {"operation": "revoke"}))
            )
            await _response(worker_reader, worker_writer, "revoke", {"state": "revoked"})
            await revoke
        elif failure == "unsolicited":
            await write_worker_frame(worker_writer, {"version": 1, "request_id": 1, "ok": True})
        elif failure == "write":
            monkeypatch.setattr(client_writer, "drain", AsyncMock(side_effect=BrokenPipeError))
            with pytest.raises(IsolatedBrowserError, match="interrupted"):
                await session.inspect()
        else:
            if failure == "timeout":
                monkeypatch.setattr(isolated_browser, "_COMMAND_TIMEOUT", 0.02)
            action = asyncio.create_task(session.inspect())
            await read_worker_frame(worker_reader)
            if failure == "cancel":
                action.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await action
            else:
                with pytest.raises(IsolatedBrowserError, match="interrupted"):
                    await action

        assert (await runtime.next_event()).kind == "isolated_browser_ended"
        assert session.state is BrowserSessionState.REVOKED
        await session.revoke()


async def test_event_queue_is_bounded(runtime: PodmanBrowserRuntime) -> None:
    for _ in range(65):
        runtime._record("fixture", "test_evt", "test")
    assert (await runtime.next_event()).sequence == 2


@pytest.mark.parametrize("init", [None, {"version": True}, {"version": 2}, {"version": 1}])
async def test_worker_rejects_invalid_startup(init: Mapping[str, object] | None) -> None:
    async with _channel() as (client_reader, client_writer, worker_reader, worker_writer):
        task = asyncio.create_task(isolated_worker._serve(worker_reader, worker_writer))
        if init is None:
            client_writer.close()
            with pytest.raises(EOFError):
                await task
        else:
            await write_worker_frame(client_writer, init)
            with pytest.raises(BrowserWorkerError, match="startup frame"):
                await task
            assert await client_reader.read() == b""


@pytest.mark.parametrize(
    "outcome", ["ok", "denied", "contacted", "bypass", "startup-write", "startup-fail"]
)
async def test_worker_starts_only_local_fixture(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    gate = Mock(endpoint="http://127.0.0.1:8081", denial_count=0)
    gate.start = AsyncMock()
    gate.close = AsyncMock()
    fixture = Mock()
    fixture.__enter__ = Mock(return_value=fixture)
    fixture.__exit__ = Mock(return_value=None)
    fixture.outside_request_count = int(outcome in {"contacted", "bypass"})
    browser = Mock(state=BrowserSessionState.ACTIVE)

    async def revoke() -> None:
        browser.state = BrowserSessionState.REVOKED

    browser.revoke = AsyncMock(side_effect=revoke)

    async def execute(proposal: ActionProposal) -> Mapping[str, object]:
        if proposal.parameters["operation"] == "open" and outcome in {"denied", "contacted"}:
            gate.denial_count += 1
            raise RuntimeError("proxy denied")
        return {"state": "active"}

    browser.execute = execute
    launch = AsyncMock(return_value=browser)
    if outcome == "startup-fail":
        launch.side_effect = RuntimeError("startup failed")
    monkeypatch.setattr(isolated_worker.StagehandBrowserAdapter, "launch_local", launch)
    monkeypatch.setattr(isolated_worker, "EgressGate", Mock(return_value=gate))
    monkeypatch.setattr(isolated_worker, "Phase5FixtureSite", Mock(return_value=fixture))
    async with _channel() as (client_reader, client_writer, worker_reader, worker_writer):
        if outcome == "startup-write":
            monkeypatch.setattr(worker_writer, "drain", AsyncMock(side_effect=BrokenPipeError))
        await write_worker_frame(client_writer, {"version": 1, "session_id": "fixture"})
        task = asyncio.create_task(isolated_worker._serve(worker_reader, worker_writer))
        if outcome in {"startup-write", "startup-fail"}:
            with pytest.raises((BrokenPipeError, RuntimeError)):
                await task
        else:
            assert await read_worker_frame(client_reader) == READY
            assert launch.call_args.kwargs["accept_downloads"] is False
            assert launch.call_args.args[0].allowed_origins == {FIXTURE_ORIGIN}
            for request_id, operation in enumerate(("screenshot", "open"), 1):
                await write_worker_frame(
                    client_writer,
                    {"version": 1, "request_id": request_id, "operation": operation},
                )
                result = await read_worker_frame(client_reader)
                if operation == "screenshot":
                    assert result["error_type"] == "BrowserWorkerError"
                elif outcome == "ok":
                    assert result["ok"] is True
                elif outcome == "denied":
                    assert result["error_type"] == "NavigationPolicyError"
                else:
                    assert result["error_type"] == "BrowserWorkerError"
            client_writer.close()
            await task
        gate.close.assert_awaited_once()
        if outcome != "startup-fail":
            browser.revoke.assert_awaited_once()


async def test_malformed_typed_result_and_failed_close_revoke_session(
    runtime: PodmanBrowserRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _channel() as streams:
        session = await _launch(runtime, monkeypatch, *streams)
        await runtime.next_event()
        action = asyncio.create_task(session.open_url(FIXTURE_ORIGIN))
        await _response(*streams[2:], "open", {"tab_id": 123})
        with pytest.raises(BrowserWorkerError, match="tab_id"):
            await action
        assert session.state is BrowserSessionState.REVOKED
        assert (await runtime.next_event()).payload["reason"] == "invalid worker result"
        await session.close()

    async with _channel() as streams:
        session = await _launch(runtime, monkeypatch, *streams)
        await runtime.next_event()
        closing = asyncio.create_task(session.close())
        request = await read_worker_frame(streams[2])
        assert request["operation"] == "close"
        await write_worker_frame(
            streams[3],
            {"version": 1, "request_id": request["request_id"], "ok": False, "error": "busy"},
        )
        with pytest.raises(BrowserWorkerError, match="command rejected"):
            await closing
        assert session.state is BrowserSessionState.REVOKED
        assert session._listener.done()


def _load_broker_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ubc_broker", ROOT / "browser-runtime/broker.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_broker_validation_and_podman_command(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _load_broker_module()
    invocation_id = "0123456789abcdef" * 2
    image = f"localhost/ubc-browser@{DIGEST}"

    assert broker.container_name(invocation_id) == f"ubc-browser-{invocation_id}"
    with pytest.raises(ValueError, match="invocation ID"):
        broker.container_name("not-hex")

    assert broker.image_digest(image) == DIGEST
    with pytest.raises(ValueError, match="immutable OCI digest"):
        broker.image_digest("localhost/ubc-browser:latest")

    command = broker.podman_command("ubc-browser-test", image, 7, "/system.slice/ubc-browser.slice")
    for flag in (
        "--pull=never",
        "--rm",
        "--attach=stdout",
        "--sig-proxy=false",
        "--runtime=crun",
        "--preserve-fd=7",
        "--userns=auto:size=65536",
        "--user=65532:65532",
        "--pid=private",
        "--ipc=private",
        "--network=none",
        "--http-proxy=false",
        "--no-hosts",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--read-only-tmpfs=false",
        "--image-volume=ignore",
        "--log-driver=none",
        "--cgroup-parent=/system.slice/ubc-browser.slice",
        "--memory=1073741824",
        "--memory-swap=1073741824",
        "--cpus=1",
        "--pids-limit=256",
        "--timeout=1800",
    ):
        assert flag in command
    assert command[-3:] == ("--", image, "7")

    monkeypatch.setenv("INVOCATION_ID", invocation_id)
    for argv in (["broker.py"], ["broker.py", "unknown"], ["broker.py", "run", "extra"]):
        monkeypatch.setattr(broker.sys, "argv", argv)
        with pytest.raises(ValueError, match="subcommand"):
            broker.main()

    run_mock = Mock()
    monkeypatch.setattr(broker.sys, "argv", ["broker.py", "cleanup"])
    monkeypatch.setattr(subprocess, "run", run_mock)
    broker.main()
    run_mock.assert_called_once_with(
        [*broker.PODMAN, "rm", "--force", "--time=0", "--ignore", f"ubc-browser-{invocation_id}"],
        check=True,
    )

    monkeypatch.setattr(broker.sys, "argv", ["broker.py", "run"])
    monkeypatch.setenv("UBC_BROWSER_IMAGE", image)
    monkeypatch.setenv("UBC_BROWSER_RELEASE", "bad/release")
    with pytest.raises(ValueError, match="release ID"):
        broker.main()
