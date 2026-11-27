"""Tests for the Phase 6 supervised browser worker protocol."""

# pyright: reportPrivateUsage=false

import asyncio
import json
import os
import socket
import struct
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from ubc_node.browser import supervised_browser as supervised_module
from ubc_node.browser.browser_contracts import (
    ArtifactRef,
    BrowserContractError,
    BrowserProfileMode,
    BrowserSessionConfig,
    BrowserSessionState,
    ExtractionField,
    ExtractionSchema,
    ExtractionType,
    InteractionKind,
    PageInspection,
    PersistencePolicyDecision,
    TabInfo,
)
from ubc_node.browser.browser_security import LeaseConfig, SessionLease
from ubc_node.browser.stagehand_browser import NavigationPolicyError
from ubc_node.browser.supervised_browser import (
    BrowserWorkerError,
    BrowserWorkerService,
    SupervisedBrowserSession,
    TemporaryProfileManager,
    _artifact,
    _config_from_record,
    _config_record,
    _handoff_parameters,
    _inspection,
    _parse_worker_fd,
    _record_boolean,
    _record_integer,
    _record_number,
    _record_string,
    _spawn_worker,
    _tab,
    available_loopback_port,
    read_worker_frame,
    write_worker_frame,
)
from ubc_node.core.contracts import ActionProposal, HumanHandoff, JsonValue, TaskSpec


def _inspection_record() -> dict[str, JsonValue]:
    return {
        "tab_id": "tab-1",
        "url": "http://fixture.test/page",
        "title": "Fixture",
        "content": "page content",
        "content_truncated": False,
        "untrusted": True,
    }


def _tab_record() -> dict[str, JsonValue]:
    return {
        "tab_id": "tab-1",
        "url": "http://fixture.test/page",
        "title": "Fixture",
        "active": True,
    }


class _FakeBrowser:
    def __init__(self) -> None:
        self._state = BrowserSessionState.ACTIVE
        self.operations: list[str] = []
        self.handoffs: list[str] = []

    @property
    def state(self) -> BrowserSessionState:
        return self._state

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
        operation = cast(str, proposal.parameters["operation"])
        self.operations.append(operation)
        if operation in {"open", "inspect", "click", "fill"}:
            return _inspection_record()
        if operation in {"screenshot", "download"}:
            return {"relative_path": "artifact.bin", "size_bytes": 3, "sha256": "abc"}
        if operation == "observe":
            return {
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "description": "Click fixture",
                        "kind": "click",
                    }
                ]
            }
        if operation == "extract":
            return {"data": {"answer": 42}}
        if operation == "tabs":
            return {"tabs": [_tab_record()]}
        if operation in {"new_tab", "switch_tab"}:
            return _tab_record()
        if operation == "close_tab":
            return {"closed": True}
        if operation == "pause":
            self._state = BrowserSessionState.PAUSED
        elif operation == "resume":
            self._state = BrowserSessionState.ACTIVE
        elif operation == "close":
            self._state = BrowserSessionState.CLOSED
        elif operation == "revoke":
            self._state = BrowserSessionState.REVOKED
        return {"state": self._state.value}

    async def observe_state(self) -> Mapping[str, JsonValue]:
        return {"session_state": self._state.value, "tabs": [], "active_page": None}

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
        self.handoffs.append(request.reason)
        return {"control_returned": True}

    async def handoff_for_task(
        self, task: TaskSpec, request: HumanHandoff
    ) -> Mapping[str, JsonValue]:
        self.handoffs.append(f"{task.task_id}:{request.reason}")
        return {"control_returned": True, "authentication_completed": True}

    async def close(self) -> None:
        self._state = BrowserSessionState.CLOSED

    async def revoke(self) -> None:
        self._state = BrowserSessionState.REVOKED


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 123
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        self.returncode = -15 if self.terminated else 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15


class _FakeGate:
    def __init__(self) -> None:
        self.denial_count = 0
        self.last_denial_reason: str | None = None
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _CaptureWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, value: bytes) -> None:
        self.data.extend(value)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _captured_frame(writer: _CaptureWriter) -> Mapping[str, object]:
    length = struct.unpack("!I", writer.data[:4])[0]
    return cast(Mapping[str, object], json.loads(writer.data[4 : 4 + length]))


async def _streams(sock: socket.socket) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(sock=sock)


async def _client_with_service(
    *, lease_config: LeaseConfig | None = None
) -> tuple[
    SupervisedBrowserSession,
    _FakeBrowser,
    _FakeGate,
    _FakeProcess,
    list[tuple[str, dict[str, JsonValue]]],
    asyncio.Task[None],
]:
    parent_socket, worker_socket = socket.socketpair()
    parent_reader, parent_writer = await _streams(parent_socket)
    worker_reader, worker_writer = await _streams(worker_socket)
    browser = _FakeBrowser()
    lease_settings = lease_config or LeaseConfig(100, 200, 100)
    worker_lease = SessionLease(lease_settings)
    service_task = asyncio.create_task(
        BrowserWorkerService(browser, worker_lease).serve(worker_reader, worker_writer)
    )
    gate = _FakeGate()
    process = _FakeProcess()
    events: list[tuple[str, dict[str, JsonValue]]] = []
    client = SupervisedBrowserSession(
        session_id="session",
        process=cast(Any, process),
        reader=parent_reader,
        writer=parent_writer,
        gate=cast(Any, gate),
        lease=SessionLease(lease_settings),
        event_sink=lambda kind, payload: events.append((kind, dict(payload))),
    )
    return client, browser, gate, process, events, service_task


@pytest.mark.asyncio
async def test_supervised_client_round_trips_every_browser_operation() -> None:
    client, browser, gate, process, events, service = await _client_with_service()
    assert client.state is BrowserSessionState.ACTIVE
    assert (await client.open_url("http://fixture.test/page")).title == "Fixture"
    assert (await client.inspect()).untrusted is True
    assert await client.screenshot(full_page=True) == ArtifactRef("artifact.bin", 3, "abc")
    assert (await client.observe("find button"))[0].kind is InteractionKind.CLICK
    assert (await client.click("candidate-1")).tab_id == "tab-1"
    assert (await client.fill("candidate-1", "private value")).content == "page content"
    extracted = await client.extract(
        "extract",
        ExtractionSchema((ExtractionField("answer", ExtractionType.INTEGER),)),
    )
    assert extracted.data == {"answer": 42}
    assert (await client.list_tabs())[0].active is True
    assert (await client.new_tab("http://fixture.test/new")).tab_id == "tab-1"
    assert (await client.switch_tab("tab-1")).title == "Fixture"
    await client.close_tab("tab-1")
    assert await client.download("candidate-1", timeout_seconds=2) == ArtifactRef(
        "artifact.bin", 3, "abc"
    )
    assert (await client.observe_state())["session_state"] == "active"
    await client.pause()
    assert client.state is BrowserSessionState.PAUSED
    await client.resume()
    assert client.state is BrowserSessionState.ACTIVE

    handoff = HumanHandoff("Login", "http://fixture.test", "Sign in")
    assert (await client.handoff(handoff))["control_returned"] is True
    task = TaskSpec("task-1", "Test supervised browser")
    assert (await client.handoff_for_task(task, handoff))["authentication_completed"] is True

    result = await client.execute(ActionProposal.browser("Inspect", {"operation": "inspect"}))
    assert result["title"] == "Fixture"
    with pytest.raises(BrowserContractError, match="only accepts"):
        await client.execute(ActionProposal.complete("done"))
    with pytest.raises(BrowserContractError, match="operation"):
        await client.execute(ActionProposal.browser("bad", {}))

    await client.close()
    await client.close()
    await service
    assert browser.state is BrowserSessionState.CLOSED
    assert gate.closed is True
    assert process.returncode == 0
    assert events[-1][0] == "browser_worker_exited"
    with pytest.raises(BrowserWorkerError, match="no longer"):
        await client.inspect()


@pytest.mark.asyncio
async def test_supervised_client_revokes_idempotently_and_maps_egress_denial() -> None:
    client, browser, gate, _process, events, service = await _client_with_service()
    gate.denial_count = 1
    gate.last_denial_reason = "origin is not approved"

    async def add_denial() -> None:
        await asyncio.sleep(0)
        gate.denial_count = 2

    denial_task = asyncio.create_task(add_denial())
    with pytest.raises(NavigationPolicyError, match="origin"):
        await client.open_url("http://fixture.test/redirect")
    await denial_task
    await service
    assert browser.state is BrowserSessionState.REVOKED
    assert any(kind == "browser_session_revoked" for kind, _payload in events)
    await client.revoke()


@pytest.mark.asyncio
async def test_worker_service_rejects_malformed_duplicate_and_unsupported_requests() -> None:
    browser = _FakeBrowser()
    service = BrowserWorkerService(browser, SessionLease(LeaseConfig(10, 20, 10)))

    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 99, "request_id": 1, "operation": "inspect"}, cast(Any, writer)
        )
        is False
    )
    assert _captured_frame(writer)["error_type"] == "BrowserWorkerError"

    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 1, "request_id": 1, "operation": "inspect"}, cast(Any, writer)
        )
        is False
    )
    assert _captured_frame(writer)["ok"] is True

    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 1, "request_id": 1, "operation": "inspect"}, cast(Any, writer)
        )
        is False
    )
    assert "duplicate" in str(_captured_frame(writer)["error"])

    invalid_requests: list[tuple[Mapping[str, object], str]] = [
        ({"version": 1, "request_id": 2, "operation": ""}, "operation"),
        (
            {"version": 1, "request_id": 3, "operation": "inspect", "parameters": []},
            "parameters",
        ),
        ({"version": 1, "request_id": 4, "operation": "escape"}, "unsupported"),
        (
            {"version": 1, "request_id": 5, "operation": "handoff", "parameters": {}},
            "reason",
        ),
    ]
    for request, message in invalid_requests:
        writer = _CaptureWriter()
        assert await service._handle(request, cast(Any, writer)) is False
        assert message in str(_captured_frame(writer)["error"])

    for invalid_id in (None, True):
        with pytest.raises(BrowserWorkerError, match="request_id"):
            await service._handle(
                {"version": 1, "request_id": invalid_id, "operation": "inspect"},
                cast(Any, _CaptureWriter()),
            )
    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 1, "request_id": 0, "operation": "inspect"}, cast(Any, writer)
        )
        is False
    )
    assert "request_id" in str(_captured_frame(writer)["error"])

    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 1, "request_id": 6, "operation": "state"}, cast(Any, writer)
        )
        is False
    )
    assert cast(dict[str, object], _captured_frame(writer)["result"])["state"] == "active"


@pytest.mark.asyncio
async def test_worker_service_expires_slow_operation_and_revokes_on_channel_end() -> None:
    class SlowBrowser(_FakeBrowser):
        async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
            await asyncio.sleep(1)
            return await super().execute(proposal)

    slow = SlowBrowser()
    service = BrowserWorkerService(
        slow,
        SessionLease(LeaseConfig(0.01, 0.02, 0.01)),
    )
    writer = _CaptureWriter()
    assert (
        await service._handle(
            {"version": 1, "request_id": 1, "operation": "inspect"}, cast(Any, writer)
        )
        is False
    )
    assert "expired" in str(_captured_frame(writer)["error"])
    assert slow.state is BrowserSessionState.REVOKED

    for close_immediately in (True, False):
        parent, worker = socket.socketpair()
        parent_reader, parent_writer = await _streams(parent)
        worker_reader, worker_writer = await _streams(worker)
        browser = _FakeBrowser()
        lease = SessionLease(LeaseConfig(0.02, 0.04, 0.02))
        task = asyncio.create_task(
            BrowserWorkerService(browser, lease).serve(worker_reader, worker_writer)
        )
        if close_immediately:
            parent_writer.close()
            await parent_writer.wait_closed()
        else:
            await asyncio.sleep(0.03)
        await task
        assert browser.state is BrowserSessionState.REVOKED
        if not parent_writer.is_closing():
            parent_writer.close()
            await parent_writer.wait_closed()
        del parent_reader

    parent, worker = socket.socketpair()
    parent_reader, parent_writer = await _streams(parent)
    worker_reader, worker_writer = await _streams(worker)
    browser = _FakeBrowser()
    task = asyncio.create_task(
        BrowserWorkerService(browser, SessionLease(LeaseConfig(10, 20, 10))).serve(
            worker_reader, worker_writer
        )
    )
    await write_worker_frame(
        parent_writer,
        {"version": 1, "request_id": None, "operation": "inspect"},
    )
    with pytest.raises(BrowserWorkerError, match="request_id"):
        await task
    assert browser.state is BrowserSessionState.REVOKED
    parent_writer.close()
    await parent_writer.wait_closed()
    del parent_reader


async def _client_with_response(
    response: Mapping[str, object] | None,
) -> tuple[SupervisedBrowserSession, _FakeProcess, asyncio.Task[None]]:
    parent, worker = socket.socketpair()
    parent_reader, parent_writer = await _streams(parent)
    worker_reader, worker_writer = await _streams(worker)

    async def respond() -> None:
        try:
            try:
                await read_worker_frame(worker_reader)
            except asyncio.IncompleteReadError:
                return
            if response is not None:
                await write_worker_frame(worker_writer, response)
        finally:
            worker_writer.close()
            await worker_writer.wait_closed()

    responder = asyncio.create_task(respond())
    process = _FakeProcess()
    client = SupervisedBrowserSession(
        session_id="session",
        process=cast(Any, process),
        reader=parent_reader,
        writer=parent_writer,
        gate=cast(Any, _FakeGate()),
        lease=SessionLease(LeaseConfig(10, 20, 10)),
        event_sink=lambda _kind, _payload: None,
    )
    return client, process, responder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type", "message"),
    [
        (
            {"version": 1, "request_id": 2, "ok": True, "result": {}},
            BrowserWorkerError,
            "out of order",
        ),
        (
            {
                "version": 1,
                "request_id": 1,
                "ok": False,
                "error_type": "NavigationPolicyError",
                "error": "denied navigation",
            },
            NavigationPolicyError,
            "denied navigation",
        ),
        (
            {"version": 1, "request_id": 1, "ok": False},
            BrowserWorkerError,
            "rejected",
        ),
        (
            {"version": 1, "request_id": 1, "ok": True, "result": []},
            BrowserWorkerError,
            "invalid result",
        ),
    ],
)
async def test_supervised_client_rejects_bad_worker_responses(
    response: Mapping[str, object], error_type: type[Exception], message: str
) -> None:
    client, _process, responder = await _client_with_response(response)
    with pytest.raises(error_type, match=message):
        await client.inspect()
    await responder
    if not client._finished:
        await client.revoke()


@pytest.mark.asyncio
async def test_supervised_client_handles_channel_and_process_failure() -> None:
    client, _process, responder = await _client_with_response(None)
    with pytest.raises(BrowserWorkerError, match="control channel"):
        await client.inspect()
    await responder

    client2, process2, responder2 = await _client_with_response(None)
    process2.returncode = 7
    with pytest.raises(BrowserWorkerError, match="unexpectedly"):
        await client2.inspect()
    await responder2


@pytest.mark.asyncio
async def test_supervised_client_validates_typed_worker_results() -> None:
    invalid_results: list[tuple[Mapping[str, object], str, str]] = [
        (
            {"version": 1, "request_id": 1, "ok": True, "result": {}},
            "observe",
            "interaction candidates",
        ),
        (
            {
                "version": 1,
                "request_id": 1,
                "ok": True,
                "result": {"candidates": ["bad"]},
            },
            "observe",
            "interaction candidate",
        ),
        (
            {"version": 1, "request_id": 1, "ok": True, "result": {}},
            "extract",
            "extraction data",
        ),
        (
            {"version": 1, "request_id": 1, "ok": True, "result": {}},
            "tabs",
            "tab data",
        ),
    ]
    for response, operation, message in invalid_results:
        client, _process, responder = await _client_with_response(response)
        with pytest.raises(BrowserWorkerError, match=message):
            if operation == "observe":
                await client.observe("find")
            elif operation == "extract":
                await client.extract(
                    "extract",
                    ExtractionSchema((ExtractionField("answer", ExtractionType.INTEGER),)),
                )
            else:
                await client.list_tabs()
        await responder
        await client.revoke()


@pytest.mark.asyncio
async def test_supervised_client_watchdog_expires_session() -> None:
    client, browser, _gate, _process, events, service = await _client_with_service(
        lease_config=LeaseConfig(0.02, 0.04, 0.02)
    )
    await asyncio.sleep(0.06)
    await service
    assert client.state is BrowserSessionState.REVOKED
    assert browser.state is BrowserSessionState.REVOKED
    assert any(kind == "browser_session_expired" for kind, _payload in events)
    await client._watch_expiry()
    await client._finish("ignored", "ignored")


@pytest.mark.asyncio
async def test_revoke_with_pre_revoked_lease_and_process_exit_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _browser, _gate, _process, events, service = await _client_with_service()
    client._lease.revoke("already revoked")
    await client.revoke()
    await service
    assert not any(kind == "browser_session_revoked" for kind, _payload in events)

    client2, _browser2, _gate2, process2, _events2, service2 = await _client_with_service()

    def slow_wait() -> int:
        time.sleep(0.05)
        process2.returncode = 0
        return 0

    process2.wait = slow_wait  # type: ignore[method-assign]
    monkeypatch.setattr(supervised_module, "_PROCESS_EXIT_TIMEOUT_SECONDS", 0.001)
    await client2.close()
    await service2
    assert process2.terminated is True


def test_temporary_profile_manager_scavenges_only_abandoned_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TemporaryProfileManager(tmp_path / "profiles")
    assert manager.scavenge() == ()
    with pytest.raises(ValueError, match="unsafe"):
        manager.create("../escape")

    current = manager.create("abc-123")
    assert current.is_dir()
    assert manager.scavenge() == ()

    stale = manager.root / "stale"
    stale.mkdir()
    (stale / "owner.json").write_text('{"pid":99999999}', encoding="utf-8")
    broken = manager.root / "broken"
    broken.mkdir()
    (broken / "owner.json").write_text("bad", encoding="utf-8")
    ordinary_file = manager.root / "ignore.txt"
    ordinary_file.write_text("keep", encoding="utf-8")
    removed = manager.scavenge()
    assert set(removed) == {stale, broken}
    assert current.exists()
    assert ordinary_file.exists()

    def denied_kill(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", denied_kill)
    assert TemporaryProfileManager._pid_is_live(123) is True
    assert TemporaryProfileManager._pid_is_live(0) is False
    TemporaryProfileManager.remove(current)
    TemporaryProfileManager.remove(current)


@pytest.mark.asyncio
async def test_protocol_frame_validation_and_size_limits() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", 1) + b"{")
    with pytest.raises(BrowserWorkerError, match="size"):
        await read_worker_frame(reader)

    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", 2) + b"[]")
    with pytest.raises(BrowserWorkerError, match="object"):
        await read_worker_frame(reader)

    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", 2) + b"{{")
    with pytest.raises(BrowserWorkerError, match="valid JSON"):
        await read_worker_frame(reader)

    parent, child = socket.socketpair()
    _reader, writer = await _streams(parent)
    with pytest.raises(BrowserWorkerError, match="exceeds"):
        await write_worker_frame(writer, {"value": "x" * (1024 * 1024)})
    writer.close()
    await writer.wait_closed()
    child.close()


def test_wire_record_helpers_and_configuration_validation(tmp_path: Path) -> None:
    config = BrowserSessionConfig(
        workspace=tmp_path,
        allowed_origins=frozenset({"http://fixture.test"}),
        headless=False,
        max_inspection_chars=10,
        max_fill_chars=11,
        max_download_bytes=12,
    )
    assert _config_from_record(_config_record(config)) == config
    invalid_configs: list[object] = [None, []]
    for value in invalid_configs:
        with pytest.raises(BrowserWorkerError, match="configuration"):
            _config_from_record(value)
    with pytest.raises(BrowserWorkerError, match="allowed_origins"):
        _config_from_record({**_config_record(config), "allowed_origins": "bad"})
    with pytest.raises(BrowserWorkerError, match="allowed_origins"):
        _config_from_record({**_config_record(config), "allowed_origins": [1]})

    record: Mapping[str, object] = {"s": "x", "b": True, "i": 3, "n": 2.5}
    assert _record_string(record, "s") == "x"
    assert _record_boolean(record, "b") is True
    assert _record_integer(record, "i") == 3
    assert _record_number(record, "n") == 2.5
    for helper, key in (
        (_record_string, "b"),
        (_record_boolean, "s"),
        (_record_integer, "b"),
        (_record_number, "b"),
    ):
        with pytest.raises(BrowserWorkerError, match="worker field"):
            helper(record, key)

    inspection = _inspection(_inspection_record())
    assert inspection == PageInspection(
        "tab-1",
        "http://fixture.test/page",
        "Fixture",
        "page content",
        False,
    )
    assert _artifact({"relative_path": "a", "size_bytes": 1, "sha256": "hash"}) == ArtifactRef(
        "a", 1, "hash"
    )
    assert _tab(_tab_record()) == TabInfo("tab-1", "http://fixture.test/page", "Fixture", True)
    with pytest.raises(BrowserWorkerError, match="invalid tab"):
        _tab("bad")

    handoff = HumanHandoff("reason", "http://fixture.test", "instructions")
    assert _handoff_parameters(handoff)["reason"] == "reason"
    assert _parse_worker_fd(["--worker-fd", "7"]) == 7


def test_spawn_worker_builds_private_fd_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class StubProcess:
        pass

    def fake_popen(args: list[str], **kwargs: object) -> StubProcess:
        seen["args"] = args
        seen.update(kwargs)
        return StubProcess()

    monkeypatch.setattr("ubc_node.browser.supervised_browser.subprocess.Popen", fake_popen)
    process = _spawn_worker(9)
    assert isinstance(process, StubProcess)
    assert seen["pass_fds"] == (9,)
    assert cast(list[str], seen["args"])[-2:] == ["--worker-fd", "9"]


def test_available_loopback_port_returns_bindable_port() -> None:
    port = available_loopback_port()
    assert 0 < port < 65_536


@pytest.mark.asyncio
async def test_launch_local_bootstraps_worker_and_boundary_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, child = socket.socketpair()
    worker_socket = socket.socket(fileno=os.dup(child.fileno()))
    process = _FakeProcess()
    browser = _FakeBrowser()
    captured: dict[str, object] = {}

    async def worker() -> None:
        reader, writer = await _streams(worker_socket)
        init = await read_worker_frame(reader)
        captured.update(init)
        await write_worker_frame(writer, {"version": 1, "status": "ready"})
        lease_record = cast(Mapping[str, object], init["lease"])
        lease = SessionLease(
            LeaseConfig(
                _record_number(lease_record, "idle_timeout_seconds"),
                _record_number(lease_record, "absolute_timeout_seconds"),
                _record_number(lease_record, "takeover_timeout_seconds"),
            )
        )
        await BrowserWorkerService(browser, lease).serve(reader, writer)

    worker_task = asyncio.create_task(worker())

    def socket_pair() -> tuple[socket.socket, socket.socket]:
        return parent, child

    def spawn_worker(_fd: int) -> _FakeProcess:
        return process

    monkeypatch.setattr(supervised_module.socket, "socketpair", socket_pair)
    monkeypatch.setattr(supervised_module, "_spawn_worker", spawn_worker)
    events: list[tuple[str, dict[str, JsonValue]]] = []
    config = BrowserSessionConfig(
        workspace=tmp_path / "workspace",
        allowed_origins=frozenset({"http://fixture.test"}),
        headless=False,
    )
    chrome = tmp_path / "chrome"
    session = await SupervisedBrowserSession.launch_local(
        config,
        chrome_path=chrome,
        takeover_enabled=True,
        lease_config=LeaseConfig(10, 20, 5),
        event_sink=lambda kind, payload: events.append((kind, dict(payload))),
    )
    assert captured["takeover_enabled"] is True
    assert captured["chrome_path"] == str(chrome.resolve())
    assert str(captured["proxy_server"]).startswith("http://127.0.0.1:")
    assert [kind for kind, _payload in events[:2]] == [
        "browser_worker_started",
        "browser_session_leased",
    ]
    await session.close()
    await worker_task


@pytest.mark.asyncio
async def test_launch_local_rejects_persistence_and_cleans_up_failed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persistent = BrowserSessionConfig(
        workspace=tmp_path,
        allowed_origins=frozenset({"http://fixture.test"}),
        profile_mode=BrowserProfileMode.PERSISTENT,
        project_id="project",
        persistence_policy=PersistencePolicyDecision("project", True, "test"),
        persistent_profile_root=tmp_path / "profiles",
    )
    with pytest.raises(BrowserContractError, match="temporary"):
        await SupervisedBrowserSession.launch_local(
            persistent,
            event_sink=lambda _kind, _payload: None,
        )

    parent, child = socket.socketpair()
    worker_socket = socket.socket(fileno=os.dup(child.fileno()))
    process = _FakeProcess()

    async def failing_worker() -> None:
        reader, writer = await _streams(worker_socket)
        await read_worker_frame(reader)
        await write_worker_frame(writer, {"version": 1, "status": "failed", "error": "boom"})
        writer.close()
        await writer.wait_closed()

    task = asyncio.create_task(failing_worker())

    def socket_pair() -> tuple[socket.socket, socket.socket]:
        return parent, child

    def spawn_worker(_fd: int) -> _FakeProcess:
        return process

    monkeypatch.setattr(supervised_module.socket, "socketpair", socket_pair)
    monkeypatch.setattr(supervised_module, "_spawn_worker", spawn_worker)
    with pytest.raises(BrowserWorkerError, match="boom"):
        await SupervisedBrowserSession.launch_local(
            BrowserSessionConfig(
                workspace=tmp_path / "temporary",
                allowed_origins=frozenset({"http://fixture.test"}),
            ),
            event_sink=lambda _kind, _payload: None,
        )
    await task
    assert process.terminated is True

    parent2, child2 = socket.socketpair()

    def socket_pair2() -> tuple[socket.socket, socket.socket]:
        return parent2, child2

    def failed_spawn(_fd: int) -> _FakeProcess:
        raise OSError("spawn failed")

    monkeypatch.setattr(supervised_module.socket, "socketpair", socket_pair2)
    monkeypatch.setattr(supervised_module, "_spawn_worker", failed_spawn)
    with pytest.raises(OSError, match="spawn failed"):
        await SupervisedBrowserSession.launch_local(
            BrowserSessionConfig(
                workspace=tmp_path / "spawn-failure",
                allowed_origins=frozenset({"http://fixture.test"}),
            ),
            event_sink=lambda _kind, _payload: None,
        )
