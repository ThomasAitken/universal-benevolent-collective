"""Supervisor-side HTTPS reads for untrusted public source URLs."""

import asyncio
import ipaddress
import json
import math
import re
import socket
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpcore
import httpx

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network

_MAX_URL_LENGTH = 8192
_REDIRECTS = {301, 302, 303, 307, 308}
_DNS_NAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_PUBLIC_IPV6 = ipaddress.IPv6Network("2000::/3")
_ANONYMOUS_HEADERS = [(b"User-Agent", b"UBC-Research/0.1"), (b"Accept-Encoding", b"gzip, deflate")]
_DUAL_STACK_CONNECT_TIMEOUT_SECONDS = 5.0


class WebReadError(ValueError):
    """The request was denied or could not be completed within the read boundary."""


@dataclass(frozen=True, slots=True)
class FetchLimits:
    timeout_seconds: float = 15
    max_body_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 5

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive and finite")
        if self.max_body_bytes < 1 or self.max_redirects < 0:
            raise ValueError("invalid body or redirect limit")


_DEFAULT_FETCH_LIMITS = FetchLimits()


@dataclass(frozen=True, slots=True)
class Redirect:
    status: int
    target_url: str


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    effective_url: str
    redirects: tuple[Redirect, ...]
    status: int
    content_type: str | None
    body: bytes
    retrieved_at: datetime


def _public_url(value: str, *, base: httpx.URL | None = None) -> httpx.URL:
    if (
        not value
        or len(value) > _MAX_URL_LENGTH
        or any(ord(character) <= 32 or ord(character) == 127 for character in value)
        or "\\" in value
    ):
        raise WebReadError("invalid web URL")
    try:
        authority = urlsplit(value).netloc
        url = httpx.URL(value) if base is None else base.join(value)
    except (ValueError, httpx.InvalidURL) as error:
        raise WebReadError("invalid web URL") from error
    if "@" in authority:
        raise WebReadError("URL credentials are forbidden")
    host = url.raw_host.decode("ascii")
    if (
        url.scheme != "https"
        or url.port is not None  # HTTPX normalizes the default HTTPS port.
        or len(host) > 253
        or not _DNS_NAME.fullmatch(host)
        or host.endswith(_LOCAL_SUFFIXES)
    ):
        raise WebReadError("a public HTTPS DNS name on port 443 is required")
    url = url.copy_with(fragment=None)
    if len(str(url)) > _MAX_URL_LENGTH:
        raise WebReadError("normalized web URL is too long")
    return url


def _public_address(value: str, excluded: tuple[Network, ...]) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address) and (
        address not in _PUBLIC_IPV6
        or address.scope_id is not None
        or address.sixtofour is not None
        or address.teredo is not None
    ):
        return False
    return not any(address in network for network in excluded)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Keep the original TLS hostname while connecting only to checked numeric IPs."""

    def __init__(self, excluded: tuple[Network, ...]) -> None:
        self._excluded = excluded
        # The pinned HTTP Core package exposes this backend when HTTPX's AnyIO is installed.
        self._backend = cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())
        self.connected_stream: httpcore.AsyncNetworkStream | None = None

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTP Core backend signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        records = await asyncio.get_running_loop().getaddrinfo(
            f"{host}.", port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
        if not addresses or not all(_public_address(ip, self._excluded) for ip in addresses):
            raise WebReadError("DNS returned a destination outside the public network policy")
        attempt_timeout = _DUAL_STACK_CONNECT_TIMEOUT_SECONDS if len(addresses) > 1 else timeout
        for address in addresses:
            try:
                stream = await self._backend.connect_tcp(
                    address, port, attempt_timeout, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                continue
            self.connected_stream = stream
            return stream
        raise WebReadError("no approved address accepted the connection")


async def _read_body(response: httpcore.Response, limit: int) -> bytes:
    content_length = _header(response, b"content-length")
    if content_length is not None and int(content_length) > limit:
        raise WebReadError("response exceeds the body byte limit")
    encoding = (_header(response, b"content-encoding") or "identity").strip().lower()
    if encoding == "identity":
        decompressor = None
    elif encoding == "gzip":
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decompressor = zlib.decompressobj(zlib.MAX_WBITS)
    else:
        raise WebReadError("unsupported response content encoding")

    body = bytearray()
    wire_bytes = 0
    try:
        async for chunk in response.aiter_stream():
            wire_bytes += len(chunk)
            if wire_bytes > limit:
                raise WebReadError("response exceeds the body byte limit")
            if decompressor is None:
                body.extend(chunk)
                continue
            remaining = limit - len(body)
            piece = decompressor.decompress(chunk, remaining + 1)
            if len(piece) > remaining:
                raise WebReadError("response exceeds the body byte limit")
            body.extend(piece)
        if decompressor is not None:
            body.extend(decompressor.flush())
            if not decompressor.eof or len(body) > limit:
                raise WebReadError("corrupt or oversize compressed response")
    except zlib.error as error:
        raise WebReadError("corrupt compressed response") from error
    return bytes(body)


def _header(response: httpcore.Response, key: bytes) -> str | None:
    values = [value for name, value in response.headers if name.lower() == key]
    if not values:
        return None
    if len(values) != 1:
        raise WebReadError("ambiguous response header")
    return values[0].decode("latin-1")


class PublicWebFetcher:
    """Read anonymous public HTTPS pages with one deadline across all hops."""

    def __init__(
        self, limits: FetchLimits = _DEFAULT_FETCH_LIMITS, *, excluded_networks: Iterable[str] = ()
    ) -> None:
        self._limits = limits
        self._excluded = tuple(ipaddress.ip_network(cidr) for cidr in excluded_networks)

    async def fetch(
        self, url: str, *, method: str = "GET", limits: FetchLimits | None = None
    ) -> FetchResult:
        if method not in {"GET", "HEAD"}:
            raise WebReadError("only GET and HEAD are allowed")
        return await _fetch(
            _public_url(url),
            method=method,
            headers=_ANONYMOUS_HEADERS,
            limits=self._limits if limits is None else limits,
            excluded=self._excluded,
        )


async def _fetch(
    url: httpx.URL,
    *,
    method: str,
    headers: list[tuple[bytes, bytes]],
    limits: FetchLimits,
    excluded: tuple[Network, ...],
    content: bytes | None = None,
) -> FetchResult:
    requested_url = str(url)
    redirects: list[Redirect] = []
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            while True:
                backend = _PinnedBackend(excluded)
                try:
                    # A fresh pool per hop prevents connection reuse across DNS decisions.
                    async with (
                        httpcore.AsyncConnectionPool(network_backend=backend) as pool,
                        pool.stream(
                            method,
                            httpcore.URL(scheme=b"https", host=url.raw_host, target=url.raw_path),
                            headers=headers,
                            content=content,
                        ) as response,
                    ):
                        if response.status in _REDIRECTS:
                            location = _header(response, b"location")
                            if location is None:
                                raise WebReadError("redirect has no location")
                            if len(redirects) >= limits.max_redirects:
                                raise WebReadError("redirect limit reached")
                            url = _public_url(location, base=url)
                            redirects.append(Redirect(response.status, str(url)))
                            continue
                        body = (
                            b""
                            if method == "HEAD"
                            else await _read_body(response, limits.max_body_bytes)
                        )
                        return FetchResult(
                            requested_url,
                            str(url),
                            tuple(redirects),
                            response.status,
                            _header(response, b"content-type"),
                            body,
                            datetime.now(UTC),
                        )
                finally:
                    # The pool owns established TLS streams. Also close a TCP stream
                    # if the deadline or caller cancelled its TLS handshake.
                    if backend.connected_stream is not None:
                        await backend.connected_stream.aclose()
    except (TimeoutError, httpcore.TimeoutException) as error:
        raise WebReadError("HTTPS read timed out") from error
    except (OSError, httpcore.NetworkError, httpcore.ProtocolError) as error:
        raise WebReadError("HTTPS read failed") from error


@dataclass(frozen=True, slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str


class SearchProvider(Protocol):
    async def search(self, query: str, *, count: int = 10) -> tuple[SearchHit, ...]: ...


def _search_query(query: str, count: int) -> str:
    query = query.strip()
    if not query or len(query) > 600 or len(query.split()) > 75:
        raise ValueError("search query must contain 1 to 600 characters and at most 75 words")
    if not 1 <= count <= 20:
        raise ValueError("search count must be between 1 and 20")
    return query


def _validate_api_key(api_key: str, provider: str) -> bytes:
    if not api_key or any(not 33 <= ord(character) <= 126 for character in api_key):
        raise ValueError(f"a valid {provider} API key is required")
    return api_key.encode("ascii")


def _parse_json_object(body: bytes) -> dict[str, object]:
    try:
        response: object = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise WebReadError("invalid search response") from error
    if not isinstance(response, dict):
        raise WebReadError("invalid search response")
    return cast(dict[str, object], response)


def _extract_hits(
    results: object, *, url_key: str, snippet_key: str, count: int
) -> tuple[SearchHit, ...]:
    if not isinstance(results, list):
        raise WebReadError("invalid search response")
    hits: list[SearchHit] = []
    for item in cast(list[object], results):
        if not isinstance(item, dict):
            raise WebReadError("invalid search result")
        fields = cast(dict[str, object], item)
        url = fields.get(url_key)
        title = fields.get("title")
        snippet = fields.get(snippet_key, "")
        if not isinstance(url, str) or not isinstance(title, str) or not isinstance(snippet, str):
            raise WebReadError("invalid search result")
        try:
            public_url = _public_url(url)
        except WebReadError:
            continue
        hits.append(SearchHit(str(public_url), title[:500], snippet[:2000]))
        if len(hits) == count:
            break
    return tuple(hits)


class FixtureSearch:
    """Return recorded-in-test hits for exact queries without using the network."""

    def __init__(self, results: Mapping[str, tuple[SearchHit, ...]]) -> None:
        self._results = dict(results)

    async def search(self, query: str, *, count: int = 10) -> tuple[SearchHit, ...]:
        return self._results.get(_search_query(query, count), ())[:count]


class BraveSearch:
    """Read one bounded batch from Brave's web index using an operator-supplied key."""

    def __init__(self, api_key: str, *, excluded_networks: Iterable[str] = ()) -> None:
        self._headers = [
            *_ANONYMOUS_HEADERS,
            (b"Accept", b"application/json"),
            (b"X-Subscription-Token", _validate_api_key(api_key, "Brave Search")),
        ]
        self._excluded = tuple(ipaddress.ip_network(cidr) for cidr in excluded_networks)

    async def search(self, query: str, *, count: int = 10) -> tuple[SearchHit, ...]:
        query = _search_query(query, count)
        url = httpx.URL(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count, "result_filter": "web", "text_decorations": False},
        )
        result = await _fetch(
            url,
            method="GET",
            headers=self._headers,
            limits=FetchLimits(timeout_seconds=10, max_body_bytes=512 * 1024, max_redirects=0),
            excluded=self._excluded,
        )
        if result.status != 200:
            raise WebReadError(f"search provider returned HTTP {result.status}")
        response = _parse_json_object(result.body)
        web = response.get("web", {})
        if not isinstance(web, dict):
            raise WebReadError("invalid search response")
        return _extract_hits(
            cast(dict[str, object], web).get("results", []),
            url_key="url",
            snippet_key="description",
            count=count,
        )


class SerperSearch:
    """Read one bounded batch from Serper's Google search API using an operator-supplied key."""

    def __init__(self, api_key: str, *, excluded_networks: Iterable[str] = ()) -> None:
        self._headers = [
            *_ANONYMOUS_HEADERS,
            (b"Accept", b"application/json"),
            (b"Content-Type", b"application/json"),
            (b"X-API-KEY", _validate_api_key(api_key, "Serper")),
        ]
        self._excluded = tuple(ipaddress.ip_network(cidr) for cidr in excluded_networks)

    async def search(self, query: str, *, count: int = 10) -> tuple[SearchHit, ...]:
        query = _search_query(query, count)
        payload = json.dumps({"q": query, "num": count}).encode("utf-8")
        result = await _fetch(
            httpx.URL("https://google.serper.dev/search"),
            method="POST",
            headers=self._headers,
            content=payload,
            limits=FetchLimits(timeout_seconds=10, max_body_bytes=512 * 1024, max_redirects=0),
            excluded=self._excluded,
        )
        if result.status != 200:
            raise WebReadError(f"search provider returned HTTP {result.status}")
        response = _parse_json_object(result.body)
        return _extract_hits(
            response.get("organic", []), url_key="link", snippet_key="snippet", count=count
        )
