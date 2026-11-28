"""Bounded snapshot payloads shared by the node and the offline Chromium worker."""

import base64
import binascii
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from ubc_node.core.contracts import JsonValue

MAX_SNAPSHOT_BYTES = 512 * 1024
_MAX_TEXT = 32_000
_MAX_TITLE = 512
_MAX_LINKS = 48
_MAX_LINK_LABEL = 256
_MAX_LINK_QUERY = 128
_MAX_HREF = 1024
_ENCODINGS = {"utf-8-sig", "windows-1252"}
_STATIC_TAGS = frozenset(
    """
    a article b blockquote br caption code dd div dl dt em footer h1 h2 h3 h4 h5 h6
    header hr i li main nav ol p pre section span strong table tbody td th thead
    title tr ul
    """.split()
)
_OMIT_CONTENT = frozenset(
    {"iframe", "math", "noembed", "noframes", "noscript", "script", "style", "svg", "template"}
)
_VOID_TAGS = {"br", "hr"}
_DOCUMENT = (
    '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; base-uri 'none'; form-action 'none'\">"
)


@dataclass(frozen=True, slots=True)
class SnapshotLink:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class SnapshotContent:
    title: str
    text: str
    links: tuple[SnapshotLink, ...]
    text_truncated: bool
    links_truncated: bool


@dataclass(frozen=True, slots=True)
class SnapshotDocument:
    html: str
    link_query: str


def normalize_link_query(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_LINK_QUERY:
        raise ValueError("link query must be a string of at most 128 characters")
    return value.strip()


def _candidate_href(href: str) -> bool:
    stripped = href.strip()
    if not stripped:
        return False
    try:
        scheme = urlsplit(stripped).scheme
    except ValueError:
        return False
    return not scheme or scheme == "https"


class _StaticHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._omitted: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _OMIT_CONTENT:
            self._omitted.append(tag)
        if self._omitted or tag not in _STATIC_TAGS:
            return
        attribute = ""
        if tag == "a":
            href = next((value for name, value in attrs if name == "href"), None)
            if href and len(href) <= _MAX_HREF and _candidate_href(href):
                # Save the reference as data. Chromium never navigates to it.
                attribute = f' data-href="{escape(href, quote=True)}"'
        self.parts.append(f"<{tag}{attribute}>")

    def handle_endtag(self, tag: str) -> None:
        if self._omitted:
            for index in range(len(self._omitted) - 1, -1, -1):
                if self._omitted[index] == tag:
                    del self._omitted[index:]
                    break
            return
        if tag in _STATIC_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._omitted:
            self.parts.append(escape(data, quote=False))


def snapshot_document(parameters: Mapping[str, object]) -> SnapshotDocument:
    encoded = parameters.get("body")
    plain_text = parameters.get("plain_text")
    encoding = parameters.get("encoding")
    if (
        not isinstance(encoded, str)
        or len(encoded) > 4 * ((MAX_SNAPSHOT_BYTES + 2) // 3)
        or not isinstance(plain_text, bool)
        or not isinstance(encoding, str)
        or encoding not in _ENCODINGS
    ):
        raise ValueError("invalid snapshot payload")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("invalid snapshot body encoding") from error
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot body exceeds the byte limit")
    link_query = normalize_link_query(parameters.get("link_query", ""))
    source = body.decode(encoding, errors="replace").replace("\x00", "\ufffd")
    if plain_text:
        return SnapshotDocument(f"{_DOCUMENT}<pre>{escape(source, quote=False)}</pre>", link_query)
    parser = _StaticHTML()
    parser.feed(source)
    parser.close()
    return SnapshotDocument(_DOCUMENT + "".join(parser.parts), link_query)


def encode_snapshot(
    body: bytes, *, plain_text: bool, encoding: str, link_query: str = ""
) -> dict[str, JsonValue]:
    if len(body) > MAX_SNAPSHOT_BYTES or encoding not in _ENCODINGS:
        raise ValueError("invalid snapshot payload")
    return {
        "body": base64.b64encode(body).decode("ascii"),
        "plain_text": plain_text,
        "encoding": encoding,
        "link_query": normalize_link_query(link_query),
    }


def parse_snapshot(result: Mapping[str, JsonValue]) -> SnapshotContent:
    title, text = result.get("title"), result.get("text")
    links = result.get("links")
    text_truncated = result.get("text_truncated")
    links_truncated = result.get("links_truncated")
    if (
        not isinstance(title, str)
        or len(title) > _MAX_TITLE
        or not isinstance(text, str)
        or len(text) > _MAX_TEXT
        or not isinstance(links, list)
        or len(links) > _MAX_LINKS
        or not isinstance(text_truncated, bool)
        or not isinstance(links_truncated, bool)
    ):
        raise ValueError("invalid snapshot result")
    parsed: list[SnapshotLink] = []
    for link in links:
        if not isinstance(link, dict):
            raise ValueError("invalid snapshot link")
        label, href = link.get("label"), link.get("href")
        if (
            not isinstance(label, str)
            or len(label) > _MAX_LINK_LABEL
            or not isinstance(href, str)
            or len(href) > _MAX_HREF
        ):
            raise ValueError("invalid snapshot link")
        parsed.append(SnapshotLink(label, href))
    return SnapshotContent(title, text, tuple(parsed), text_truncated, links_truncated)


async def render_chromium(
    document: SnapshotDocument,
) -> Mapping[str, JsonValue]:  # pragma: no cover - Linux gate
    from contextlib import suppress

    from stagehand import Stagehand, local_browser

    profile = Path("/var/lib/ubc/profile/chromium")
    browser = await local_browser.launch(
        headless=True,
        user_data_dir=profile if profile.parent.is_dir() else None,
        chromium_sandbox=False,
        accept_downloads=False,
        args=("--disable-background-networking", "--no-proxy-server"),
    )
    try:
        stagehand = await Stagehand.create(browser=browser)
        try:
            page = await browser.context.new_page()
            # The document is generated inside the worker. Only this fixed CDP
            # expression executes. The page policy forbids scripts and resources.
            with tempfile.TemporaryDirectory() as directory:
                snapshot_path = Path(directory) / "snapshot.html"
                snapshot_path.write_text(document.html, encoding="utf-8")
                await page.goto(snapshot_path.as_uri(), wait_until="domcontentloaded")
            result = await page.evaluate(
                f"""(() => {{
                    const text = document.body?.innerText ?? "";
                    const query = {json.dumps(document.link_query)}.toLowerCase();
                    const links = [];
                    let linksTruncated = false;
                    for (const anchor of document.querySelectorAll("a[data-href]")) {{
                        const href = anchor.dataset.href;
                        if (query && !href.toLowerCase().includes(query)
                            && !anchor.textContent.toLowerCase().includes(query)) {{
                            continue;
                        }}
                        if (links.length === {_MAX_LINKS}) {{
                            linksTruncated = true;
                            break;
                        }}
                        links.push({{
                            href,
                            label: anchor.innerText.trim().slice(0, {_MAX_LINK_LABEL}).toWellFormed()
                        }});
                    }}
                    return {{
                        title: document.title.slice(0, {_MAX_TITLE}).toWellFormed(),
                        text: text.slice(0, {_MAX_TEXT}).toWellFormed(),
                        links,
                        text_truncated: text.length > {_MAX_TEXT},
                        links_truncated: linksTruncated
                    }};
                }})()"""
            )
            return cast(dict[str, JsonValue], result)
        finally:
            await stagehand.close()
    finally:
        # The broker retires the container if host policy denies a fallback kill.
        with suppress(PermissionError):
            await browser.close()
