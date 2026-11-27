"""Authority policy and HTTP proxy for the isolated browser's private Unix socket."""

import asyncio
import ipaddress
import re
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ubc_node.browser.supervised_browser import read_worker_frame

_MAX_HEADER = 16 * 1024
_MAX_CONNECTIONS = 32
_MAX_ORIGINS = 32
_HEADER_TIMEOUT = 5
_CONNECT_TIMEOUT = 10
_CONNECTION_TIMEOUT = 120
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# A DNS name must have a letter in its final label. This excludes numeric IP aliases.
_HOST = re.compile(rf"(?:{_DNS_LABEL}\.)*[a-z](?:[a-z0-9-]{{0,61}}[a-z0-9])?")
_HTTP_TARGET = re.compile(r"http://([^/?]+)([/?].*)?", re.IGNORECASE)
_ORIGIN = re.compile(r"(https?)://([^/]+)", re.IGNORECASE)
_FORBIDDEN_HEADERS = {"expect", "te", "trailer", "transfer-encoding", "upgrade"}
_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
}
_DENIED = b"HTTP/1.1 403 Denied\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
_UNAVAILABLE = b"HTTP/1.1 502 Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
_CONNECTED = b"HTTP/1.1 200 Connection Established\r\n\r\n"
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")

type AddressRange = ipaddress.IPv4Network | ipaddress.IPv6Network


class EgressDenied(ValueError):
    """The request cannot create an upstream connection."""


@dataclass(frozen=True, slots=True)
class _Destination:
    scheme: str
    host: str
    port: int

    @property
    def authority(self) -> str:
        default = 80 if self.scheme == "http" else 443
        return self.host if self.port == default else f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.authority}"


def _destination(scheme: str, authority: str, *, connect: bool = False) -> _Destination:
    host, separator, raw_port = authority.lower().partition(":")
    if len(host) > 253 or _HOST.fullmatch(host) is None:
        raise EgressDenied("a DNS hostname is required")
    if separator:
        if not re.fullmatch(r"[1-9][0-9]{0,4}", raw_port) or int(raw_port) > 65535:
            raise EgressDenied("invalid port")
        port = int(raw_port)
    elif connect:
        raise EgressDenied("CONNECT needs an explicit port")
    else:
        port = 80 if scheme == "http" else 443
    return _Destination(scheme, host, port)


def _origin(value: str) -> str:
    match = _ORIGIN.fullmatch(value)
    if match is None:
        raise EgressDenied("expected an HTTP or HTTPS origin")
    return _destination(match[1].lower(), match[2]).origin


def session_policy(requested: object, configured: object) -> dict[str, tuple[AddressRange, ...]]:
    """Apply the operator's exact-origin ceiling to a session's requested origins."""
    if not isinstance(configured, dict) or not isinstance(requested, list):
        raise EgressDenied("expected an origin list and an operator policy")
    origins = cast(list[object], requested)
    ceiling = cast(dict[object, object], configured)
    if not 1 <= len(origins) <= _MAX_ORIGINS or not 1 <= len(ceiling) <= _MAX_ORIGINS:
        raise EgressDenied("invalid number of egress origins")
    policy: dict[str, tuple[AddressRange, ...]] = {}
    for origin, ranges in ceiling.items():
        if not isinstance(origin, str) or not isinstance(ranges, list):
            raise EgressDenied("expected CIDR ranges for each configured origin")
        canonical = _origin(origin)
        if canonical in policy:
            raise EgressDenied("duplicate configured origin")
        values = cast(list[object], ranges)
        if len(values) > _MAX_ORIGINS or not all(isinstance(value, str) for value in values):
            raise EgressDenied("invalid destination ranges")
        policy[canonical] = tuple(ipaddress.ip_network(value) for value in cast(list[str], values))

    allowed: dict[str, tuple[AddressRange, ...]] = {}
    for origin in origins:
        if not isinstance(origin, str) or (canonical := _origin(origin)) not in policy:
            raise EgressDenied("session origin is outside the operator policy")
        allowed[canonical] = policy[canonical]
    return allowed


def _request(head: bytes) -> tuple[_Destination, bytes | None]:
    lines = head.decode("ascii").removesuffix("\r\n\r\n").split("\r\n")
    method, target, version = lines[0].split(" ")
    if version not in {"HTTP/1.0", "HTTP/1.1"} or not re.fullmatch(r"[\x21-\x7e]+", target):
        raise EgressDenied("invalid proxy request line")
    if any(character in target for character in "\\#"):
        raise EgressDenied("invalid request target")
    if method == "CONNECT":
        destination = _destination("https", target, connect=True)
        path = ""
    elif method in {"GET", "HEAD"}:
        match = _HTTP_TARGET.fullmatch(target)
        if match is None:
            raise EgressDenied("expected an absolute HTTP URL")
        destination = _destination("http", match[1])
        path = match[2] or "/"
        if path.startswith("//"):
            raise EgressDenied("invalid origin-form path")
        if path.startswith("?"):
            path = f"/{path}"
    else:
        raise EgressDenied("proxy method is forbidden")

    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        field, _, raw_value = line.partition(":")
        if ":" not in line or not _TOKEN.fullmatch(field) or not re.fullmatch(r"[ -~]*", raw_value):
            raise EgressDenied("invalid proxy header")
        headers.append((field.lower(), raw_value.strip()))

    hosts = [value for field, value in headers if field == "host"]
    if len(hosts) != 1 or _destination(destination.scheme, hosts[0]) != destination:
        raise EgressDenied("Host must match the proxy target")
    lengths = [value for field, value in headers if field == "content-length"]
    if lengths not in ([], ["0"]) or any(field in _FORBIDDEN_HEADERS for field, _ in headers):
        raise EgressDenied("request bodies and protocol upgrades are disabled")
    connection_fields = {
        token.strip().lower()
        for field, value in headers
        if field == "connection"
        for token in value.split(",")
    }
    if "upgrade" in connection_fields:
        raise EgressDenied("protocol upgrades are disabled")
    if method == "CONNECT":
        return destination, None
    forwarded = [
        f"{method} {path} HTTP/1.1",
        f"Host: {destination.authority}",
        "Connection: close",
    ]
    forwarded.extend(
        f"{field}: {value}"
        for field, value in headers
        if field not in _HOP_HEADERS and field not in connection_fields
    )
    return destination, ("\r\n".join(forwarded) + "\r\n\r\n").encode("ascii")


def _allowed_address(address: str, ranges: tuple[AddressRange, ...]) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.ipv4_mapped is not None
        or ip.sixtofour is not None
        or ip.teredo is not None
        or ip.scope_id is not None
        or ip in _NAT64_WELL_KNOWN
        or ip in _NAT64_LOCAL
    ):
        return False
    return any(ip in network for network in ranges) if ranges else ip.is_global


async def _upstream(
    destination: _Destination, ranges: tuple[AddressRange, ...]
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(
        f"{destination.host}.",
        destination.port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    if not addresses or any(
        not _allowed_address(str(address[0]), ranges) for *_, address in addresses
    ):
        raise EgressDenied("DNS returned a destination outside the address policy")
    # Connect to the checked socket address directly, without resolving the name again.
    for family, kind, proto, _, address in addresses:
        upstream = socket.socket(family, kind, proto)
        try:
            upstream.setblocking(False)
            await loop.sock_connect(upstream, address)
            return await asyncio.open_connection(sock=upstream)
        except OSError:
            upstream.close()
        except BaseException:
            upstream.close()
            raise
    raise OSError("no approved address accepted the connection")


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one stream, preserving a TCP or Unix stream half-close."""
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()
    if not writer.is_closing():
        writer.write_eof()


async def tunnel(
    left: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    right: tuple[asyncio.StreamReader, asyncio.StreamWriter],
) -> None:
    outgoing = asyncio.create_task(relay(left[0], right[1]))
    incoming = asyncio.create_task(relay(right[0], left[1]))
    try:
        await asyncio.gather(outgoing, incoming)
    finally:
        outgoing.cancel()
        incoming.cancel()
        await asyncio.gather(outgoing, incoming, return_exceptions=True)


class IsolatedEgressGate:
    """Serve only the authority delegated to one browser container."""

    def __init__(self, policy: Mapping[str, tuple[AddressRange, ...]]) -> None:
        self._policy = policy
        self._active = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._active >= _MAX_CONNECTIONS:
            writer.close()
            return
        self._active += 1
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            try:
                async with asyncio.timeout(_HEADER_TIMEOUT):
                    head = await reader.readuntil(b"\r\n\r\n")
                    if len(head) > _MAX_HEADER:
                        raise EgressDenied("proxy request headers exceeded the limit")
                    destination, forwarded = _request(head)
                    ranges = self._policy.get(destination.origin)
                    if ranges is None:
                        raise EgressDenied("origin is not approved")
            except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                writer.write(_DENIED)
                await writer.drain()
                return
            try:
                async with asyncio.timeout(_CONNECT_TIMEOUT):
                    upstream_reader, upstream_writer = await _upstream(destination, ranges)
            except EgressDenied:
                writer.write(_DENIED)
                await writer.drain()
                return
            except (TimeoutError, OSError):
                writer.write(_UNAVAILABLE)
                await writer.drain()
                return

            async with asyncio.timeout(_CONNECTION_TIMEOUT):
                if forwarded is None:
                    writer.write(_CONNECTED)
                    await writer.drain()
                    await tunnel((reader, writer), (upstream_reader, upstream_writer))
                else:
                    upstream_writer.write(forwarded)
                    await upstream_writer.drain()
                    await relay(upstream_reader, writer)
        except (TimeoutError, OSError):
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
            writer.close()
            self._active -= 1


async def _serve(listener: socket.socket, broker: socket.socket) -> None:
    reader, writer = await asyncio.open_connection(sock=broker)
    try:
        init = await read_worker_frame(reader)
        gate = IsolatedEgressGate(session_policy(init.get("origins"), init.get("policy")))
        server = await asyncio.start_unix_server(gate.handle, sock=listener, limit=_MAX_HEADER)
        try:
            writer.write(b"ready\n")
            await writer.drain()
            await reader.read(1)
        finally:
            server.close()
            server.abort_clients()
            await server.wait_closed()
    finally:
        writer.close()


async def _main(
    listener_fd: int, broker_fd: int
) -> None:  # pragma: no cover - Linux gate entrypoint
    with socket.socket(fileno=listener_fd) as listener, socket.socket(fileno=broker_fd) as broker:
        listener.set_inheritable(False)
        broker.set_inheritable(False)
        await _serve(listener, broker)


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1]), int(sys.argv[2])))
