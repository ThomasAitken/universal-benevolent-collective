"""Snapshot formatting and cancellation at the public-read entry point."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from ubc_node.browser.isolation.isolated_browser import PodmanBrowserRuntime
from ubc_node.browser.isolation.snapshot import SnapshotContent, SnapshotLink
from ubc_node.research.page_reader import PageHTTPError, PageReader
from ubc_node.research.public_web import (
    FetchLimits,
    FetchResult,
    PublicWebFetcher,
    Redirect,
    WebReadError,
)

_FETCH = FetchResult(
    "https://example.com/start",
    "https://example.org/reports/index",
    (Redirect(302, "https://example.org/reports/index"),),
    200,
    'Text/HTML; charset="ISO-8859-1"',
    b"<p>Evidence</p>",
    datetime(2026, 1, 1, tzinfo=UTC),
)


def _readers(response: FetchResult = _FETCH) -> tuple[PageReader, AsyncMock, AsyncMock, Mock]:
    fetch = AsyncMock(return_value=response)
    browser = Mock(
        render_snapshot=AsyncMock(
            return_value=SnapshotContent(
                "Evidence",
                "Rendered evidence",
                (
                    SnapshotLink("Table", " table#figures "),
                    SnapshotLink("Other", "//example.net/other"),
                    SnapshotLink("Local", "http://localhost/path"),
                    SnapshotLink("Invalid Unicode", "\ud800"),
                ),
                True,
                False,
            )
        ),
        revoke=AsyncMock(),
    )
    launch = AsyncMock(return_value=browser)
    reader = PageReader(
        cast(PublicWebFetcher, Mock(fetch=fetch)),
        cast(PodmanBrowserRuntime, Mock(launch_snapshot=launch)),
    )
    return reader, fetch, launch, browser


async def test_page_read_preserves_mediator_metadata_and_resolves_inert_links() -> None:
    reader, fetch, launch, browser = _readers()
    snapshot = await reader.read(_FETCH.requested_url, link_query=" Table ")
    assert snapshot.retrieval.requested_url == _FETCH.requested_url
    assert snapshot.retrieval.effective_url == _FETCH.effective_url
    assert snapshot.retrieval.redirects == _FETCH.redirects
    assert snapshot.retrieval.status == 200
    assert snapshot.retrieval.content_type == _FETCH.content_type
    assert snapshot.retrieval.retrieved_at == _FETCH.retrieved_at
    assert snapshot.retrieval.body_bytes == len(_FETCH.body)
    assert snapshot.retrieval.body_sha256 == hashlib.sha256(_FETCH.body).hexdigest()
    assert snapshot.title == "Evidence"
    assert snapshot.link_query == "Table"
    assert snapshot.text == "Rendered evidence"
    assert snapshot.untrusted and snapshot.text_truncated and not snapshot.links_truncated
    assert snapshot.links == (
        SnapshotLink("Table", "https://example.org/reports/table"),
        SnapshotLink("Other", "https://example.net/other"),
    )
    assert fetch.call_args.kwargs["limits"].max_body_bytes == 512 * 1024
    launch.assert_awaited_once()
    browser.render_snapshot.assert_awaited_once_with(
        _FETCH.body, plain_text=False, encoding="windows-1252", link_query="Table"
    )
    browser.revoke.assert_awaited_once()


async def test_text_and_unsupported_types() -> None:
    reader, _, launch, browser = _readers(replace(_FETCH, content_type="text/plain"))
    await reader.read(_FETCH.requested_url)
    browser.render_snapshot.assert_awaited_once_with(
        _FETCH.body, plain_text=True, encoding="utf-8-sig", link_query=""
    )
    await reader.stop()
    reader, _, launch, _ = _readers(replace(_FETCH, content_type=None))
    with pytest.raises(WebReadError, match="HTML or plain text"):
        await reader.read(_FETCH.requested_url)
    launch.assert_not_awaited()


def test_rejects_fetch_limit_larger_than_worker_payload() -> None:
    with pytest.raises(ValueError, match="snapshot byte limit"):
        PageReader(
            cast(PublicWebFetcher, Mock()),
            cast(PodmanBrowserRuntime, Mock()),
            limits=FetchLimits(),
        )


@pytest.mark.parametrize("stage", ["fetch", "launch", "render"])
async def test_stop_cancels_active_reads_and_permanently_closes_reader(stage: str) -> None:
    reader, fetch, launch, browser = _readers()
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    disposed = asyncio.Event()

    async def block(*_args: object, **_kwargs: object) -> None:
        entered.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
            await disposed.wait()

    {"fetch": fetch, "launch": launch, "render": browser.render_snapshot}[stage].side_effect = block
    async with asyncio.timeout(2):
        read = asyncio.create_task(reader.read(_FETCH.requested_url))
        await entered.wait()
        with pytest.raises(WebReadError, match="already active"):
            await reader.read(_FETCH.requested_url)
        stop = asyncio.create_task(reader.stop())
        await cancelled.wait()
        second_stop = asyncio.create_task(reader.stop())
        await asyncio.sleep(0)
        assert not stop.done() and not second_stop.done()
        disposed.set()
        await asyncio.gather(stop, second_stop)
        with pytest.raises(asyncio.CancelledError):
            await read
    assert browser.revoke.await_count == (1 if stage == "render" else 0)
    with pytest.raises(WebReadError, match="has stopped"):
        await reader.read(_FETCH.requested_url)


@pytest.mark.parametrize(
    ("status", "challenge", "message"),
    [(200, True, "browser challenge"), (403, True, "browser challenge"), (404, False, "HTTP 404")],
)
async def test_reports_publisher_error_with_metadata_without_launching_chromium(
    status: int, challenge: bool, message: str
) -> None:
    reader, _, launch, _ = _readers(replace(_FETCH, status=status, challenge_required=challenge))
    with pytest.raises(PageHTTPError, match=message) as error:
        await reader.read(_FETCH.requested_url)
    assert error.value.challenge_required is challenge
    assert error.value.retrieval.status == status
    assert error.value.retrieval.redirects == _FETCH.redirects
    assert error.value.retrieval.effective_url == _FETCH.effective_url
    launch.assert_not_awaited()


async def test_rejects_oversize_link_query_before_fetch() -> None:
    reader, fetch, _, _ = _readers()
    with pytest.raises(ValueError, match="link query"):
        await reader.read(_FETCH.requested_url, link_query="x" * 129)
    fetch.assert_not_awaited()


async def test_cancelling_read_directly_retires_in_flight_browser_session() -> None:
    reader, _, _, browser = _readers(replace(_FETCH, content_type="text/html; charset=cp1252"))
    entered = asyncio.Event()
    disposed = asyncio.Event()

    async def block(*_args: object, **_kwargs: object) -> None:
        entered.set()
        try:
            await asyncio.Future[None]()
        finally:
            await disposed.wait()

    browser.render_snapshot.side_effect = block
    async with asyncio.timeout(2):
        read = asyncio.create_task(reader.read(_FETCH.requested_url))
        await entered.wait()
        read.cancel()
        await asyncio.sleep(0)
        assert not read.done()
        disposed.set()
        with pytest.raises(asyncio.CancelledError):
            await read
    browser.revoke.assert_awaited_once()
    browser.render_snapshot.assert_awaited_once_with(
        _FETCH.body, plain_text=False, encoding="windows-1252", link_query=""
    )
