"""Opt-in gate against an installed Linux systemd and Podman browser launcher."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from ubc_node.browser_contracts import BrowserSession, BrowserSessionState
from ubc_node.contracts import JsonValue
from ubc_node.isolated_browser import FIXTURE_ORIGIN, IsolatedBrowserError, PodmanBrowserRuntime
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
        frozenset({FIXTURE_ORIGIN}),
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
        assert "18 percent" in (await replacement.open_url(f"{FIXTURE_ORIGIN}/source/beta")).content
    finally:
        await replacement.close()
    assert replacement.state is BrowserSessionState.CLOSED


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
@pytest.mark.parametrize("failure", ["worker", "runtime", "broker", "parent"])
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
        return

    runtime, digest = _runtime()
    session = await runtime.launch(_spec(digest))
    try:
        await runtime.next_event()
        container = await asyncio.to_thread(_container)
        if failure == "worker":
            sentinel = tmp_path / "mock-node-key"
            sentinel.write_text("mock")
            probe = """
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
            await asyncio.to_thread(_podman, "kill", "--signal", "KILL", container)
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
                    if Path(f"/proc/{child}/comm").read_text().strip() == "podman"
                )
                os.kill(podman, signal.SIGKILL)
        ended = await asyncio.wait_for(runtime.next_event(), 15)
        assert ended.kind == "isolated_browser_ended"
        assert session.state is BrowserSessionState.REVOKED
        await _wait_for_removal(container)
    finally:
        await session.revoke()
