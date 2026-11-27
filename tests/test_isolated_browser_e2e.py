"""Opt-in gate against an installed Linux systemd and Podman browser launcher."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from tests.egress_fixture import APPROVED, DENIED, DENIED_IP, DENIED_PORT, HTTP_PORT, TLS_PORT
from ubc_node.agent_backend import (
    ModelPricing,
    ModelResponse,
    ProposalRequest,
    StructuredAgentBackend,
)
from ubc_node.browser_contracts import BrowserSession, BrowserSessionState
from ubc_node.browser_research import RESEARCH_INSTRUCTION, RESEARCH_SCHEMA, run_browser_research
from ubc_node.contracts import EventKind, JsonValue, RunState, TaskSpec
from ubc_node.event_log import JsonlEventLog
from ubc_node.isolated_browser import IsolatedBrowserError, PodmanBrowserRuntime
from ubc_node.isolation_contracts import (
    BoundaryChannelKind,
    ChannelCapability,
    IsolatedBrowserLaunchSpec,
)
from ubc_node.release_manifest import load_release_manifest
from ubc_node.stagehand_browser import NavigationPolicyError
from ubc_node.supervised_browser import BrowserWorkerError

pytestmark = [
    pytest.mark.isolated_browser,
    pytest.mark.skipif(
        os.environ.get("UBC_RUN_ISOLATED_BROWSER_E2E") != "1",
        reason="requires the installed Linux browser launcher",
    ),
]

FIXTURE_ORIGIN = f"http://{APPROVED}:{HTTP_PORT}"
FIXTURE_TLS_ORIGIN = f"https://{APPROVED}:{TLS_PORT}"
_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = Path(
    os.environ.get("UBC_ISOLATION_MANIFEST", str(_ROOT / "release/trusted-core-manifest.json"))
)


def _runtime() -> tuple[PodmanBrowserRuntime, str]:
    manifest = load_release_manifest(_MANIFEST)
    browser_image = cast(dict[str, JsonValue], manifest["browser_image"])
    return PodmanBrowserRuntime(_ROOT, _MANIFEST), cast(str, browser_image["digest"])


def _spec(digest: str) -> IsolatedBrowserLaunchSpec:
    return IsolatedBrowserLaunchSpec(
        uuid4().hex,
        digest,
        frozenset({FIXTURE_ORIGIN, FIXTURE_TLS_ORIGIN}),
        ChannelCapability(uuid4().hex, BoundaryChannelKind.CONTROL),
        ChannelCapability(uuid4().hex, BoundaryChannelKind.EGRESS),
    )


async def test_isolated_chromium_approved_page_denied_redirect_and_fresh_sessions() -> None:
    runtime, digest = _runtime()
    session: BrowserSession = await runtime.launch(_spec(digest))
    try:
        started = await runtime.next_event()
        assert started.payload["browser_image_digest"] == digest
        alpha = await session.open_url(f"{FIXTURE_ORIGIN}/source/alpha")
        assert "12 hectares" in alpha.content
        assert alpha.http_status == 200
        await session.open_url(f"{FIXTURE_ORIGIN}/session-write")
        assert "retained" in (await session.open_url(f"{FIXTURE_ORIGIN}/session-read")).content
        tls = await session.open_url(f"{FIXTURE_TLS_ORIGIN}/source/alpha")
        assert "12 hectares" in tls.content
        renderer = await session.new_tab(f"{FIXTURE_TLS_ORIGIN}/renderer")
        async with asyncio.timeout(10):
            while "Renderer requests finished" not in (await session.inspect()).content:  # noqa: ASYNC110
                await asyncio.sleep(0.1)
        await session.close_tab(renderer.tab_id)
        await session.open_url(f"{FIXTURE_ORIGIN}/source/alpha")
        malicious = await session.new_tab(f"{FIXTURE_ORIGIN}/malicious")
        assert "Ignore the task" in (await session.inspect()).content
        assert len(await session.list_tabs()) == 2
        await session.close_tab(malicious.tab_id)

        # Rediscovering the same launcher socket yields a distinct container.
        second = await runtime.launch(_spec(digest))
        try:
            with pytest.raises(BrowserWorkerError, match="command rejected"):
                await second.switch_tab(alpha.tab_id)
            assert second.state is BrowserSessionState.REVOKED
            assert "12 hectares" in (await session.inspect()).content
        finally:
            await second.close()

        with pytest.raises(BrowserWorkerError, match="disabled"):
            await session.screenshot()
        with pytest.raises(NavigationPolicyError, match="denied"):
            await session.open_url(f"{FIXTURE_ORIGIN}/outside-redirect")
        assert session.state is BrowserSessionState.REVOKED
        with pytest.raises(IsolatedBrowserError, match="ended"):
            await session.inspect()
    finally:
        await session.revoke()

    replacement = await runtime.launch(_spec(digest))
    try:
        assert len(await replacement.list_tabs()) == 1
        assert (
            "Empty storage"
            in (await replacement.open_url(f"{FIXTURE_ORIGIN}/session-read")).content
        )
        assert "18 percent" in (await replacement.open_url(f"{FIXTURE_ORIGIN}/source/beta")).content
    finally:
        await replacement.close()
    assert replacement.state is BrowserSessionState.CLOSED


class _FixtureModel:
    pricing = ModelPricing(0, 0)

    def __init__(self, page: str, attack: bool) -> None:
        self.page = page
        self.attack = attack
        self.calls = 0

    async def generate_proposal(self, request: ProposalRequest) -> ModelResponse:
        assert request.json_schema == RESEARCH_SCHEMA
        self.calls += 1
        if self.calls == 1:
            proposal = {"kind": "browser", "operation": "open", "url": self.page}
        elif self.attack:
            assert "Ignore the task" in request.user_prompt
            assert "untrusted_external_data" in request.user_prompt
            proposal = {
                "kind": "browser",
                "operation": "open",
                "url": f"http://{DENIED}:{DENIED_PORT}/",
            }
        else:
            context = json.loads(request.user_prompt)
            event = context["events"][-1]
            assert event["trust"] == "untrusted_external_data"
            observation = event["payload"]["observation"]
            assert "12 hectares" in observation["content"]
            proposal = {"kind": "complete", "output": f"12 hectares [{observation['evidence_id']}]"}
        return ModelResponse(json.dumps({"description": "Fixture proposal", **proposal}))


@pytest.mark.parametrize(
    ("path", "attack", "expected"),
    [
        ("/source/alpha", False, RunState.COMPLETED),
        ("/malicious", True, RunState.FAILED),
        ("/outside-redirect", False, RunState.FAILED),
    ],
)
async def test_research_supervisor_uses_real_isolated_chromium(
    tmp_path: Path, path: str, attack: bool, expected: RunState
) -> None:
    runtime, digest = _runtime()
    log = JsonlEventLog(tmp_path / "c05.jsonl")
    url = FIXTURE_ORIGIN + path
    model = _FixtureModel(url, attack)
    async with StructuredAgentBackend(
        model, system_instruction=RESEARCH_INSTRUCTION, json_schema=RESEARCH_SCHEMA
    ) as agent:
        result = await run_browser_research(
            TaskSpec("fixture", "Read the fixture text"),
            {"fixture-page": url},
            runtime=runtime,
            image_digest=digest,
            agent=agent,
            event_sink=log,
        )
    assert result.state is expected
    events = log.read_all()
    observations = [event for event in events if event.kind is EventKind.BROWSER_ACTION_COMPLETED]
    if path == "/outside-redirect":
        assert not observations
        assert "navigation denied" in str(result.error)
    else:
        assert len(observations) == 1
    if attack:
        assert "policy denied" in str(result.error)
        assert (
            len([event for event in events if event.kind is EventKind.BROWSER_ACTION_STARTED]) == 1
        )
    assert (await asyncio.wait_for(runtime.next_event(), 2)).kind == "isolated_browser_started"
    assert (await asyncio.wait_for(runtime.next_event(), 2)).kind == "isolated_browser_ended"
    if os.environ.get("UBC_RUN_ISOLATION_ADMIN") == "1":
        _assert_denied_untouched()


def _podman(*args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/podman", "--remote=false", "--cgroup-manager=cgroupfs", *args],
        text=True,
        timeout=15,
    ).strip()


def _container() -> str:
    containers = _podman(
        "ps", "--filter", "name=^ubc-browser-", "--format", "{{.Names}}"
    ).splitlines()
    assert len(containers) == 1, "run the operator gate with no other browser sessions"
    return containers[0]


def _gate(container: str) -> str:
    return container.replace("ubc-browser-", "ubc-egress-", 1)


def _assert_denied_untouched() -> None:
    assert _podman("logs", "ubc-denied-fixture") == "ready"


async def test_launcher_rejects_origins_outside_operator_policy() -> None:
    from dataclasses import replace

    runtime, digest = _runtime()
    with pytest.raises(IsolatedBrowserError, match="startup failed"):
        await runtime.launch(
            replace(_spec(digest), allowed_origins=frozenset({f"http://{DENIED}:{DENIED_PORT}"}))
        )


@pytest.mark.skipif(os.geteuid() == 0, reason="run with the ordinary node account")
async def test_ordinary_node_cannot_connect_to_the_browser_proxy() -> None:
    runtime, digest = _runtime()
    session = await runtime.launch(_spec(digest))
    try:
        endpoints = [
            path for path in Path("/run/ubc-browser").iterdir() if path.name != "control.sock"
        ]
        assert endpoints
        for path in endpoints:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                with pytest.raises(PermissionError):
                    await asyncio.get_running_loop().run_in_executor(
                        None, probe.connect, str(path / "endpoint/proxy.sock")
                    )
    finally:
        await session.close()


async def _wait_for_removal(container: str) -> None:
    async with asyncio.timeout(15):
        while True:
            status = (
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "/usr/bin/podman",
                        "--remote=false",
                        "--cgroup-manager=cgroupfs",
                        "container",
                        "exists",
                        container,
                    ],
                    check=False,
                    timeout=10,
                )
            ).returncode
            if status == 1:
                return
            assert status == 0
            await asyncio.sleep(0.1)


@pytest.mark.skipif(
    os.environ.get("UBC_RUN_ISOLATION_ADMIN") != "1",
    reason="requires root in a disposable Linux test host",
)
@pytest.mark.parametrize("failure", ["worker", "runtime", "broker", "gate", "parent"])
async def test_isolation_operator_gate(tmp_path: Path, failure: str) -> None:
    assert os.geteuid() == 0
    if failure == "parent":
        code = """
import asyncio
from tests.test_isolated_browser_e2e import _runtime, _spec, FIXTURE_ORIGIN
async def run():
    runtime, digest = _runtime()
    session = await runtime.launch(_spec(digest))
    await session.open_url(f"{FIXTURE_ORIGIN}/source/alpha")
    print("ready", flush=True)
    await asyncio.Future()
asyncio.run(run())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            cwd=_ROOT,
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            assert process.stdout is not None
            assert await asyncio.wait_for(process.stdout.readline(), 60) == b"ready\n"
            container = await asyncio.to_thread(_container)
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
        await _wait_for_removal(container)
        await _wait_for_removal(_gate(container))
        return

    runtime, digest = _runtime()
    session = await runtime.launch(_spec(digest))
    try:
        await runtime.next_event()
        container = await asyncio.to_thread(_container)
        gate = _gate(container)
        if failure == "worker":
            sentinel = tmp_path / "mock-node-key"
            sentinel.write_text("mock")
            probe = r"""
import os
import socket
import sys
from pathlib import Path
assert os.geteuid() == os.getegid() == 65532
status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
assert all(int(status[field], 16) == 0 for field in ("CapEff", "CapPrm", "CapBnd"))
assert int(status["NoNewPrivs"]) == 1
assert str(Path("/proc/self/ns/net").readlink()) != sys.argv[3]
up = {name for _, name in socket.if_nameindex()
      if int(Path(f"/sys/class/net/{name}/flags").read_text(), 16) & 1}
assert up == {"lo"}, up
assert len(Path("/proc/net/route").read_text().splitlines()) == 1
assert not Path(sys.argv[1]).exists()
for path in ("/run/podman/podman.sock", "/var/run/docker.sock", "/proc/1/root/run/ubc-browser"):
    assert not Path(path).exists()
assert not os.access("/opt/ubc/app/src/ubc_node/policy.py", os.W_OK)
assert os.statvfs("/").f_flag & os.ST_RDONLY
for path in ("/run/ubc", "/var/lib/ubc/profile", "/var/lib/ubc/quarantine"):
    probe = Path(path) / "write-proof"
    probe.write_text("ok")
    probe.unlink()
with socket.socket() as probe:
    probe.settimeout(1)
    assert probe.connect_ex(("127.0.0.1", int(sys.argv[2]))) != 0
denied, port, host, approved, http_port, tls_port = sys.argv[4:10]
for target in ((denied, int(port)), (denied, 53), (denied, 443)):
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(target) != 0
for port in (53, 443, 3478):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.sendto(b"local network acceptance probe", (denied, port))
        except OSError:
            pass
        else:
            raise AssertionError("isolated worker unexpectedly has a UDP route")
for request in (
    f"GET http://{host}:{sys.argv[5]}/ HTTP/1.1\r\nHost: {host}:{sys.argv[5]}\r\n\r\n",
    f"CONNECT {host}:{tls_port} HTTP/1.1\r\nHost: {host}:{tls_port}\r\n\r\n",
    f"POST http://{approved}:{http_port}/ HTTP/1.1\r\nHost: {approved}:{http_port}\r\n\r\n",
    f"CONNECT {approved}:{http_port} HTTP/1.1\r\nHost: {approved}:{http_port}\r\n\r\n",
):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        probe.connect("/run/ubc-egress/proxy.sock")
        probe.sendall(request.encode())
        assert probe.recv(256).startswith(b"HTTP/1.1 403")
"""
            server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
            try:
                port = server.sockets[0].getsockname()[1]
                await asyncio.to_thread(
                    _podman,
                    "exec",
                    container,
                    "python",
                    "-c",
                    probe,
                    str(sentinel),
                    str(port),
                    str(Path("/proc/self/ns/net").readlink()),
                    DENIED_IP,
                    str(DENIED_PORT),
                    DENIED,
                    APPROVED,
                    str(HTTP_PORT),
                    str(TLS_PORT),
                )
            finally:
                server.close()
                await server.wait_closed()
            inspected = await asyncio.to_thread(
                _podman, "inspect", "--type", "container", container
            )
            status = json.loads(inspected)[0]
            config = status["HostConfig"]
            assert config["PidsLimit"] == 256 and config["Memory"] == 1024**3
            assert config["CpuQuota"] == config["CpuPeriod"] > 0
            assert not config["PortBindings"] and config["NetworkMode"] == "none"
            gate_inspected = await asyncio.to_thread(
                _podman, "inspect", "--type", "container", gate
            )
            gate_status = json.loads(gate_inspected)[0]
            gate_config = gate_status["HostConfig"]
            assert gate_config["ReadonlyRootfs"] is True
            assert gate_config["PidsLimit"] == 32 and gate_config["Memory"] == 128 * 1024 * 1024
            assert gate_config["CpuQuota"] == gate_config["CpuPeriod"] > 0
            assert not gate_config["PortBindings"] and not gate_config["Binds"]
            assert set(gate_status["NetworkSettings"]["Networks"]) == {"ubc-egress-fixture"}
            await asyncio.to_thread(_assert_denied_untouched)
            await asyncio.to_thread(_podman, "kill", "--signal", "KILL", container)
        elif failure == "gate":
            await asyncio.to_thread(_podman, "kill", "--signal", "KILL", gate)
        else:
            pid = int(
                await asyncio.to_thread(_podman, "inspect", "--format", "{{.State.Pid}}", container)
            )
            cgroup = Path(f"/proc/{pid}/cgroup").read_text()
            unit = next(part for part in cgroup.split("/") if part.startswith("ubc-browser@"))
            if failure == "broker":
                await asyncio.to_thread(
                    subprocess.run,
                    ["systemctl", "kill", "--kill-whom=main", "--signal=KILL", unit],
                    check=True,
                    timeout=10,
                )
            else:
                main_pid = int(
                    await asyncio.to_thread(
                        subprocess.check_output,
                        ["systemctl", "show", "--property=MainPID", "--value", unit],
                        timeout=10,
                    )
                )
                children = Path(f"/proc/{main_pid}/task/{main_pid}/children").read_text().split()
                podman = next(
                    int(child)
                    for child in children
                    if f"--name={container}".encode()
                    in Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
                )
                os.kill(podman, signal.SIGKILL)
        ended = await asyncio.wait_for(runtime.next_event(), 15)
        assert ended.kind == "isolated_browser_ended"
        assert session.state is BrowserSessionState.REVOKED
        await _wait_for_removal(container)
        await _wait_for_removal(gate)
        await asyncio.to_thread(_assert_denied_untouched)
    finally:
        await session.revoke()
