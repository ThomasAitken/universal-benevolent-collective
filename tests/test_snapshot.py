"""Tests for the static document and bounded snapshot frames."""

import base64

import pytest

from ubc_node.browser.isolation.snapshot import (
    MAX_SNAPSHOT_BYTES,
    SnapshotLink,
    encode_snapshot,
    parse_snapshot,
    snapshot_document,
)
from ubc_node.core.contracts import JsonValue


def test_static_document_escapes_text_and_retains_inert_link_metadata() -> None:
    document = snapshot_document(
        encode_snapshot(
            b'<base href="https://example.org"><h1 class="header">Evidence &lt;x&gt;</h1>'
            b"<script>ignored</script><svg><style>ignored</style>also ignored</svg>"
            b"<noscript><svg>ignored</noscript>"
            b'<template><div>ignored</div></template><img src="/asset">'
            b'<a href="path?x=&quot;&amp;y" href="/second" onclick="ignored">Report</a>'
            b'<a href="https://example.org/ok">HTTPS</a>'
            b'<a href="javascript:alert(1)">js</a><a href="mailto:a@b.example">mail</a>'
            b'<a href="http://example.com/">http</a><a href="   ">blank</a>'
            b'<a href="https://[::1">bad</a><a href="">empty</a><br/><form><p>Text\x00</p></form>',
            plain_text=False,
            encoding="utf-8-sig",
        )
    )
    assert document.html.endswith(
        '<h1>Evidence &lt;x&gt;</h1><a data-href="path?x=&quot;&amp;y">Report</a>'
        '<a data-href="https://example.org/ok">HTTPS</a>'
        "<a>js</a><a>mail</a><a>http</a><a>blank</a><a>bad</a>"
        "<a>empty</a><br><p>Text\ufffd</p>"
    )
    assert "default-src 'none'" in document.html


def test_render_plain_text_and_bound_href() -> None:
    document = snapshot_document(
        encode_snapshot(b"\x80 <strong>", plain_text=True, encoding="windows-1252")
    )
    assert document.html.endswith("<pre>€ &lt;strong&gt;</pre>")
    assert snapshot_document(
        encode_snapshot(
            b'<a href="' + b"x" * 1025 + b'">Long</a>',
            plain_text=False,
            encoding="utf-8-sig",
        )
    ).html.endswith("<a>Long</a>")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("body", None, "payload"),
        ("link_query", False, "link query"),
        ("link_query", "x" * 129, "link query"),
        ("body", "A" * (4 * ((MAX_SNAPSHOT_BYTES + 2) // 3) + 1), "payload"),
        ("plain_text", 0, "payload"),
        ("encoding", None, "payload"),
        ("encoding", "unknown", "payload"),
        ("body", "!", "body encoding"),
        ("body", "☀", "body encoding"),
        ("body", base64.b64encode(b"x" * (MAX_SNAPSHOT_BYTES + 1)).decode(), "byte limit"),
    ],
    ids=[
        "body-type",
        "query-type",
        "query-length",
        "body-size",
        "mode",
        "encoding-type",
        "encoding",
        "base64",
        "unicode",
        "size",
    ],
)
def test_reject_invalid_snapshot_request(field: str, value: object, message: str) -> None:
    parameters: dict[str, object] = {"body": "", "plain_text": False, "encoding": "utf-8-sig"}
    parameters[field] = value
    with pytest.raises(ValueError, match=message):
        snapshot_document(parameters)


@pytest.mark.parametrize(
    ("body", "encoding"),
    [(b"", "unknown"), (b"0" * (MAX_SNAPSHOT_BYTES + 1), "utf-8-sig")],
    ids=["encoding", "size"],
)
def test_reject_invalid_snapshot_encoding(body: bytes, encoding: str) -> None:
    with pytest.raises(ValueError, match="payload"):
        encode_snapshot(body, plain_text=False, encoding=encoding)


_RESULT: dict[str, JsonValue] = {
    "title": "Title",
    "text": "Content",
    "links": [{"href": "/report", "label": "Report"}],
    "text_truncated": False,
    "links_truncated": False,
}


def test_decode_bounded_worker_result() -> None:
    parsed = parse_snapshot(_RESULT)
    assert parsed.links == (SnapshotLink("Report", "/report"),)
    assert parsed.text == "Content"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", None),
        ("title", "x" * 513),
        ("text", None),
        ("text", "x" * 32001),
        ("links", None),
        ("links", [None] * 49),
        ("links", [None]),
        ("links", [{"label": None}]),
        ("links", [{"label": "x" * 257}]),
        ("links", [{"label": "Report", "href": None}]),
        ("links", [{"label": "Report", "href": "x" * 1025}]),
        ("text_truncated", None),
        ("links_truncated", None),
    ],
)
def test_reject_malformed_worker_result(field: str, value: JsonValue) -> None:
    with pytest.raises(ValueError, match="invalid snapshot"):
        parse_snapshot({**_RESULT, field: value})
