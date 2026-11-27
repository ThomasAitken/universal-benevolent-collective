"""Local ClamAV INSTREAM fixture for quarantine and pilot acceptance."""

import os
import socket
import struct
import tempfile
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from ubc_node.quarantine import ClamAVScanner


def read_scan(channel: socket.socket) -> bytes:
    channel.settimeout(5)
    received = bytearray()
    with channel.makefile("rb") as stream:
        assert stream.read(10) == b"zINSTREAM\0"
        while True:
            header = stream.read(4)
            assert len(header) == 4
            size = struct.unpack("!I", header)[0]
            if size == 0:
                break
            chunk = stream.read(size)
            assert len(chunk) == size
            received.extend(chunk)
    return bytes(received)


@contextmanager
def clamd_listener() -> Generator[tuple[Path, socket.socket]]:
    with (
        tempfile.TemporaryDirectory(dir="/tmp", prefix="ubc-scan-") as directory,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener,
    ):
        endpoint = Path(directory) / "socket"
        listener.bind(os.fspath(endpoint))
        listener.listen()
        listener.settimeout(5)
        yield endpoint, listener


@pytest.fixture(name="clamd")
def clamd_fixture() -> Generator[tuple[Path, socket.socket]]:
    with clamd_listener() as listener:
        yield listener


@contextmanager
def scanner_for(
    clamd: tuple[Path, socket.socket], *replies: bytes, expected: bytes
) -> Generator[ClamAVScanner]:
    endpoint, listener = clamd

    def serve() -> None:
        channel, _ = listener.accept()
        with channel:
            assert read_scan(channel) == expected
            for reply in replies:
                channel.sendall(reply)

    with ThreadPoolExecutor() as executor:
        serving = executor.submit(serve)
        yield ClamAVScanner(endpoint)
        serving.result()
