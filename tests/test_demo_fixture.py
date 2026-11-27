"""Tests for the restart-stable Phase 5 loopback fixture."""

from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from ubc_node.demos.demo_fixture import Phase5FixtureSite


def _read(url: str) -> str:
    with urlopen(url, timeout=2) as response:
        return response.read().decode()


def test_fixture_serves_research_attack_download_redirect_and_errors() -> None:
    with Phase5FixtureSite() as fixture:
        assert "Research index" in _read(fixture.origin)
        assert "12 hectares" in _read(f"{fixture.origin}/source/alpha")
        assert "18 percent" in _read(f"{fixture.origin}/source/beta")
        assert "attacker.invalid" in _read(f"{fixture.origin}/malicious")
        assert "no pesticide" in _read(f"{fixture.origin}/document.txt")
        assert "Outside scope" in _read(f"{fixture.origin}/outside-redirect")
        assert "Human sign-in required" in _read(f"{fixture.origin}/login")

        with pytest.raises(HTTPError) as missing:
            _read(f"{fixture.origin}/missing")
        assert missing.value.code == 404

        request = Request(f"{fixture.origin}/missing", data=b"", method="POST")
        with pytest.raises(HTTPError) as missing_post:
            urlopen(request, timeout=2)
        assert missing_post.value.code == 404


def test_fixture_authentication_cookie_survives_server_restart() -> None:
    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))
    fixture = Phase5FixtureSite()
    research_port = int(fixture.origin.rsplit(":", 1)[1])
    outside_port = int(fixture.outside_origin.rsplit(":", 1)[1])
    with fixture:
        with opener.open(f"{fixture.origin}/private", timeout=2) as response:
            assert "Human sign-in required" in response.read().decode()

        empty = Request(
            f"{fixture.origin}/login",
            data=urlencode({"username": "", "password": ""}).encode(),
            method="POST",
        )
        with pytest.raises(HTTPError) as bad_login:
            opener.open(empty, timeout=2)
        assert bad_login.value.code == 400

        login = Request(
            f"{fixture.origin}/login",
            data=urlencode({"username": "person", "password": "secret-canary"}).encode(),
            method="POST",
        )
        with opener.open(login, timeout=2) as response:
            authenticated_page = response.read().decode()
            assert "four quarterly surveys" in authenticated_page
            assert "Return to the terminal" in authenticated_page
            assert "<code>return</code>" in authenticated_page

    with Phase5FixtureSite(
        research_port=research_port,
        outside_port=outside_port,
    ) as restarted:
        with opener.open(f"{restarted.origin}/private", timeout=2) as response:
            authenticated_page = response.read().decode()
            assert "four quarterly surveys" in authenticated_page
            assert "Return to the terminal" in authenticated_page
