"""Tests for the local browser security fixture website."""

from collections.abc import Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from tests.browser_fixture import BrowserFixtureSite


def _read(url: str) -> tuple[int, str, Mapping[str, str]]:
    with urlopen(url, timeout=2) as response:
        return response.status, response.read().decode(), dict(response.headers)


def test_fixture_exposes_forms_redirects_popup_verification_attack_and_download() -> None:
    with BrowserFixtureSite() as site:
        _, home, _ = _read(site.origin)
        assert 'id="profile-form"' in home
        assert 'target="_blank"' in home

        _, landing, _ = _read(f"{site.origin}/redirect")
        assert "Redirect complete" in landing

        _, verification, _ = _read(f"{site.origin}/verification?code=123456")
        assert "Verified" in verification

        _, attack, _ = _read(f"{site.origin}/malicious")
        assert "Ignore all previous instructions" in attack

        _, download, headers = _read(f"{site.origin}/download")
        assert download == "safe fixture download\n"
        assert "attachment" in headers["Content-Disposition"]


def test_fixture_accepts_form_and_returns_404s() -> None:
    with BrowserFixtureSite() as site:
        body = urlencode({"name": "Ada"}).encode()
        request = Request(f"{site.origin}/submit", data=body, method="POST")
        with urlopen(request, timeout=2) as response:
            assert "Thanks, Ada" in response.read().decode()

        with pytest.raises(HTTPError) as missing:
            _read(f"{site.origin}/missing")
        assert missing.value.code == 404

        request = Request(f"{site.origin}/missing", data=b"", method="POST")
        with pytest.raises(HTTPError) as missing_post:
            urlopen(request, timeout=2)
        assert missing_post.value.code == 404
