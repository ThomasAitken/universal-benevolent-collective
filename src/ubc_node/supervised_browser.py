"""Process-supervised browser client and worker protocol."""

import argparse
import asyncio
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
from collections.abc import Awaitable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from ubc_node.browser_contracts import (
    ArtifactRef,
    BrowserContractError,
    BrowserProfileMode,
    BrowserSessionConfig,
    BrowserSessionState,
    ExtractionResult,
    ExtractionSchema,
    InteractionCandidate,
    InteractionKind,
    PageInspection,
    TabInfo,
)
from ubc_node.browser_security import (
    BoundaryEventSink,
    EgressGate,
    LeaseConfig,
    SessionLease,
    SessionLeaseError,
)
from ubc_node.contracts import ActionKind, ActionProposal, HumanHandoff, JsonValue, TaskSpec
from ubc_node.stagehand_browser import (
    BrowserSessionError,
    NavigationPolicyError,
    StagehandBrowserAdapter,
)
from ubc_node.takeover import CliTakeoverHandler

_PROTOCOL_VERSION = 1
_MAX_MESSAGE_BYTES = 1024 * 1024
_PROCESS_EXIT_TIMEOUT_SECONDS = 5.0


class BrowserWorkerError(BrowserSessionError):
    """Raised when the supervised browser worker fails or violates its protocol."""


class _WorkerBrowser(Protocol):
    @property
    def state(self) -> BrowserSessionState: ...

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]: ...

    async def observe_state(self) -> Mapping[str, JsonValue]: ...

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]: ...

    async def handoff_for_task(
        self, task: TaskSpec, request: HumanHandoff
    ) -> Mapping[str, JsonValue]: ...

    async def close(self) -> None: ...

    async def revoke(self) -> None: ...


class TemporaryProfileManager:
    """Create disposable worker profiles and remove abandoned predecessors."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def scavenge(self) -> tuple[Path, ...]:
        if not self.root.exists():
            return ()
        removed: list[Path] = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            owner = path / "owner.json"
            try:
                body = json.loads(owner.read_text(encoding="utf-8"))
                pid = int(body["pid"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pid = -1
            if not self._pid_is_live(pid):
                shutil.rmtree(path)
                removed.append(path)
        return tuple(removed)

    def create(self, session_id: str) -> Path:
        if not session_id or any(character not in "0123456789abcdef-" for character in session_id):
            raise ValueError("session_id contains unsafe profile characters")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / session_id
        path.mkdir(mode=0o700)
        (path / "owner.json").write_text(
            json.dumps({"pid": os.getpid(), "session_id": session_id}),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def remove(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _pid_is_live(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


async def _read_frame(reader: asyncio.StreamReader) -> Mapping[str, object]:
    prefix = await reader.readexactly(4)
    length = struct.unpack("!I", prefix)[0]
    if length < 2 or length > _MAX_MESSAGE_BYTES:
        raise BrowserWorkerError("worker message size is invalid")
    payload = await reader.readexactly(length)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrowserWorkerError("worker message is not valid JSON") from error
    if not isinstance(value, dict):
        raise BrowserWorkerError("worker message must be an object")
    return cast(Mapping[str, object], value)


async def _write_frame(writer: asyncio.StreamWriter, value: Mapping[str, object]) -> None:
    payload = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(payload) > _MAX_MESSAGE_BYTES:
        raise BrowserWorkerError("worker message exceeds the protocol limit")
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()


class BrowserWorkerService:
    """Serve validated, sequential browser commands over one private channel."""

    def __init__(self, browser: _WorkerBrowser, lease: SessionLease) -> None:
        self._browser = browser
        self._lease = lease
        self._seen_ids: set[int] = set()

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    request = await asyncio.wait_for(
                        _read_frame(reader), timeout=self._lease.remaining_seconds
                    )
                except TimeoutError:
                    await self._browser.revoke()
                    break
                except asyncio.IncompleteReadError:
                    await self._browser.revoke()
                    break
                should_stop = await self._handle(request, writer)
                if should_stop:
                    break
        finally:
            if self._browser.state not in {
                BrowserSessionState.CLOSED,
                BrowserSessionState.REVOKED,
            }:
                await self._browser.revoke()
            writer.close()
            await writer.wait_closed()

    async def _handle(self, request: Mapping[str, object], writer: asyncio.StreamWriter) -> bool:
        request_id = request.get("request_id")
        try:
            if request.get("version") != _PROTOCOL_VERSION:
                raise BrowserWorkerError("unsupported worker protocol version")
            if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 1:
                raise BrowserWorkerError("request_id must be a positive integer")
            if request_id in self._seen_ids:
                raise BrowserWorkerError("duplicate request_id")
            self._seen_ids.add(request_id)
            operation = request.get("operation")
            parameters = request.get("parameters", {})
            if not isinstance(operation, str) or not operation:
                raise BrowserWorkerError("operation must be a non-empty string")
            if not isinstance(parameters, dict):
                raise BrowserWorkerError("parameters must be an object")
            if operation in {"handoff", "handoff_for_task"}:
                self._lease.begin_takeover()
            else:
                self._lease.touch()
            result = await self._with_expiry(
                self._dispatch(operation, cast(Mapping[str, object], parameters))
            )
            await _write_frame(
                writer,
                {
                    "version": _PROTOCOL_VERSION,
                    "request_id": request_id,
                    "ok": True,
                    "result": dict(result),
                },
            )
            return operation in {"close", "revoke"}
        except Exception as error:
            if isinstance(request_id, int) and not isinstance(request_id, bool):
                await _write_frame(
                    writer,
                    {
                        "version": _PROTOCOL_VERSION,
                        "request_id": request_id,
                        "ok": False,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                )
                return False
            raise

    async def _with_expiry(
        self,
        operation: Awaitable[Mapping[str, JsonValue]],
    ) -> Mapping[str, JsonValue]:
        task = asyncio.ensure_future(operation)
        expiry = asyncio.create_task(asyncio.sleep(self._lease.remaining_seconds))
        done, _pending = await asyncio.wait({task, expiry}, return_when=asyncio.FIRST_COMPLETED)
        if expiry in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._browser.revoke()
            raise SessionLeaseError("browser session expired")
        expiry.cancel()
        await asyncio.gather(expiry, return_exceptions=True)
        return await task

    async def _dispatch(
        self, operation: str, parameters: Mapping[str, object]
    ) -> Mapping[str, JsonValue]:
        if operation == "state":
            return {"state": self._browser.state.value}
        if operation == "observe_state":
            return await self._browser.observe_state()
        if operation in {"handoff", "handoff_for_task"}:
            request = HumanHandoff(
                self._string(parameters, "reason"),
                self._string(parameters, "origin"),
                self._string(parameters, "instructions"),
            )
            if operation == "handoff":
                return await self._browser.handoff(request)
            task = TaskSpec(
                self._string(parameters, "task_id"),
                self._string(parameters, "task_objective"),
            )
            return await self._browser.handoff_for_task(task, request)
        if operation not in {
            "open",
            "inspect",
            "screenshot",
            "observe",
            "click",
            "fill",
            "extract",
            "tabs",
            "new_tab",
            "switch_tab",
            "close_tab",
            "download",
            "pause",
            "resume",
            "close",
            "revoke",
        }:
            raise BrowserContractError(f"unsupported browser operation: {operation}")
        json_parameters = cast(Mapping[str, JsonValue], parameters)
        proposal = ActionProposal.browser(
            f"Supervised browser {operation}",
            {
                "operation": operation,
                **json_parameters,
            },
        )
        return await self._browser.execute(proposal)

    @staticmethod
    def _string(parameters: Mapping[str, object], key: str) -> str:
        value = parameters.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BrowserWorkerError(f"{key} must be a non-empty string")
        return value


class SupervisedBrowserSession:
    """Parent-side typed browser capability backed by a dedicated worker process."""

    def __init__(
        self,
        *,
        session_id: str,
        process: subprocess.Popen[bytes],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        gate: EgressGate,
        lease: SessionLease,
        event_sink: BoundaryEventSink,
    ) -> None:
        self.session_id = session_id
        self._process = process
        self._reader = reader
        self._writer = writer
        self._gate = gate
        self._lease = lease
        self._event_sink = event_sink
        self._state = BrowserSessionState.ACTIVE
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._finished = False
        self._watchdog = asyncio.create_task(self._watch_expiry())

    @classmethod
    async def launch_local(
        cls,
        config: BrowserSessionConfig,
        *,
        chrome_path: Path | None = None,
        takeover_enabled: bool = False,
        lease_config: LeaseConfig | None = None,
        event_sink: BoundaryEventSink,
    ) -> "SupervisedBrowserSession":
        if config.profile_mode is not BrowserProfileMode.TEMPORARY:
            raise BrowserContractError("supervised Phase 6 sessions require a temporary profile")
        lease_settings = lease_config or LeaseConfig()
        lease = SessionLease(lease_settings)
        session_id = str(uuid4())
        gate = EgressGate(
            session_id=session_id,
            allowed_origins=config.allowed_origins,
            lease=lease,
            event_sink=event_sink,
        )
        await gate.start()
        parent_socket, child_socket = socket.socketpair()
        process: subprocess.Popen[bytes] | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            process = await asyncio.to_thread(_spawn_worker, child_socket.fileno())
            child_socket.close()
            reader, writer = await asyncio.open_connection(sock=parent_socket)
            await _write_frame(
                writer,
                {
                    "version": _PROTOCOL_VERSION,
                    "session_id": session_id,
                    "config": _config_record(config),
                    "lease": asdict(lease_settings),
                    "proxy_server": gate.endpoint,
                    "chrome_path": str(chrome_path.resolve()) if chrome_path is not None else None,
                    "takeover_enabled": takeover_enabled,
                },
            )
            ready = await asyncio.wait_for(_read_frame(reader), timeout=60)
            if ready.get("status") != "ready":
                raise BrowserWorkerError(str(ready.get("error", "browser worker failed to start")))
            event_sink("browser_worker_started", {"session_id": session_id, "pid": process.pid})
            event_sink(
                "browser_session_leased",
                {
                    "session_id": session_id,
                    "idle_timeout_seconds": lease_settings.idle_timeout_seconds,
                    "absolute_timeout_seconds": lease_settings.absolute_timeout_seconds,
                },
            )
            return cls(
                session_id=session_id,
                process=process,
                reader=reader,
                writer=writer,
                gate=gate,
                lease=lease,
                event_sink=event_sink,
            )
        except Exception:
            child_socket.close()
            parent_socket.close()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            if process is not None and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait)
            await gate.close()
            raise

    @property
    def state(self) -> BrowserSessionState:
        return self._state

    async def open_url(self, url: str) -> PageInspection:
        return _inspection(await self._request("open", {"url": url}))

    async def inspect(self) -> PageInspection:
        return _inspection(await self._request("inspect"))

    async def screenshot(self, *, full_page: bool = False) -> ArtifactRef:
        return _artifact(await self._request("screenshot", {"full_page": full_page}))

    async def observe(self, instruction: str) -> tuple[InteractionCandidate, ...]:
        result = await self._request("observe", {"instruction": instruction})
        values = result.get("candidates")
        if not isinstance(values, list):
            raise BrowserWorkerError("worker returned invalid interaction candidates")
        candidates: list[InteractionCandidate] = []
        for value in values:
            if not isinstance(value, dict):
                raise BrowserWorkerError("worker returned an invalid interaction candidate")
            candidates.append(
                InteractionCandidate(
                    _record_string(value, "candidate_id"),
                    _record_string(value, "description"),
                    InteractionKind(_record_string(value, "kind")),
                )
            )
        return tuple(candidates)

    async def click(self, candidate_id: str) -> PageInspection:
        return _inspection(await self._request("click", {"candidate_id": candidate_id}))

    async def fill(self, candidate_id: str, value: str) -> PageInspection:
        return _inspection(
            await self._request("fill", {"candidate_id": candidate_id, "value": value})
        )

    async def extract(self, instruction: str, schema: ExtractionSchema) -> ExtractionResult:
        fields: list[dict[str, JsonValue]] = [
            {"name": field.name, "type": field.type.value, "required": field.required}
            for field in schema.fields
        ]
        result = await self._request(
            "extract", {"instruction": instruction, "schema": cast(JsonValue, fields)}
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise BrowserWorkerError("worker returned invalid extraction data")
        return ExtractionResult(cast(Mapping[str, JsonValue], data))

    async def list_tabs(self) -> tuple[TabInfo, ...]:
        result = await self._request("tabs")
        values = result.get("tabs")
        if not isinstance(values, list):
            raise BrowserWorkerError("worker returned invalid tab data")
        return tuple(_tab(value) for value in values)

    async def new_tab(self, url: str) -> TabInfo:
        return _tab(await self._request("new_tab", {"url": url}))

    async def switch_tab(self, tab_id: str) -> TabInfo:
        return _tab(await self._request("switch_tab", {"tab_id": tab_id}))

    async def close_tab(self, tab_id: str) -> None:
        await self._request("close_tab", {"tab_id": tab_id})

    async def download(self, candidate_id: str, *, timeout_seconds: float = 10) -> ArtifactRef:
        return _artifact(
            await self._request(
                "download", {"candidate_id": candidate_id, "timeout_seconds": timeout_seconds}
            )
        )

    async def pause(self) -> None:
        await self._request("pause")
        self._state = BrowserSessionState.PAUSED

    async def resume(self) -> None:
        await self._request("resume")
        self._state = BrowserSessionState.ACTIVE

    async def close(self) -> None:
        if self._finished:
            return
        try:
            await self._request("close")
            self._state = BrowserSessionState.CLOSED
        finally:
            await self._finish("browser_worker_exited", "closed")

    async def revoke(self) -> None:
        if self._finished:
            return
        changed = self._lease.revoke("explicit revocation")
        try:
            try:
                await self._request_without_lease("revoke")
            except (BrowserSessionError, ConnectionError, OSError):
                pass
            self._state = BrowserSessionState.REVOKED
        finally:
            if changed:
                self._event_sink(
                    "browser_session_revoked",
                    {"session_id": self.session_id, "reason": "explicit revocation"},
                )
            await self._finish("browser_worker_exited", "revoked")

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
        return await self._request("handoff", _handoff_parameters(request))

    async def handoff_for_task(
        self, task: TaskSpec, request: HumanHandoff
    ) -> Mapping[str, JsonValue]:
        return await self._request(
            "handoff_for_task",
            {
                **_handoff_parameters(request),
                "task_id": task.task_id,
                "task_objective": task.objective,
            },
        )

    async def observe_state(self) -> Mapping[str, JsonValue]:
        return await self._request("observe_state")

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
        if proposal.kind is not ActionKind.BROWSER:
            raise BrowserContractError("browser runtime only accepts browser proposals")
        operation = proposal.parameters.get("operation")
        if not isinstance(operation, str):
            raise BrowserContractError("operation must be a string")
        return await self._request(
            operation,
            {key: value for key, value in proposal.parameters.items() if key != "operation"},
        )

    async def _request(
        self,
        operation: str,
        parameters: Mapping[str, JsonValue] | None = None,
    ) -> Mapping[str, JsonValue]:
        if operation in {"handoff", "handoff_for_task"}:
            self._lease.begin_takeover()
        else:
            self._lease.touch()
        denials_before = self._gate.denial_count
        result = await self._request_without_lease(operation, parameters)
        if self._gate.denial_count != denials_before:
            reason = self._gate.last_denial_reason or "egress gate denied navigation"
            await self.revoke()
            raise NavigationPolicyError(reason)
        return result

    async def _request_without_lease(
        self,
        operation: str,
        parameters: Mapping[str, JsonValue] | None = None,
    ) -> Mapping[str, JsonValue]:
        if self._finished:
            raise BrowserWorkerError("browser worker is no longer available")
        async with self._lock:
            if self._process.poll() is not None:
                await self._finish("browser_worker_exited", "unexpected exit")
                raise BrowserWorkerError("browser worker exited unexpectedly")
            self._request_id += 1
            await _write_frame(
                self._writer,
                {
                    "version": _PROTOCOL_VERSION,
                    "request_id": self._request_id,
                    "operation": operation,
                    "parameters": dict(parameters or {}),
                },
            )
            try:
                response = await _read_frame(self._reader)
            except (asyncio.IncompleteReadError, ConnectionError) as error:
                await self._finish("browser_worker_exited", "control channel closed")
                raise BrowserWorkerError("browser worker control channel closed") from error
            if response.get("request_id") != self._request_id:
                await self._finish("browser_worker_exited", "protocol mismatch")
                raise BrowserWorkerError("browser worker response was out of order")
            if response.get("ok") is not True:
                message = str(response.get("error", "browser worker rejected the request"))
                if response.get("error_type") == "NavigationPolicyError":
                    self._lease.revoke("navigation policy denial")
                    self._state = BrowserSessionState.REVOKED
                    self._event_sink(
                        "browser_session_revoked",
                        {"session_id": self.session_id, "reason": "navigation policy denial"},
                    )
                    await self._finish("browser_worker_exited", "navigation policy denial")
                    raise NavigationPolicyError(message)
                raise BrowserWorkerError(message)
            result = response.get("result")
            if not isinstance(result, dict):
                raise BrowserWorkerError("browser worker returned an invalid result")
            return cast(Mapping[str, JsonValue], result)

    async def _watch_expiry(self) -> None:
        while not self._finished:
            await asyncio.sleep(self._lease.remaining_seconds)
            try:
                self._lease.check()
            except SessionLeaseError:
                self._lease.revoke("lease expired")
                self._event_sink(
                    "browser_session_expired",
                    {"session_id": self.session_id, "reason": "lease expired"},
                )
                try:
                    await self._request_without_lease("revoke")
                except BrowserSessionError:
                    pass
                self._state = BrowserSessionState.REVOKED
                await self._finish("browser_worker_exited", "expired")
                return

    async def _finish(self, kind: str, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        current = asyncio.current_task()
        if self._watchdog is not current:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        await self._gate.close()
        if self._process.poll() is None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._process.wait),
                    timeout=_PROCESS_EXIT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                self._process.terminate()
                await asyncio.to_thread(self._process.wait)
        self._event_sink(
            kind,
            {
                "session_id": self.session_id,
                "reason": reason,
                "returncode": self._process.returncode,
            },
        )


def _config_record(config: BrowserSessionConfig) -> dict[str, object]:
    return {
        "workspace": str(config.workspace),
        "allowed_origins": sorted(config.allowed_origins),
        "headless": config.headless,
        "max_inspection_chars": config.max_inspection_chars,
        "max_fill_chars": config.max_fill_chars,
        "max_download_bytes": config.max_download_bytes,
    }


def _spawn_worker(fd: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ubc_node.supervised_browser",
            "--worker-fd",
            str(fd),
        ],
        pass_fds=(fd,),
        close_fds=True,
    )


def _config_from_record(value: object) -> BrowserSessionConfig:
    if not isinstance(value, dict):
        raise BrowserWorkerError("worker configuration must be an object")
    record = cast(Mapping[str, object], value)
    origins = record.get("allowed_origins")
    if not isinstance(origins, list):
        raise BrowserWorkerError("worker allowed_origins must be strings")
    origin_values = cast(list[object], origins)
    if not all(isinstance(item, str) for item in origin_values):
        raise BrowserWorkerError("worker allowed_origins must be strings")
    return BrowserSessionConfig(
        workspace=Path(_record_string(record, "workspace")),
        allowed_origins=frozenset(cast(list[str], origin_values)),
        headless=_record_boolean(record, "headless"),
        max_inspection_chars=_record_integer(record, "max_inspection_chars"),
        max_fill_chars=_record_integer(record, "max_fill_chars"),
        max_download_bytes=_record_integer(record, "max_download_bytes"),
    )


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return cast(tuple[str, int], candidate.getsockname())[1]


async def _worker(fd: int) -> None:  # pragma: no cover - exercised by opt-in Chromium test
    control_socket = socket.socket(fileno=fd)
    reader, writer = await asyncio.open_connection(sock=control_socket)
    profile_path: Path | None = None
    browser: StagehandBrowserAdapter | None = None
    try:
        init = await _read_frame(reader)
        if init.get("version") != _PROTOCOL_VERSION:
            raise BrowserWorkerError("unsupported worker protocol version")
        session_id = _record_string(init, "session_id")
        config = _config_from_record(init.get("config"))
        lease_value = init.get("lease")
        if not isinstance(lease_value, dict):
            raise BrowserWorkerError("worker lease configuration must be an object")
        lease_record = cast(Mapping[str, object], lease_value)
        lease = SessionLease(
            LeaseConfig(
                idle_timeout_seconds=_record_number(lease_record, "idle_timeout_seconds"),
                absolute_timeout_seconds=_record_number(lease_record, "absolute_timeout_seconds"),
                takeover_timeout_seconds=_record_number(lease_record, "takeover_timeout_seconds"),
            )
        )
        profile_manager = TemporaryProfileManager(config.workspace.parent / "supervised-profiles")
        profile_manager.scavenge()
        profile_path = profile_manager.create(session_id)
        chrome_value = init.get("chrome_path")
        if chrome_value is not None and not isinstance(chrome_value, str):
            raise BrowserWorkerError("chrome_path must be a string or null")
        debugging_port = _available_loopback_port()
        browser = await StagehandBrowserAdapter.launch_local(
            config,
            chrome_path=Path(chrome_value) if isinstance(chrome_value, str) else None,
            takeover_handler=(
                CliTakeoverHandler() if init.get("takeover_enabled") is True else None
            ),
            proxy_server=_record_string(init, "proxy_server"),
            proxy_bypass=(
                f"<-loopback>;http://127.0.0.1:{debugging_port};ws://127.0.0.1:{debugging_port}"
            ),
            temporary_profile_path=profile_path,
            debugging_port=debugging_port,
        )
        await _write_frame(writer, {"version": _PROTOCOL_VERSION, "status": "ready"})
        await BrowserWorkerService(browser, lease).serve(reader, writer)
    except Exception as error:
        if browser is not None:
            await browser.revoke()
        if not writer.is_closing():
            await _write_frame(
                writer,
                {
                    "version": _PROTOCOL_VERSION,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                },
            )
            writer.close()
            await writer.wait_closed()
    finally:
        if profile_path is not None:
            TemporaryProfileManager.remove(profile_path)


def _record_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise BrowserWorkerError(f"worker field {key} must be a string")
    return item


def _record_boolean(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise BrowserWorkerError(f"worker field {key} must be a boolean")
    return item


def _record_integer(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise BrowserWorkerError(f"worker field {key} must be an integer")
    return item


def _record_number(value: Mapping[str, object], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise BrowserWorkerError(f"worker field {key} must be a number")
    return float(item)


def _inspection(value: Mapping[str, JsonValue]) -> PageInspection:
    return PageInspection(
        _record_string(cast(Mapping[str, object], value), "tab_id"),
        _record_string(cast(Mapping[str, object], value), "url"),
        _record_string(cast(Mapping[str, object], value), "title"),
        _record_string(cast(Mapping[str, object], value), "content"),
        _record_boolean(cast(Mapping[str, object], value), "content_truncated"),
        _record_boolean(cast(Mapping[str, object], value), "untrusted"),
    )


def _artifact(value: Mapping[str, JsonValue]) -> ArtifactRef:
    record = cast(Mapping[str, object], value)
    return ArtifactRef(
        _record_string(record, "relative_path"),
        _record_integer(record, "size_bytes"),
        _record_string(record, "sha256"),
    )


def _tab(value: object) -> TabInfo:
    if not isinstance(value, dict):
        raise BrowserWorkerError("worker returned an invalid tab")
    record = cast(Mapping[str, object], value)
    return TabInfo(
        _record_string(record, "tab_id"),
        _record_string(record, "url"),
        _record_string(record, "title"),
        _record_boolean(record, "active"),
    )


def _handoff_parameters(request: HumanHandoff) -> dict[str, JsonValue]:
    return {
        "reason": request.reason,
        "origin": request.origin,
        "instructions": request.instructions,
    }


def _parse_worker_fd(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker-fd", type=int, required=True)
    return cast(int, parser.parse_args(argv).worker_fd)


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    asyncio.run(_worker(_parse_worker_fd()))
