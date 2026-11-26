"""Root-owned systemd socket-activated launcher. Requires only the standard library."""

import asyncio
import json
import os
import re
import select
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


def container_name(invocation_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", invocation_id):
        raise ValueError("invalid systemd invocation ID")
    return f"ubc-browser-{invocation_id}"


def image_digest(image: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("UBC_BROWSER_IMAGE requires a repository and an immutable OCI digest")
    return image.split("@", 1)[1]


def podman_command(name: str, image: str, child_fd: int, cgroup: str) -> tuple[str, ...]:
    return (
        *PODMAN,
        "run",
        f"--name={name}",
        "--pull=never",
        "--rm",
        "--attach=stdout",
        "--sig-proxy=false",
        "--runtime=crun",
        f"--preserve-fd={child_fd}",
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
        f"--cgroup-parent={cgroup}",
        "--memory=1073741824",
        "--memory-swap=1073741824",
        "--cpus=1",
        "--pids-limit=256",
        "--timeout=1800",
        *(f"--tmpfs={mount}" for mount in TMPFS),
        "--",
        image,
        str(child_fd),
    )


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


async def _serve(client: socket.socket, image: str, release_id: str, name: str) -> None:
    membership = Path("/proc/self/cgroup").read_text().strip()
    if (
        not membership.startswith("0::/")
        or not membership.endswith("/broker")
        or "\n" in membership
    ):
        raise RuntimeError("the browser launcher requires a delegated cgroup v2 service")
    cgroup = str(Path(membership.removeprefix("0::")).parent)

    client_reader, client_writer = await asyncio.open_connection(sock=client)
    worker, child = socket.socketpair()
    with worker, child:
        try:
            greeting = json.dumps(
                {
                    "version": 1,
                    "status": "broker",
                    "image_digest": image_digest(image),
                    "release_id": release_id,
                },
                separators=(",", ":"),
            ).encode()
            client_writer.write(struct.pack("!I", len(greeting)) + greeting)
            await client_writer.drain()
            process = await asyncio.create_subprocess_exec(
                *podman_command(name, image, child.fileno(), cgroup),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                pass_fds=(child.fileno(),),
            )
            child.close()
            worker_reader, worker_writer = await asyncio.open_connection(sock=worker)
            try:
                tasks = [
                    asyncio.create_task(_relay(client_reader, worker_writer)),
                    asyncio.create_task(_relay(worker_reader, client_writer)),
                    asyncio.create_task(_client_disconnected(client)),
                ]
                process_exit = asyncio.create_task(process.wait())
                try:
                    done, _ = await asyncio.wait(
                        [*tasks, process_exit], return_when=asyncio.FIRST_COMPLETED
                    )
                    if process_exit in done and tasks[1] not in done:
                        # A final worker reply can still be buffered after exit.
                        await asyncio.wait({tasks[1]}, timeout=0.25)
                finally:
                    for task in [*tasks, process_exit]:
                        task.cancel()
                    await asyncio.gather(*tasks, process_exit, return_exceptions=True)
            finally:
                worker_writer.transport.abort()
        finally:
            client_writer.close()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "cleanup"}:
        raise ValueError("broker requires a single 'run' or 'cleanup' subcommand")
    name = container_name(os.environ["INVOCATION_ID"])
    if sys.argv[1] == "cleanup":
        subprocess.run([*PODMAN, "rm", "--force", "--time=0", "--ignore", name], check=True)
        return

    image = os.environ["UBC_BROWSER_IMAGE"]
    image_digest(image)
    release_id = os.environ["UBC_BROWSER_RELEASE"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", release_id):
        raise ValueError("invalid browser release ID")
    # Keep the accepted socket on its own descriptor before running Podman.
    with socket.fromfd(0, socket.AF_UNIX, socket.SOCK_STREAM) as client:
        with open(os.devnull, "rb") as null:
            os.dup2(null.fileno(), 0)
        asyncio.run(_serve(client, image, release_id, name))


if __name__ == "__main__":
    main()
