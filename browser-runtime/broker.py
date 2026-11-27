"""Root-owned systemd socket-activated launcher. Requires only the standard library."""

import asyncio
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path

PODMAN = ("/usr/bin/podman", "--remote=false", "--cgroup-manager=cgroupfs")
TMPFS = (
    "/run/ubc:rw,nosuid,nodev,noexec,U,mode=0700,size=128m",
    "/var/lib/ubc/profile:rw,nosuid,nodev,noexec,U,mode=0700,size=256m",
    "/var/lib/ubc/quarantine:rw,nosuid,nodev,noexec,U,mode=0700,size=64m",
)
STARTUP_LIMIT = 16 * 1024
EGRESS_POLICY_PATH = Path("/etc/ubc-browser/egress.json")


def container_name(invocation_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", invocation_id):
        raise ValueError("invalid systemd invocation ID")
    return f"ubc-browser-{invocation_id}"


def image_digest(image: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("UBC_BROWSER_IMAGE requires a repository and an immutable OCI digest")
    return image.split("@", 1)[1]


def _container_options(name: str, cgroup: str) -> tuple[str, ...]:
    return (
        *PODMAN,
        "run",
        f"--name={name}",
        "--pull=never",
        "--rm",
        "--attach=stdout",
        "--sig-proxy=false",
        "--runtime=crun",
        "--userns=auto:size=65536",
        "--user=65532:65532",
        "--pid=private",
        "--ipc=private",
        "--http-proxy=false",
        "--no-hosts",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--read-only-tmpfs=false",
        "--image-volume=ignore",
        "--log-driver=none",
        f"--cgroup-parent={cgroup}",
        "--timeout=1800",
    )


def podman_command(
    name: str, image: str, child_fd: int, cgroup: str, endpoint: Path
) -> tuple[str, ...]:
    return (
        *_container_options(name, cgroup),
        f"--preserve-fd={child_fd}",
        "--network=none",
        f"--volume={endpoint}:/run/ubc-egress:ro",
        "--memory=1073741824",
        "--memory-swap=1073741824",
        "--cpus=1",
        "--pids-limit=256",
        *(f"--tmpfs={mount}" for mount in TMPFS),
        "--",
        image,
        str(child_fd),
    )


def _gate_name(name: str) -> str:
    return name.replace("ubc-browser-", "ubc-egress-", 1)


def _gate_command(
    name: str,
    image: str,
    listener: int,
    channel: int,
    cgroup: str,
    network: str,
) -> tuple[str, ...]:
    return (
        *_container_options(_gate_name(name), cgroup),
        f"--preserve-fd={listener},{channel}",
        f"--network={network}",
        "--memory=134217728",
        "--memory-swap=134217728",
        "--cpus=1",
        "--pids-limit=32",
        '--entrypoint=["python","-m","ubc_node.browser.isolation.isolated_egress"]',
        "--",
        image,
        str(listener),
        str(channel),
    )


def _session_directory(name: str) -> Path:
    return Path("/run/ubc-browser") / name.removeprefix("ubc-browser-")


def _frame(message: dict[str, object]) -> bytes:
    data = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
    return struct.pack("!I", len(data)) + data


async def _startup(reader: asyncio.StreamReader) -> dict[str, object]:
    (size,) = struct.unpack("!I", await reader.readexactly(4))
    if not 2 <= size <= STARTUP_LIMIT:
        raise ValueError("invalid browser startup size")
    message = json.loads(await reader.readexactly(size))
    if (
        not isinstance(message, dict)
        or set(message) != {"version", "session_id", "origins"}
        or type(message["version"]) is not int
        or message["version"] != 1
        or not isinstance(message["session_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", message["session_id"])
        or not isinstance(message["origins"], list)
        or not 1 <= len(message["origins"]) <= 32
        or not all(isinstance(origin, str) and len(origin) <= 270 for origin in message["origins"])
    ):
        raise ValueError("invalid browser startup request")
    return message


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()


async def _client_disconnected(client: socket.socket) -> None:
    # Detect a closed peer even while a saturated relay is waiting for a write.
    poller = select.poll()
    poller.register(client, select.POLLRDHUP)
    while not poller.poll(0):  # noqa: ASYNC110 - Linux hangup flags are independent of readability.
        await asyncio.sleep(0.1)


async def _serve(
    client: socket.socket, image: str, release_id: str, name: str, network: str, policy: object
) -> None:
    membership = Path("/proc/self/cgroup").read_text().strip()
    if (
        not membership.startswith("0::/")
        or not membership.endswith("/broker")
        or "\n" in membership
    ):
        raise RuntimeError("the browser launcher requires a delegated cgroup v2 service")
    cgroup = str(Path(membership.removeprefix("0::")).parent)

    client_reader, client_writer = await asyncio.open_connection(sock=client)
    try:
        client_writer.write(
            _frame(
                {
                    "version": 1,
                    "status": "broker",
                    "egress": "unix",
                    "image_digest": image_digest(image),
                    "release_id": release_id,
                }
            )
        )
        await client_writer.drain()
        async with asyncio.timeout(30):
            init = await _startup(client_reader)
        await _launch(
            client, client_reader, client_writer, image, name, cgroup, network, policy, init
        )
    finally:
        client_writer.close()


async def _launch(
    client: socket.socket,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    image: str,
    name: str,
    cgroup: str,
    network: str,
    policy: object,
    init: dict[str, object],
) -> None:
    directory = _session_directory(name)
    directory.mkdir(mode=0o700)
    endpoint = directory / "endpoint"
    endpoint.mkdir()
    endpoint.chmod(0o711)
    gate, child = socket.socketpair()
    with gate, child, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        path = endpoint / "proxy.sock"
        listener.bind(str(path))
        # The host path is behind a root-only directory. Only this session's
        # browser can use the mounted path with its private mapped UID.
        path.chmod(0o666)
        listener.listen(32)
        gate_process = await asyncio.create_subprocess_exec(
            *_gate_command(name, image, listener.fileno(), child.fileno(), cgroup, network),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            pass_fds=(listener.fileno(), child.fileno()),
        )
        child.close()
        listener.close()
        gate_reader, gate_writer = await asyncio.open_connection(sock=gate)
        try:
            async with asyncio.timeout(25):
                gate_writer.write(_frame({"policy": policy, "origins": init["origins"]}))
                await gate_writer.drain()
                if await gate_reader.readexactly(6) != b"ready\n":
                    raise RuntimeError("egress gate did not start")
            worker, worker_child = socket.socketpair()
            with worker, worker_child:
                process = await asyncio.create_subprocess_exec(
                    *podman_command(name, image, worker_child.fileno(), cgroup, endpoint),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    pass_fds=(worker_child.fileno(),),
                )
                worker_child.close()
                worker_reader, worker_writer = await asyncio.open_connection(sock=worker)
                try:
                    worker_writer.write(_frame(init))
                    await worker_writer.drain()
                    tasks = [
                        asyncio.create_task(_relay(client_reader, worker_writer)),
                        asyncio.create_task(_relay(worker_reader, client_writer)),
                        asyncio.create_task(_client_disconnected(client)),
                        asyncio.create_task(gate_reader.read(1)),
                        asyncio.create_task(gate_process.wait()),
                    ]
                    process_exit = asyncio.create_task(process.wait())
                    try:
                        done, _ = await asyncio.wait(
                            [*tasks, process_exit], return_when=asyncio.FIRST_COMPLETED
                        )
                        if done == {process_exit}:
                            # A final worker reply can still be buffered after exit.
                            await asyncio.wait({tasks[1]}, timeout=0.25)
                    finally:
                        for task in [*tasks, process_exit]:
                            task.cancel()
                        await asyncio.gather(*tasks, process_exit, return_exceptions=True)
                finally:
                    worker_writer.transport.abort()
        finally:
            gate_writer.transport.abort()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "cleanup"}:
        raise ValueError("broker requires a single 'run' or 'cleanup' subcommand")
    name = container_name(os.environ["INVOCATION_ID"])
    if sys.argv[1] == "cleanup":
        try:
            subprocess.run(
                [*PODMAN, "rm", "--force", "--time=0", "--ignore", _gate_name(name), name],
                check=True,
            )
        finally:
            directory = _session_directory(name)
            if directory.exists():
                shutil.rmtree(directory)
        return

    image = os.environ["UBC_BROWSER_IMAGE"]
    image_digest(image)
    release_id = os.environ["UBC_BROWSER_RELEASE"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", release_id):
        raise ValueError("invalid browser release ID")
    network = os.environ["UBC_EGRESS_NETWORK"]
    if not re.fullmatch(r"ubc-egress-[a-z0-9-]{1,40}", network):
        raise ValueError("UBC_EGRESS_NETWORK must name a dedicated ubc-egress-* network")
    with EGRESS_POLICY_PATH.open("rb") as config:
        policy_data = config.read(STARTUP_LIMIT + 1)
    if len(policy_data) > STARTUP_LIMIT:
        raise ValueError("egress policy exceeds the configuration limit")
    policy = json.loads(policy_data)
    # Keep the accepted socket on its own descriptor before running Podman.
    with socket.fromfd(0, socket.AF_UNIX, socket.SOCK_STREAM) as client:
        with open(os.devnull, "rb") as null:
            os.dup2(null.fileno(), 0)
        asyncio.run(_serve(client, image, release_id, name, network, policy))


if __name__ == "__main__":
    main()
