import asyncio
import gzip
import json
import socket
import ssl
import zlib
from collections.abc import Iterable
from datetime import UTC
from urllib.parse import parse_qs, urlsplit

import httpcore
import pytest
from pytest import MonkeyPatch

from ubc_node.research.public_web import (
    BraveSearch,
    FetchLimits,
    FixtureSearch,
    PublicWebFetcher,
    Redirect,
    SearchHit,
    SearchProvider,
    SerperSearch,
    WebReadError,
)

PUBLIC_IP = "93.184.215.14"
OTHER_IP = "2606:4700::6810:84e5"
URL = "https://source.example/start"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nContent-Type: text/plain\r\n\r\ndata"


class Wire(httpcore.AsyncMockStream):
    def __init__(self, *chunks: bytes, stall_read: bool = False, stall_tls: bool = False) -> None:
        super().__init__(list(chunks))
        self.stall_read = stall_read
        self.stall_tls = stall_tls
        self.started = asyncio.Event()
        self.hostname: str | None = None
        self.outgoing = bytearray()
        self.closed = False

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTP Core signature
    ) -> httpcore.AsyncNetworkStream:
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert ssl_context.check_hostname
        self.hostname = server_hostname
        if self.stall_tls:
            self.started.set()
            await asyncio.Event().wait()
        return self

    async def read(
        self,
        max_bytes: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTP Core signature
    ) -> bytes:
        if self.stall_read:
            self.started.set()
            await asyncio.Event().wait()
        return await super().read(max_bytes, timeout)

    async def write(
        self,
        buffer: bytes,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTP Core signature
    ) -> None:
        self.outgoing.extend(buffer)

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


class NetworkFixture(httpcore.AsyncNetworkBackend):
    def __init__(self, monkeypatch: MonkeyPatch) -> None:
        self.answers: list[list[str] | OSError] = [[PUBLIC_IP]]
        self.dns_calls: list[tuple[str, int]] = []
        self.connections: list[tuple[str, int]] = []
        self.connect_timeouts: list[float | None] = []
        self.outcomes: list[Wire | Exception] = []
        self.stall_dns = False
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", self.resolve)
        monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: self)

    async def resolve(
        self, host: str, port: int, *, type: int, proto: int
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert type == socket.SOCK_STREAM and proto == socket.IPPROTO_TCP
        self.dns_calls.append((host, port))
        if self.stall_dns:
            await asyncio.Event().wait()
        answers = self.answers.pop(0)
        if isinstance(answers, OSError):
            raise answers
        return [(0, type, proto, "", (address, port)) for address in answers]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTP Core signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connections.append((host, port))
        self.connect_timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
async def net(monkeypatch: MonkeyPatch) -> NetworkFixture:
    return NetworkFixture(monkeypatch)


def redirect(target: str) -> Wire:
    return Wire(
        f"HTTP/1.1 302 Found\r\nLocation: {target}\r\nContent-Length: 5000\r\n\r\n".encode()
    )


async def test_pins_each_hop_and_keeps_hostname_for_tls_and_host(
    net: NetworkFixture, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    first, second, final = redirect("/next"), redirect("//www.example/end?q=yes#ignored"), Wire(OK)
    net.outcomes = [first, second, final]
    net.answers = [[PUBLIC_IP], [OTHER_IP], [PUBLIC_IP]]
    result = await PublicWebFetcher(FetchLimits(max_body_bytes=4, max_redirects=2)).fetch(
        "https://SOURCE.example:443/start#initial"
    )

    assert result.requested_url == URL
    assert result.effective_url == "https://www.example/end?q=yes"
    assert result.redirects == (
        Redirect(302, "https://source.example/next"),
        Redirect(302, result.effective_url),
    )
    assert (result.status, result.content_type, result.body) == (200, "text/plain", b"data")
    assert result.retrieved_at.tzinfo is UTC
    assert net.dns_calls == [
        ("source.example.", 443),
        ("source.example.", 443),
        ("www.example.", 443),
    ]
    assert net.connections == [(PUBLIC_IP, 443), (OTHER_IP, 443), (PUBLIC_IP, 443)]
    assert [wire.hostname for wire in (first, second, final)] == [
        "source.example",
        "source.example",
        "www.example",
    ]
    assert [wire.outgoing.split(b"\r\n", 1)[0] for wire in (first, second, final)] == [
        b"GET /start HTTP/1.1",
        b"GET /next HTTP/1.1",
        b"GET /end?q=yes HTTP/1.1",
    ]
    for wire in (first, second, final):
        assert wire.closed
        assert wire.hostname
        assert f"host: {wire.hostname}".encode() in wire.outgoing.lower()
        assert b"accept-encoding: gzip, deflate" in wire.outgoing.lower()
        assert b"cookie:" not in wire.outgoing.lower()
        assert b"authorization:" not in wire.outgoing.lower()
        assert b"x-subscription-token:" not in wire.outgoing.lower()
        assert b"x-api-key:" not in wire.outgoing.lower()


async def test_head_stays_head_after_redirect_and_ignores_advertised_body(
    net: NetworkFixture,
) -> None:
    first = redirect("/next")
    final = Wire(b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n")
    net.answers = [[PUBLIC_IP], [PUBLIC_IP]]
    net.outcomes = [first, final]
    result = await PublicWebFetcher(FetchLimits(max_body_bytes=1)).fetch(URL, method="HEAD")
    assert first.outgoing.startswith(b"HEAD /start ")
    assert final.outgoing.startswith(b"HEAD /next ")
    assert result.body == b""
    assert result.content_type is None


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "CONNECT", "get"])
async def test_methods_require_get_or_head(net: NetworkFixture, method: str) -> None:
    with pytest.raises(WebReadError, match="only GET and HEAD"):
        await PublicWebFetcher().fetch(URL, method=method)
    assert not net.dns_calls and not net.connections


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://source.example/",
        "https://source.example:444/",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://localhost/",
        "https://vm.internal/",
        "https://key:secret@source.example/",
        "https://@source.example/",
        "https://[unclosed",
        "https://source.example/\n",
        "https://source.example/\\other",
        "https://source.example/" + "x" * 8192,
        "https://source.example/" + "📘" * 1000,
    ],
)
async def test_url_rejected_before_dns(net: NetworkFixture, url: str) -> None:
    with pytest.raises(WebReadError):
        await PublicWebFetcher().fetch(url)
    assert not net.dns_calls and not net.connections


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.89.97.1",
        "192.168.5.2",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "::ffff:93.184.215.14",
        "64:ff9b::a9fe:a9fe",
        "2002:c0a8:0502::1",
        "2001::1",
        "2606:4700::1%1",
        OTHER_IP,  # An operator-excluded, globally numbered management range.
    ],
)
async def test_checks_all_dns_answers_before_any_connection(
    net: NetworkFixture, address: str
) -> None:
    net.answers = [[PUBLIC_IP, address]]
    with pytest.raises(WebReadError, match="outside the public network policy"):
        await PublicWebFetcher(excluded_networks=["2606:4700::/32"]).fetch(URL)
    assert net.dns_calls == [("source.example.", 443)]
    assert not net.connections


async def test_empty_dns_answer_is_denied(net: NetworkFixture) -> None:
    net.answers = [[]]
    with pytest.raises(WebReadError, match="outside the public network policy"):
        await PublicWebFetcher().fetch(URL)
    assert not net.connections


@pytest.mark.parametrize("resolves_private", [False, True])
async def test_rejects_redirect_to_ineligible_destination(
    net: NetworkFixture, resolves_private: bool
) -> None:
    net.answers = [[PUBLIC_IP], ["192.168.5.2"]]
    net.outcomes = [
        redirect("https://other.example" if resolves_private else "http://other.example")
    ]
    with pytest.raises(WebReadError, match="public"):
        await PublicWebFetcher().fetch(URL)
    assert net.dns_calls == (
        [("source.example.", 443), ("other.example.", 443)]
        if resolves_private
        else [("source.example.", 443)]
    )
    assert net.connections == [(PUBLIC_IP, 443)]


async def test_same_host_redirect_must_pass_dns_again(net: NetworkFixture) -> None:
    net.answers = [[PUBLIC_IP], ["127.0.0.1"]]
    net.outcomes = [redirect("/changed")]
    with pytest.raises(WebReadError, match="outside the public network policy"):
        await PublicWebFetcher().fetch(URL)
    assert net.dns_calls == [("source.example.", 443)] * 2
    assert net.connections == [(PUBLIC_IP, 443)]


async def test_redirect_limit(net: NetworkFixture) -> None:
    net.outcomes = [redirect("/next"), redirect("/again")]
    net.answers = [[PUBLIC_IP], [PUBLIC_IP]]
    with pytest.raises(WebReadError, match="redirect limit reached"):
        await PublicWebFetcher(FetchLimits(max_redirects=1)).fetch(URL)
    assert len(net.connections) == 2


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        (b"", "redirect has no location"),
        (b"Location: /one\r\nLocation: /two\r\n", "ambiguous response header"),
    ],
)
async def test_redirect_requires_unambiguous_location(
    net: NetworkFixture, headers: bytes, reason: str
) -> None:
    net.outcomes = [Wire(b"HTTP/1.1 301 Moved\r\n" + headers + b"\r\n")]
    with pytest.raises(WebReadError, match=reason):
        await PublicWebFetcher().fetch(URL)


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        ((OK,), "body byte limit"),
        (
            (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\na\r\n", b"2\r\nbc\r\n"),
            "body byte limit",
        ),
        ((b"HTTP/1.1 200 OK\r\nContent-Encoding: br\r\n\r\n",), "unsupported response content"),
        ((b"HTTP/1.1 200 OK\r\nContent-Length: nope\r\n\r\n",), "read failed"),
    ],
)
async def test_rejects_large_or_invalid_responses(
    net: NetworkFixture, replies: tuple[bytes, ...], reason: str
) -> None:
    wire = Wire(*replies)
    net.outcomes = [wire]
    with pytest.raises(WebReadError, match=reason):
        await PublicWebFetcher(FetchLimits(max_body_bytes=2)).fetch(URL)
    assert wire.closed


@pytest.mark.parametrize(
    ("encoding", "payload"),
    [
        ("gzip", gzip.compress(b"hello world")),
        ("deflate", zlib.compress(b"hello world")),
    ],
)
async def test_decompresses_bounded_gzip_and_deflate_responses(
    net: NetworkFixture, encoding: str, payload: bytes
) -> None:
    wire = Wire(
        f"HTTP/1.1 200 OK\r\nContent-Encoding: {encoding}\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    net.outcomes = [wire]
    result = await PublicWebFetcher(FetchLimits(max_body_bytes=1024)).fetch(URL)
    assert result.body == b"hello world"
    assert wire.closed


async def test_rejects_compressed_payload_exceeding_decompressed_limit(net: NetworkFixture) -> None:
    payload = gzip.compress(b"a" * 1024)
    assert len(payload) < 100
    wire = Wire(
        f"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    net.outcomes = [wire]
    with pytest.raises(WebReadError, match="body byte limit"):
        await PublicWebFetcher().fetch(URL, limits=FetchLimits(max_body_bytes=100))
    assert wire.closed


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"not-valid-gzip", "corrupt compressed response"),
        (gzip.compress(b"hello world")[:-4], "corrupt or oversize compressed response"),
    ],
)
async def test_rejects_corrupt_or_truncated_compressed_responses(
    net: NetworkFixture, payload: bytes, reason: str
) -> None:
    wire = Wire(
        f"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    net.outcomes = [wire]
    with pytest.raises(WebReadError, match=reason):
        await PublicWebFetcher(FetchLimits(max_body_bytes=1024)).fetch(URL)
    assert wire.closed


@pytest.mark.parametrize("failure", [httpcore.ConnectError(), httpcore.ConnectTimeout()])
async def test_tries_only_checked_addresses_when_tcp_fails(
    net: NetworkFixture, failure: Exception
) -> None:
    net.answers = [[PUBLIC_IP, PUBLIC_IP, OTHER_IP]]
    net.outcomes = [failure, Wire(OK)]
    assert (await PublicWebFetcher().fetch(URL)).body == b"data"
    assert net.connections == [(PUBLIC_IP, 443), (OTHER_IP, 443)]
    assert net.connect_timeouts == [5.0, 5.0]


@pytest.mark.parametrize("dns_failure", [False, True])
async def test_connection_failure(net: NetworkFixture, dns_failure: bool) -> None:
    if dns_failure:
        net.answers = [socket.gaierror()]
    else:
        net.outcomes = [httpcore.ConnectError()]
    with pytest.raises(WebReadError, match=r"read failed|no approved address"):
        await PublicWebFetcher().fetch(URL)


@pytest.mark.parametrize("phase", ["dns", "read", "tls"])
async def test_deadline_closes_active_stream(net: NetworkFixture, phase: str) -> None:
    net.stall_dns = phase == "dns"
    wire = Wire(stall_read=phase == "read", stall_tls=phase == "tls")
    net.outcomes = [wire]
    with pytest.raises(WebReadError, match="timed out"):
        await PublicWebFetcher(FetchLimits(timeout_seconds=0.05)).fetch(URL)
    assert wire.closed is (phase != "dns")


async def test_caller_can_cancel_a_read(net: NetworkFixture) -> None:
    wire = Wire(stall_tls=True)
    net.outcomes = [wire]
    task = asyncio.create_task(PublicWebFetcher().fetch(URL))
    async with asyncio.timeout(2):
        await wire.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.closed


@pytest.mark.parametrize(
    "limits",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"max_body_bytes": 0},
        {"max_redirects": -1},
    ],
)
def test_invalid_fetch_limits(limits: dict[str, float | int]) -> None:
    with pytest.raises(ValueError):
        FetchLimits(
            timeout_seconds=limits.get("timeout_seconds", 1),
            max_body_bytes=int(limits.get("max_body_bytes", 1)),
            max_redirects=int(limits.get("max_redirects", 1)),
        )


async def test_offline_search_uses_exact_queries_and_caps_results() -> None:
    hit = SearchHit("https://source.example", "Title", "Example snippet")
    search: SearchProvider = FixtureSearch({"question": (hit, hit)})
    assert await search.search(" question ", count=1) == (hit,)
    assert await search.search("Question") == ()


@pytest.mark.parametrize(
    ("query", "count"), [(" ", 1), ("x" * 601, 1), ("x " * 76, 1), ("question", 21)]
)
async def test_search_arguments_are_bounded(query: str, count: int) -> None:
    with pytest.raises(ValueError, match="search"):
        await FixtureSearch({}).search(query, count=count)


@pytest.mark.parametrize("key", ["", "key\r\nHeader: value"])
def test_provider_rejects_invalid_credentials(key: str) -> None:
    with pytest.raises(ValueError, match="API key"):
        BraveSearch(key)


def json_reply(body: bytes) -> Wire:
    return Wire(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)


async def test_live_adapter_requests_only_provider_and_returns_bounded_candidate_hits(
    net: NetworkFixture,
) -> None:
    wire = json_reply(
        json.dumps(
            {
                "web": {
                    "results": [
                        {"url": "http://elsewhere.example", "title": "skip"},
                        {"url": URL, "title": "t" * 600, "description": "s" * 2500},
                        {"url": "https://ignored.example", "title": "past count"},
                    ]
                }
            }
        ).encode()
    )
    net.outcomes = [wire]
    search: SearchProvider = BraveSearch("test-key")
    assert await search.search("A & B? count=20", count=1) == (
        SearchHit(URL, "t" * 500, "s" * 2000),
    )
    assert net.dns_calls == [("api.search.brave.com.", 443)]
    assert net.connections == [(PUBLIC_IP, 443)]
    assert wire.hostname == "api.search.brave.com"
    method, target, _ = wire.outgoing.decode().split("\r\n", 1)[0].split(" ")
    assert method == "GET"
    assert urlsplit(target).path == "/res/v1/web/search"
    assert parse_qs(urlsplit(target).query)["q"] == ["A & B? count=20"]
    assert parse_qs(urlsplit(target).query)["count"] == ["1"]
    assert b"x-subscription-token: test-key" in wire.outgoing.lower()


async def test_live_adapter_does_not_forward_search_credentials(net: NetworkFixture) -> None:
    net.outcomes = [redirect("https://other.example/token")]
    with pytest.raises(WebReadError, match="redirect limit reached"):
        await BraveSearch("test-key").search("query")
    assert net.dns_calls == [("api.search.brave.com.", 443)]


async def test_live_adapter_reports_status_without_provider_response_body(
    net: NetworkFixture,
) -> None:
    net.outcomes = [Wire(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 8\r\n\r\nresponse")]
    with pytest.raises(WebReadError, match=r"^search provider returned HTTP 401$"):
        await BraveSearch("test-key").search("query")


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"null",
        b'{"web": null}',
        b'{"web": {"results": {}}}',
        b'{"web": {"results": [null]}}',
        b'{"web": {"results": [{"url": 1, "title": "x"}]}}',
    ],
)
async def test_live_adapter_rejects_malformed_results(net: NetworkFixture, body: bytes) -> None:
    net.outcomes = [json_reply(body)]
    with pytest.raises(WebReadError, match="invalid search"):
        await BraveSearch("test-key").search("query")


async def test_live_adapter_allows_no_search_hits(net: NetworkFixture) -> None:
    net.outcomes = [json_reply(b"{}")]
    assert await BraveSearch("test-key").search("query") == ()


async def test_live_adapter_can_return_fewer_hits_than_requested(net: NetworkFixture) -> None:
    net.outcomes = [
        json_reply(json.dumps({"web": {"results": [{"url": URL, "title": "One"}]}}).encode())
    ]
    assert await BraveSearch("test-key").search("query", count=2) == (SearchHit(URL, "One", ""),)


@pytest.mark.parametrize("key", ["", "key\r\nHeader: value"])
def test_serper_rejects_invalid_credentials(key: str) -> None:
    with pytest.raises(ValueError, match="Serper API key"):
        SerperSearch(key)


async def test_serper_adapter_posts_json_and_returns_bounded_candidate_hits(
    net: NetworkFixture,
) -> None:
    wire = json_reply(
        json.dumps(
            {
                "organic": [
                    {"link": "http://elsewhere.example", "title": "skip", "snippet": "nope"},
                    {"link": URL, "title": "t" * 600, "snippet": "s" * 2500},
                    {"link": "https://ignored.example", "title": "past count", "snippet": "x"},
                ]
            }
        ).encode()
    )
    net.outcomes = [wire]
    search: SearchProvider = SerperSearch("serper-key")
    assert await search.search("NEM negative prices", count=1) == (
        SearchHit(URL, "t" * 500, "s" * 2000),
    )
    assert net.dns_calls == [("google.serper.dev.", 443)]
    assert net.connections == [(PUBLIC_IP, 443)]
    assert wire.hostname == "google.serper.dev"
    head, raw_body = wire.outgoing.split(b"\r\n\r\n", 1)
    method, target, _ = head.decode().split("\r\n", 1)[0].split(" ")
    assert method == "POST"
    assert target == "/search"
    assert b"x-api-key: serper-key" in head.lower()
    assert b"content-type: application/json" in head.lower()
    assert json.loads(raw_body) == {"q": "NEM negative prices", "num": 1}


async def test_serper_adapter_does_not_forward_search_credentials(net: NetworkFixture) -> None:
    net.outcomes = [redirect("https://other.example/token")]
    with pytest.raises(WebReadError, match="redirect limit reached"):
        await SerperSearch("serper-key").search("query")
    assert net.dns_calls == [("google.serper.dev.", 443)]


async def test_serper_adapter_reports_status_without_provider_response_body(
    net: NetworkFixture,
) -> None:
    net.outcomes = [Wire(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 6\r\n\r\nsecret")]
    with pytest.raises(WebReadError, match=r"^search provider returned HTTP 403$"):
        await SerperSearch("serper-key").search("query")


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"null",
        b'{"organic": {}}',
        b'{"organic": [null]}',
        b'{"organic": [{"link": 1, "title": "x"}]}',
    ],
)
async def test_serper_adapter_rejects_malformed_results(net: NetworkFixture, body: bytes) -> None:
    net.outcomes = [json_reply(body)]
    with pytest.raises(WebReadError, match="invalid search"):
        await SerperSearch("serper-key").search("query")


async def test_serper_adapter_allows_empty_or_partial_hits(net: NetworkFixture) -> None:
    net.outcomes = [
        json_reply(b"{}"),
        json_reply(json.dumps({"organic": [{"link": URL, "title": "One"}]}).encode()),
    ]
    net.answers = [[PUBLIC_IP], [PUBLIC_IP]]
    assert await SerperSearch("serper-key").search("query") == ()
    assert await SerperSearch("serper-key").search("query", count=2) == (SearchHit(URL, "One", ""),)
