"""Opt-in smoke test against a real local Stagehand browser."""

import os
from pathlib import Path

import pytest

from tests.browser_fixture import BrowserFixtureSite
from ubc_node.browser.browser_contracts import BrowserSessionConfig, BrowserSessionState
from ubc_node.browser.stagehand_browser import NavigationPolicyError, StagehandBrowserAdapter
from ubc_node.browser.supervised_browser import SupervisedBrowserSession
from ubc_node.core.contracts import JsonValue
from ubc_node.demos.demo_fixture import Phase5FixtureSite


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_stagehand_browser_against_local_fixture(tmp_path: Path) -> None:
    if os.environ.get("UBC_RUN_BROWSER_E2E") != "1":
        pytest.skip("set UBC_RUN_BROWSER_E2E=1 to run the real-browser smoke test")

    chrome_value = os.environ.get("CHROME_PATH")
    chrome_path = Path(chrome_value).resolve() if chrome_value else None
    if chrome_path is not None and not chrome_path.is_file():
        pytest.fail(f"CHROME_PATH does not name a file: {chrome_path}")

    with BrowserFixtureSite() as fixture:
        config = BrowserSessionConfig(
            workspace=tmp_path / "workspace",
            allowed_origins=frozenset({fixture.origin}),
            headless=True,
        )
        adapter = await StagehandBrowserAdapter.launch_local(
            config,
            chrome_path=chrome_path,
        )
        try:
            malicious = await adapter.open_url(f"{fixture.origin}/malicious")
            assert malicious.untrusted is True
            assert malicious.http_status == 200
            assert "Ignore all previous instructions" in malicious.content

            screenshot = await adapter.screenshot()
            assert (config.workspace / screenshot.relative_path).is_file()

            landing = await adapter.new_tab(f"{fixture.origin}/redirect")
            assert landing.url == f"{fixture.origin}/landing"
            assert len(await adapter.list_tabs()) == 2
            await adapter.close_tab(landing.tab_id)
        finally:
            await adapter.close()
        assert adapter.state is BrowserSessionState.CLOSED


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_supervised_browser_denies_before_outside_contact(tmp_path: Path) -> None:
    if os.environ.get("UBC_RUN_BROWSER_E2E") != "1":
        pytest.skip("set UBC_RUN_BROWSER_E2E=1 to run the real-browser smoke test")

    chrome_value = os.environ.get("CHROME_PATH")
    chrome_path = Path(chrome_value).resolve() if chrome_value else None
    events: list[tuple[str, dict[str, JsonValue]]] = []
    with Phase5FixtureSite() as fixture:
        config = BrowserSessionConfig(
            workspace=tmp_path / "supervised-workspace",
            allowed_origins=frozenset({fixture.origin}),
            headless=True,
        )
        browser = await SupervisedBrowserSession.launch_local(
            config,
            chrome_path=chrome_path,
            event_sink=lambda kind, payload: events.append((kind, dict(payload))),
        )
        alpha = await browser.open_url(f"{fixture.origin}/source/alpha")
        assert "12 hectares" in alpha.content
        with pytest.raises(NavigationPolicyError):
            await browser.open_url(f"{fixture.origin}/outside-redirect")
        assert fixture.outside_request_count == 0
        assert browser.state is BrowserSessionState.REVOKED
    assert any(
        kind == "egress_decided" and payload["decision"] == "deny" for kind, payload in events
    )
