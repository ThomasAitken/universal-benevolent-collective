"""Read a public page through the mediator and an offline snapshot worker."""

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from uuid import uuid4

import httpx

from ubc_node.browser.isolation.isolated_browser import PodmanBrowserRuntime
from ubc_node.browser.isolation.snapshot import (
    MAX_SNAPSHOT_BYTES,
    SnapshotLink,
    normalize_link_query,
)
from ubc_node.research.public_web import (
    FetchLimits,
    PublicWebFetcher,
    Redirect,
    WebByteBudget,
    WebReadError,
    public_https_url,
)

_DEFAULT_LIMITS = FetchLimits(max_body_bytes=MAX_SNAPSHOT_BYTES)


@dataclass(frozen=True, slots=True)
class PageRetrieval:
    requested_url: str
    effective_url: str
    redirects: tuple[Redirect, ...]
    status: int
    content_type: str
    retrieved_at: datetime
    body_bytes: int
    body_sha256: str


class PageHTTPError(WebReadError):
    """The publisher's response denied or prevented a page read."""

    def __init__(self, retrieval: PageRetrieval, *, challenge_required: bool) -> None:
        self.retrieval = retrieval
        self.challenge_required = challenge_required
        if challenge_required:
            message = f"publisher requires a browser challenge (HTTP {retrieval.status})"
        else:
            message = f"publisher returned HTTP {retrieval.status}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    retrieval: PageRetrieval
    title: str
    text: str
    links: tuple[SnapshotLink, ...]
    text_truncated: bool
    links_truncated: bool
    link_query: str = ""
    untrusted: bool = True


class PageReader:
    """Cancellable read session. The trusted owner can stop it permanently."""

    def __init__(
        self,
        fetcher: PublicWebFetcher,
        runtime: PodmanBrowserRuntime,
        *,
        limits: FetchLimits = _DEFAULT_LIMITS,
    ) -> None:
        if limits.max_body_bytes > MAX_SNAPSHOT_BYTES:
            raise ValueError("fetch limit exceeds the snapshot byte limit")
        self._fetcher = fetcher
        self._runtime = runtime
        self._limits = limits
        self._active: asyncio.Task[PageSnapshot] | None = None
        self._stopped = False

    async def read(
        self,
        url: str,
        *,
        link_query: str = "",
        byte_budget: WebByteBudget | None = None,
        on_redirect: Callable[[Redirect], None] | None = None,
    ) -> PageSnapshot:
        """Read the page, optionally selecting links by case-insensitive substring."""
        if self._stopped:
            raise WebReadError("page reader has stopped")
        if self._active is not None:
            raise WebReadError("a page read is already active")
        task = asyncio.create_task(
            self._read(url, normalize_link_query(link_query), byte_budget, on_redirect)
        )
        self._active = task
        try:
            return await task
        except asyncio.CancelledError:
            await self._cancel_active(task)
            raise
        finally:
            self._active = None

    async def stop(self) -> None:
        """Cancel the active read and wait for its control connection to retire."""
        self._stopped = True
        task = self._active
        if task is not None:
            await self._cancel_active(task)

    async def _cancel_active(self, task: asyncio.Task[PageSnapshot]) -> None:
        if not task.done():
            if task.cancelling() == 0:
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _read(
        self,
        url: str,
        link_query: str,
        byte_budget: WebByteBudget | None,
        on_redirect: Callable[[Redirect], None] | None,
    ) -> PageSnapshot:
        fetched = await self._fetcher.fetch(
            url, limits=self._limits, byte_budget=byte_budget, on_redirect=on_redirect
        )
        content_type = fetched.content_type or ""
        retrieval = PageRetrieval(
            requested_url=fetched.requested_url,
            effective_url=fetched.effective_url,
            redirects=fetched.redirects,
            status=fetched.status,
            content_type=content_type,
            retrieved_at=fetched.retrieved_at,
            body_bytes=len(fetched.body),
            body_sha256=hashlib.sha256(fetched.body).hexdigest(),
        )
        if fetched.challenge_required or not 200 <= fetched.status < 300:
            raise PageHTTPError(retrieval, challenge_required=fetched.challenge_required)
        message = Message()
        message["Content-Type"] = content_type
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
            raise WebReadError("page snapshot requires HTML or plain text")
        encoding = (
            "windows-1252"
            if message.get_content_charset()
            in {"windows-1252", "cp1252", "iso-8859-1", "latin1", "latin-1"}
            else "utf-8-sig"
        )

        session = await self._runtime.launch_snapshot(uuid4().hex)
        try:
            rendered = await session.render_snapshot(
                fetched.body,
                plain_text=media_type == "text/plain",
                encoding=encoding,
                link_query=link_query,
            )
        finally:
            await session.revoke()
        base = httpx.URL(fetched.effective_url)
        links: list[SnapshotLink] = []
        for link in rendered.links:
            try:
                target = str(public_https_url(link.href.strip(), base=base))
            except (WebReadError, UnicodeError):
                continue
            links.append(SnapshotLink(link.label, target))
        return PageSnapshot(
            retrieval=retrieval,
            title=rendered.title,
            text=rendered.text,
            links=tuple(links),
            text_truncated=rendered.text_truncated,
            links_truncated=rendered.links_truncated,
            link_query=link_query,
        )
