"""Deterministic policy and transport tests using only local endpoints."""

# pyright: reportPrivateUsage=false

import asyncio
import ipaddress
import socket
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from ubc_node import isolated_egress as egress
from ubc_node import isolated_worker
from ubc_node.supervised_browser import write_worker_frame

HTTP = "http://approved.test"
HTTPS = "https://approved.test"
GET = b"GET http://approved.test/path?q=1 HTTP/1.1\r\nHost: approved.test\r\n\r\n"
CONNECT = b"CONNECT approved.test:443 HTTP/1.1\r\nHost: approved.test:443\r\n\r\n"


@pytest.mark.parametrize(
    ("requested", "ceiling"),
    [
        (None, {}),
        ([], None),
        ([], {HTTP: []}),
        ([HTTP], {}),
        ([HTTP], {1: []}),
        ([HTTP], {HTTP: None}),
        ([HTTP], {"ftp://approved.test": []}),
        ([HTTP], {HTTP: [], "HTTP://APPROVED.TEST:80": []}),
        ([HTTP], {HTTP: [None]}),
        ([HTTP], {HTTP: ["192.0.2.1/32"] * 33}),
        ([HTTP], {HTTP: ["bad-range"]}),
        ([1], {HTTP: []}),
        ([HTTPS], {HTTP: []}),
        (["http://approved.test/"], {HTTP: []}),
    ],
)
def test_invalid_policy(requested: object, ceiling: object) -> None:
    with pytest.raises(ValueError):
        egress.session_policy(requested, ceiling)


def test_policy_can_only_narrow_configured_authority() -> None:
    assert egress.session_policy(
        ["HTTPS://APPROVED.test:443"],
        {HTTP: [], "https://approved.test:443": ["192.0.2.10/32"]},
    ) == {HTTPS: (ipaddress.ip_network("192.0.2.10/32"),)}


@pytest.mark.parametrize(
    "head",
    [
        b"GET / HTTP/9\r\n\r\n",
        b"GET http://approved.test/\x00 HTTP/1.1\r\n\r\n",
        GET.replace(b"/path", b"\\path"),
        GET.replace(b"/path", b"/path#fragment"),
        GET.replace(b"/path", b"//path"),
        GET.replace(b"GET", b"POST"),
        GET.replace(b"http:", b"https:"),
        GET.replace(b"http:", b"ws:"),
        GET.replace(b"approved.test/", b"127.1/"),
        GET.replace(b"approved.test/", b"0x7f000001/"),
        GET.replace(b"approved.test/", b"user@approved.test/"),
        GET.replace(b"approved.test/", b"approved.test:65536/"),
        GET.replace(b"approved.test/", b"approved.test:0/"),
        GET.replace(b"approved.test/", b"approved.test:00080/"),
        GET.replace(b"approved.test/", b"[::1]/"),
        GET.replace(b"approved.test/", b"approved.test./"),
        GET.replace(b"approved.test/", b"bad-" + b"a" * 254 + b"/"),
        GET.replace(b"Host:", b" Host:"),
        GET.replace(b"Host:", b"Host"),
        GET.replace(b"Host:", b"Host:\x00"),
        GET.replace(b"Host: approved.test", b"Host: denied.test"),
        GET.replace(b"Host: approved.test", b"Host: approved.test\r\nHost: approved.test"),
        GET[:-2] + b"Content-Length: 2\r\n\r\n",
        GET[:-2] + b"Content-Length: 0\r\nContent-Length: 0\r\n\r\n",
        GET[:-2] + b"Transfer-Encoding: chunked\r\n\r\n",
        GET[:-2] + b"Connection: Upgrade\r\n\r\n",
        GET[:-2] + b"Upgrade: websocket\r\n\r\n",
        CONNECT.replace(b":443", b""),
    ],
)
def test_proxy_rejects_ambiguous_or_unsupported_requests(head: bytes) -> None:
    with pytest.raises(ValueError):
        egress._request(head)


def test_forwarding_uses_canonical_host_and_discards_hop_headers() -> None:
    head = (
        b"HEAD HTTP://APPROVED.TEST:80?q=1 HTTP/1.0\r\n"
        b"Host: APPROVED.test\r\nConnection: x-hop, keep-alive\r\n"
        b"X-Hop: discard\r\nProxy-Authorization: discard\r\n"
        b"Content-Length: 0\r\nAccept: text/html\r\n\r\n"
    )
    destination, forwarded = egress._request(head)
    assert destination.origin == HTTP
    assert forwarded == (
        b"HEAD /?q=1 HTTP/1.1\r\nHost: approved.test\r\n"
        b"Connection: close\r\naccept: text/html\r\n\r\n"
    )
    assert egress._request(GET.replace(b"/path?q=1", b""))[1] == (
        b"GET / HTTP/1.1\r\nHost: approved.test\r\nConnection: close\r\n\r\n"
    )
    assert egress._request(CONNECT)[0].origin == HTTPS


@pytest.mark.parametrize(
    ("address", "allowed", "explicit"),
    [
        ("192.0.2.1", False, False),
        ("192.0.2.1", True, True),
        ("10.4.0.1", False, False),
        ("10.4.0.1", True, True),
        ("127.0.0.1", False, True),
        ("169.254.169.254", False, True),
        ("224.1.0.1", False, True),
        ("0.0.0.0", False, True),
        ("93.184.215.14", True, False),
        ("2606:4700::1111", True, False),
        ("::ffff:192.0.2.1", False, True),
        ("2002:c000:201::", False, True),
        ("2001::1", False, True),
        ("2606:4700::1111%1", False, True),
        ("64:ff9b::c000:201", False, True),
        ("64:ff9b:1::c000:201", False, True),
    ],
)
def test_address_restrictions(address: str, allowed: bool, explicit: bool) -> None:
    ranges = (ipaddress.ip_network(address.split("%")[0]),) if explicit else ()
    assert egress._allowed_address(address, ranges) is allowed


def _address(host: str, port: int) -> tuple[int, int, int, str, tuple[str, int]]:
    return socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port)


@pytest.fixture
async def upstream() -> AsyncGenerator[tuple[int, list[bytes]]]:
    data: list[bytes] = []

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await reader.readexactly(4)
            if first == b"GET ":
                data.append(first + await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            else:
                data.append(first + await reader.read())
                writer.write(b"tunnel reply")
            await writer.drain()
        finally:
            writer.close()

    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        yield server.sockets[0].getsockname()[1], data


@pytest.fixture
async def proxy(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[str]:
    with tempfile.TemporaryDirectory(prefix="ue-", dir="/tmp") as directory:
        path = Path(directory) / "proxy.sock"
        left, right = socket.socketpair()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen()
        serving = asyncio.create_task(egress._serve(listener, right))
        reader, writer = await asyncio.open_connection(sock=left)
        await write_worker_frame(
            writer, {"origins": [HTTP, HTTPS], "policy": {HTTP: [], HTTPS: []}}
        )
        assert await reader.readexactly(6) == b"ready\n"
        try:
            monkeypatch.setattr(isolated_worker, "_GATE_SOCKET", path)
            async with isolated_worker._proxy() as url:
                yield url
        finally:
            writer.close()
            await writer.wait_closed()
            await serving


async def _connect(proxy: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", int(proxy.rsplit(":", 1)[1]))


async def _exchange(proxy: str, raw_request: bytes) -> bytes:
    reader, writer = await _connect(proxy)
    try:
        writer.write(raw_request)
        await writer.drain()
        writer.write_eof()
        return await reader.read()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_http_and_opaque_connect_through_the_bridge(
    monkeypatch: pytest.MonkeyPatch, proxy: str, upstream: tuple[int, list[bytes]]
) -> None:
    port, data = upstream
    resolver = AsyncMock(return_value=[_address("127.0.0.1", port)])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    monkeypatch.setattr(egress, "_allowed_address", Mock(return_value=True))
    assert (await _exchange(proxy, GET)).endswith(b"OK")
    assert (await _exchange(proxy, CONNECT + b"opaque client data")) == (
        egress._CONNECTED + b"tunnel reply"
    )
    assert data == [
        b"GET /path?q=1 HTTP/1.1\r\nHost: approved.test\r\nConnection: close\r\n\r\n",
        b"opaque client data",
    ]
    assert [call.args for call in resolver.await_args_list] == [
        ("approved.test.", 80),
        ("approved.test.", 443),
    ]


async def test_denials_never_contact_dns_or_upstream(
    monkeypatch: pytest.MonkeyPatch, proxy: str, upstream: tuple[int, list[bytes]]
) -> None:
    port, data = upstream
    resolver = AsyncMock(return_value=[_address("93.184.215.14", 80), _address("127.0.0.1", port)])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    for request in [
        GET.replace(b"approved.test", b"denied.test"),
        CONNECT.replace(b":443", b":8443"),
        GET[:-2],
        GET[:-2] + b"X-Large: " + (b"x" * 20000) + b"\r\n\r\n",
    ]:
        assert (await _exchange(proxy, request)).startswith(b"HTTP/1.1 403")
    resolver.assert_not_called()
    assert (await _exchange(proxy, GET)).startswith(b"HTTP/1.1 403")
    resolver.assert_awaited_once()
    assert not data


@pytest.mark.parametrize("result", ["unreachable", "dns-error", "empty", "cancel"])
async def test_upstream_resolution_fails_closed(
    monkeypatch: pytest.MonkeyPatch, proxy: str, result: str
) -> None:
    resolver = AsyncMock(return_value=[] if result == "empty" else [_address("93.184.215.14", 80)])
    if result == "dns-error":
        resolver.side_effect = OSError("dns failed")
    loop = asyncio.get_running_loop()
    connect = loop.sock_connect

    async def connector(sock: socket.socket, address: str | tuple[str, int]) -> None:
        if isinstance(address, str) or address[0] == "127.0.0.1":
            await connect(sock, address)
        elif result == "cancel":
            raise asyncio.CancelledError
        else:
            raise OSError("unreachable")

    monkeypatch.setattr(loop, "getaddrinfo", resolver)
    monkeypatch.setattr(loop, "sock_connect", connector)
    if result == "cancel":
        # A deadline cancellation must close the attempted socket.
        with pytest.raises(asyncio.CancelledError):
            await egress._upstream(egress._request(GET)[0], ())
    else:
        expected = b"403" if result == "empty" else b"502"
        assert (await _exchange(proxy, GET)).split(b" ")[1] == expected


async def test_connection_gate_limits_and_missing_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proxy: str
) -> None:
    gate = egress.IsolatedEgressGate({})
    writer = Mock()
    monkeypatch.setattr(egress, "_MAX_CONNECTIONS", 0)
    await gate.handle(asyncio.StreamReader(), writer)
    writer.close.assert_called_once()

    monkeypatch.setattr(isolated_worker, "_GATE_SOCKET", tmp_path / "missing.sock")
    reader, writer = await _connect(proxy)
    try:
        assert await reader.read() == b""
    finally:
        writer.close()
        await writer.wait_closed()


async def test_header_size_and_transport_cutoffs(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = egress.IsolatedEgressGate({HTTP: ()})
    reader = asyncio.StreamReader()
    reader.feed_data(GET)
    writer = Mock(drain=AsyncMock())
    with monkeypatch.context() as patch:
        patch.setattr(egress, "_MAX_HEADER", len(GET) - 1)
        await gate.handle(reader, writer)
    writer.write.assert_called_once_with(egress._DENIED)

    reader.feed_data(GET)
    upstream_reader = asyncio.StreamReader()
    upstream_reader.set_exception(ConnectionResetError())
    upstream_writer = Mock(drain=AsyncMock())
    monkeypatch.setattr(
        egress, "_upstream", AsyncMock(return_value=(upstream_reader, upstream_writer))
    )
    await gate.handle(reader, writer)
    upstream_writer.close.assert_called_once()
    assert gate._active == 0

    reader.feed_eof()
    writer.is_closing.return_value = True
    await egress.relay(reader, writer)
    writer.write_eof.assert_not_called()

    stalled_writer = Mock(drain=AsyncMock())
    with monkeypatch.context() as patch:
        patch.setattr(egress, "_HEADER_TIMEOUT", 0.01)
        await gate.handle(asyncio.StreamReader(), stalled_writer)
    stalled_writer.write.assert_not_called()
    stalled_writer.close.assert_called_once()
    assert gate._active == 0
