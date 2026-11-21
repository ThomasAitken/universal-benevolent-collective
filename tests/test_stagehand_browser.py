"""Tests for the constrained Stagehand v4 browser adapter."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest

from tests.browser_fakes import (
    FakeBrowser,
    FakeContext,
    FakeLocator,
    FakeObservedAction,
    FakePage,
    FakeStagehand,
)
from ubc_node.browser_contracts import (
    BrowserContractError,
    BrowserSessionConfig,
    BrowserSessionState,
    ExtractionField,
    ExtractionSchema,
    ExtractionType,
    InteractionKind,
)
from ubc_node.contracts import ActionProposal, HumanHandoff, JsonValue
from ubc_node.stagehand_browser import (
    BrowserSessionError,
    BrowserSessionInactiveError,
    DownloadError,
    NavigationPolicyError,
    StagehandBrowserAdapter,
    UnknownCandidateError,
    UnknownTabError,
    _BrowserLike,  # pyright: ignore[reportPrivateUsage]
    _StagehandLike,  # pyright: ignore[reportPrivateUsage]
)

ORIGIN = "https://fixture.test"
HANDOFF = HumanHandoff("Sign in", ORIGIN, "Complete the fake verification")


def _adapter(
    tmp_path: Path,
    *,
    page: FakePage | None = None,
    config: BrowserSessionConfig | None = None,
    stagehand: FakeStagehand | None = None,
    context: FakeContext | None = None,
    handoff_handler: Callable[[HumanHandoff], Awaitable[None]] | None = None,
) -> tuple[StagehandBrowserAdapter, FakePage, FakeContext, FakeBrowser, FakeStagehand]:
    fake_page = page or FakePage("page-1", f"{ORIGIN}/")
    fake_context = context or FakeContext([fake_page])
    browser = FakeBrowser(fake_context)
    fake_stagehand = stagehand or FakeStagehand()
    session_config = config or BrowserSessionConfig(
        workspace=tmp_path / "workspace",
        allowed_origins=frozenset({ORIGIN}),
        max_inspection_chars=20,
        max_fill_chars=10,
        max_download_bytes=20,
    )
    adapter = StagehandBrowserAdapter(
        config=session_config,
        browser=cast(_BrowserLike, browser),
        stagehand=cast(_StagehandLike, fake_stagehand),
        handoff_handler=handoff_handler,
    )
    return adapter, fake_page, fake_context, browser, fake_stagehand


@pytest.mark.asyncio
async def test_open_inspect_and_screenshot_return_bounded_safe_values(tmp_path: Path) -> None:
    page = FakePage("page-1", content="x" * 30)
    adapter, page, _, _, _ = _adapter(tmp_path, page=page)

    inspection = await adapter.open_url(f"{ORIGIN}/malicious")
    assert inspection.url == f"{ORIGIN}/malicious"
    assert inspection.content == "x" * 20
    assert inspection.content_truncated is True
    assert inspection.untrusted is True
    assert page.goto_target is None

    artifact = await adapter.screenshot(full_page=True)
    assert artifact.relative_path.startswith("screenshots/")
    assert artifact.size_bytes == 3
    assert len(artifact.sha256) == 64

    page.write_screenshot = False
    fallback = await adapter.screenshot()
    assert (tmp_path / "workspace" / fallback.relative_path).read_bytes() == b"PNG"

    page.snapshot_as_model = True
    page.content = 123
    assert (await adapter.inspect()).content == "123"


@pytest.mark.asyncio
async def test_navigation_requires_approved_http_origin_and_revokes_after_redirect(
    tmp_path: Path,
) -> None:
    adapter, page, _, browser, stagehand = _adapter(tmp_path)

    with pytest.raises(NavigationPolicyError, match="not approved"):
        await adapter.open_url("https://other.test/")
    with pytest.raises(NavigationPolicyError, match="http"):
        await adapter.open_url("file:///tmp/secret")
    assert adapter.state is BrowserSessionState.ACTIVE

    page.goto_target = "https://other.test/redirected"
    with pytest.raises(NavigationPolicyError, match="unapproved"):
        await adapter.open_url(f"{ORIGIN}/redirect")
    assert adapter.state is BrowserSessionState.REVOKED
    assert browser.closed is True
    assert stagehand.closed is True


@pytest.mark.asyncio
async def test_non_http_page_reached_by_browser_is_revoked(tmp_path: Path) -> None:
    adapter, page, _, _, _ = _adapter(tmp_path)
    page.current_url = "about:blank"

    with pytest.raises(NavigationPolicyError, match="http"):
        await adapter.inspect()
    assert adapter.state is BrowserSessionState.REVOKED


@pytest.mark.asyncio
async def test_observe_returns_opaque_supported_candidates_only(tmp_path: Path) -> None:
    stagehand = FakeStagehand()
    stagehand.observed = (
        FakeObservedAction("#button", "Submit", "click"),
        FakeObservedAction("#input", "Name", "type"),
        FakeObservedAction("#fill", "Email", "FILL"),
        FakeObservedAction("#hover", "Hover", "hover"),
        FakeObservedAction("", "Missing selector", "click"),
        FakeObservedAction("#unknown", "No method", None),
    )
    adapter, _, _, _, _ = _adapter(tmp_path, stagehand=stagehand)

    candidates = await adapter.observe("Find safe controls")

    assert [candidate.kind for candidate in candidates] == [
        InteractionKind.CLICK,
        InteractionKind.FILL,
        InteractionKind.FILL,
    ]
    assert all(not candidate.candidate_id.startswith("#") for candidate in candidates)
    assert stagehand.observe_calls[0][0] == "Find safe controls"

    with pytest.raises(BrowserContractError, match="instruction"):
        await adapter.observe(" ")


@pytest.mark.asyncio
async def test_click_and_fill_require_current_observed_candidate(tmp_path: Path) -> None:
    stagehand = FakeStagehand()
    stagehand.observed = (
        FakeObservedAction("#button", "Submit", "click"),
        FakeObservedAction("#input", "Name", "fill"),
    )
    adapter, page, context, _, _ = _adapter(tmp_path, stagehand=stagehand)
    click, fill = await adapter.observe("Find controls")

    result = await adapter.click(click.candidate_id)
    assert result.url == f"{ORIGIN}/"
    assert page.locator("#button").clicks == 1
    with pytest.raises(UnknownCandidateError, match="stale"):
        await adapter.click(click.candidate_id)

    _, fill = await adapter.observe("Find controls")
    await adapter.fill(fill.candidate_id, "Ada")
    assert page.locator("#input").fills == ["Ada"]

    _, fill = await adapter.observe("Find controls")
    with pytest.raises(UnknownCandidateError, match="not click"):
        await adapter.click(fill.candidate_id)
    with pytest.raises(BrowserContractError, match="limit"):
        await adapter.fill(fill.candidate_id, "x" * 11)

    other = FakePage("page-2", f"{ORIGIN}/other")
    context.all_pages.append(other)
    context.active = other
    with pytest.raises(UnknownCandidateError, match="different"):
        await adapter.fill(fill.candidate_id, "Ada")


@pytest.mark.asyncio
async def test_extract_builds_and_validates_constrained_schema(tmp_path: Path) -> None:
    stagehand = FakeStagehand()
    stagehand.extract_values = {
        "title": "Example",
        "count": 2,
        "score": 4.5,
        "safe": True,
        "note": None,
    }
    adapter, _, _, _, _ = _adapter(tmp_path, stagehand=stagehand)
    schema = ExtractionSchema(
        (
            ExtractionField("title", ExtractionType.STRING),
            ExtractionField("count", ExtractionType.INTEGER),
            ExtractionField("score", ExtractionType.NUMBER),
            ExtractionField("safe", ExtractionType.BOOLEAN),
            ExtractionField("note", ExtractionType.STRING, required=False),
        )
    )

    result = await adapter.extract("Extract the fixture fields", schema)
    assert result.data == stagehand.extract_values
    assert set(stagehand.extract_calls[0][1].model_fields) == set(stagehand.extract_values)

    with pytest.raises(BrowserContractError, match="instruction"):
        await adapter.extract(" ", schema)


@pytest.mark.asyncio
async def test_tabs_use_opaque_tokens_and_reject_unknown_or_unapproved_pages(
    tmp_path: Path,
) -> None:
    first = FakePage("internal-a", f"{ORIGIN}/", title="First")
    second = FakePage("internal-b", f"{ORIGIN}/second", title="Second")
    context = FakeContext([first, second])
    adapter, _, context, _, _ = _adapter(tmp_path, page=first, context=context)

    tabs = await adapter.list_tabs()
    assert len(tabs) == 2
    assert tabs[0].tab_id != "internal-a"
    assert tabs[0].active is True

    switched = await adapter.switch_tab(tabs[1].tab_id)
    assert switched.active is True
    assert context.active is second

    opened = await adapter.new_tab(f"{ORIGIN}/new")
    assert opened.url == f"{ORIGIN}/new"
    await adapter.close_tab(opened.tab_id)
    assert context.all_pages[-1].closed is True

    with pytest.raises(UnknownTabError):
        await adapter.switch_tab("missing")
    with pytest.raises(UnknownTabError):
        await adapter.close_tab("missing")

    second.closed = True
    remaining = await adapter.list_tabs()
    assert len(remaining) == 1

    context.all_pages.append(FakePage("popup", "https://other.test/popup"))
    with pytest.raises(NavigationPolicyError, match="unapproved"):
        await adapter.list_tabs()
    assert adapter.state is BrowserSessionState.REVOKED


@pytest.mark.asyncio
async def test_blank_tabs_are_listed_and_ignored_during_resume(tmp_path: Path) -> None:
    blank = FakePage("blank", "about:blank", title="")
    context = FakeContext([blank])
    adapter, _, _, _, _ = _adapter(tmp_path, page=blank, context=context)

    assert (await adapter.list_tabs())[0].url == "about:blank"
    await adapter.pause()
    await adapter.resume()
    assert adapter.state is BrowserSessionState.ACTIVE


@pytest.mark.asyncio
async def test_new_tab_rejects_bad_initial_and_redirected_origins(tmp_path: Path) -> None:
    adapter, _, context, _, _ = _adapter(tmp_path)
    with pytest.raises(NavigationPolicyError):
        await adapter.new_tab("https://other.test/")

    context.new_page_url_override = "https://other.test/redirect"
    with pytest.raises(NavigationPolicyError):
        await adapter.new_tab(f"{ORIGIN}/redirect")
    assert adapter.state is BrowserSessionState.REVOKED


@pytest.mark.asyncio
async def test_closing_only_tab_leaves_no_active_page(tmp_path: Path) -> None:
    adapter, _, _, _, _ = _adapter(tmp_path)
    only_tab = (await adapter.list_tabs())[0]
    await adapter.close_tab(only_tab.tab_id)

    with pytest.raises(BrowserSessionError, match="no active"):
        await adapter.inspect()


async def _observe_download_candidate(
    adapter: StagehandBrowserAdapter, stagehand: FakeStagehand
) -> str:
    stagehand.observed = (FakeObservedAction("#download", "Download", "click"),)
    return (await adapter.observe("Find download"))[0].candidate_id


@pytest.mark.asyncio
async def test_download_is_confined_hashed_and_size_limited(tmp_path: Path) -> None:
    stagehand = FakeStagehand()
    adapter, page, _, _, _ = _adapter(tmp_path, stagehand=stagehand)
    downloads = tmp_path / "workspace" / "downloads"

    async def write_download() -> None:
        (downloads / "fixture.txt").write_bytes(b"fixture")

    page.locators["#download"] = FakeLocator(on_click=write_download)
    candidate_id = await _observe_download_candidate(adapter, stagehand)
    artifact = await adapter.download(candidate_id, timeout_seconds=1)
    assert artifact.relative_path == "downloads/fixture.txt"
    assert artifact.size_bytes == 7

    with pytest.raises(BrowserContractError, match="positive"):
        await adapter.download("anything", timeout_seconds=0)

    candidate_id = await _observe_download_candidate(adapter, stagehand)
    page.locators["#download"] = FakeLocator()
    with pytest.raises(DownloadError, match="timeout"):
        await adapter.download(candidate_id, timeout_seconds=0.06)

    async def write_oversized() -> None:
        (downloads / "oversized.txt").write_bytes(b"x" * 21)

    page.locators["#download"] = FakeLocator(on_click=write_oversized)
    candidate_id = await _observe_download_candidate(adapter, stagehand)
    with pytest.raises(DownloadError, match="size"):
        await adapter.download(candidate_id, timeout_seconds=1)
    assert not (downloads / "oversized.txt").exists()


@pytest.mark.asyncio
async def test_artifact_path_escape_is_rejected(tmp_path: Path) -> None:
    adapter, _, _, _, _ = _adapter(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")

    with pytest.raises(DownloadError, match="escaped"):
        adapter._artifact(outside)  # pyright: ignore[reportPrivateUsage]

    adapter._forget_page(FakePage("never-seen"))  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_pause_resume_close_and_revoke_are_enforced(tmp_path: Path) -> None:
    adapter, _, _, browser, stagehand = _adapter(tmp_path)

    await adapter.pause()
    assert adapter.state is BrowserSessionState.PAUSED
    with pytest.raises(BrowserSessionInactiveError, match="paused"):
        await adapter.inspect()
    with pytest.raises(BrowserSessionInactiveError, match="pause"):
        await adapter.pause()

    await adapter.resume()
    assert adapter.state is BrowserSessionState.ACTIVE
    with pytest.raises(BrowserSessionInactiveError, match="resume"):
        await adapter.resume()

    await adapter.close()
    await adapter.close()
    assert adapter.state is BrowserSessionState.CLOSED
    assert stagehand.closed is True
    assert browser.close_calls == 1

    await adapter.revoke()
    await adapter.revoke()
    assert adapter.state is BrowserSessionState.REVOKED
    assert browser.close_calls == 1


@pytest.mark.asyncio
async def test_shutdown_skips_browser_already_closed(tmp_path: Path) -> None:
    adapter, _, _, browser, _ = _adapter(tmp_path)
    browser.closed = True
    await adapter.close()
    assert browser.close_calls == 0


@pytest.mark.asyncio
async def test_handoff_pauses_resumes_and_revokes_on_failure(tmp_path: Path) -> None:
    handled: list[HumanHandoff] = []

    async def handler(request: HumanHandoff) -> None:
        handled.append(request)

    adapter, _, _, _, _ = _adapter(tmp_path, handoff_handler=handler)
    result = await adapter.handoff(HANDOFF)
    assert handled == [HANDOFF]
    assert result["untrusted"] is True
    assert adapter.state is BrowserSessionState.ACTIVE

    adapter_without_handler, _, _, _, _ = _adapter(tmp_path / "no-handler")
    with pytest.raises(BrowserSessionError, match="handler"):
        await adapter_without_handler.handoff(HANDOFF)

    async def failing_handler(_: HumanHandoff) -> None:
        raise RuntimeError("human aborted")

    failing, _, _, _, _ = _adapter(tmp_path / "failure", handoff_handler=failing_handler)
    with pytest.raises(RuntimeError, match="aborted"):
        await failing.handoff(HANDOFF)
    assert failing.state is BrowserSessionState.REVOKED

    revoking: StagehandBrowserAdapter | None = None

    async def revoking_handler(_: HumanHandoff) -> None:
        assert revoking is not None
        await revoking.revoke()

    revoking, _, _, _, _ = _adapter(tmp_path / "revoking", handoff_handler=revoking_handler)
    with pytest.raises(BrowserSessionInactiveError, match="revoked"):
        await revoking.handoff(HANDOFF)
    assert revoking.state is BrowserSessionState.REVOKED


def _proposal(operation: str, **parameters: JsonValue) -> ActionProposal:
    payload: dict[str, JsonValue] = {"operation": operation, **parameters}
    return ActionProposal.browser(operation, payload)


@pytest.mark.asyncio
async def test_phase_zero_execute_bridge_supports_browser_operations(tmp_path: Path) -> None:
    stagehand = FakeStagehand()
    adapter, page, _, _, _ = _adapter(tmp_path, stagehand=stagehand)

    opened = await adapter.execute(_proposal("open", url=f"{ORIGIN}/page"))
    assert opened["url"] == f"{ORIGIN}/page"
    assert (await adapter.execute(_proposal("inspect")))["untrusted"] is True
    assert "relative_path" in await adapter.execute(_proposal("screenshot", full_page=True))

    stagehand.observed = (FakeObservedAction("#button", "Button", "click"),)
    observed = await adapter.execute(_proposal("observe", instruction="Find button"))
    candidate = cast(list[dict[str, str]], observed["candidates"])[0]["candidate_id"]
    assert "url" in await adapter.execute(_proposal("click", candidate_id=candidate))

    stagehand.observed = (FakeObservedAction("#input", "Input", "fill"),)
    observed = await adapter.execute(_proposal("observe", instruction="Find input"))
    candidate = cast(list[dict[str, str]], observed["candidates"])[0]["candidate_id"]
    assert "url" in await adapter.execute(_proposal("fill", candidate_id=candidate, value="Ada"))

    stagehand.extract_values = {"title": "Example"}
    extracted = await adapter.execute(
        _proposal(
            "extract",
            instruction="Extract title",
            schema=[{"name": "title", "type": "string"}],
        )
    )
    assert extracted == {"data": {"title": "Example"}}

    tabs = await adapter.execute(_proposal("tabs"))
    assert len(cast(list[object], tabs["tabs"])) == 1
    new_tab = await adapter.execute(_proposal("new_tab", url=f"{ORIGIN}/new"))
    tab_id = cast(str, new_tab["tab_id"])
    assert (await adapter.execute(_proposal("switch_tab", tab_id=tab_id)))["active"] is True
    assert await adapter.execute(_proposal("close_tab", tab_id=tab_id)) == {"closed": True}

    downloads = tmp_path / "workspace" / "downloads"

    async def write_download() -> None:
        (downloads / "bridge.txt").write_text("bridge")

    page.locators["#download"] = FakeLocator(on_click=write_download)
    stagehand.observed = (FakeObservedAction("#download", "Download", "click"),)
    observed = await adapter.execute(_proposal("observe", instruction="Find download"))
    candidate = cast(list[dict[str, str]], observed["candidates"])[0]["candidate_id"]
    assert "sha256" in await adapter.execute(_proposal("download", candidate_id=candidate))

    assert await adapter.execute(_proposal("pause")) == {"state": "paused"}
    assert await adapter.execute(_proposal("resume")) == {"state": "active"}
    assert await adapter.execute(_proposal("close")) == {"state": "closed"}

    revoked, _, _, _, _ = _adapter(tmp_path / "revoked")
    assert await revoked.execute(_proposal("revoke")) == {"state": "revoked"}


@pytest.mark.asyncio
async def test_execute_bridge_rejects_invalid_operations_and_parameters(tmp_path: Path) -> None:
    adapter, _, _, _, _ = _adapter(tmp_path)

    with pytest.raises(BrowserContractError, match="only accepts"):
        await adapter.execute(ActionProposal.complete("done"))
    with pytest.raises(BrowserContractError, match="operation"):
        await adapter.execute(ActionProposal.browser("missing operation"))
    with pytest.raises(BrowserContractError, match="unsupported"):
        await adapter.execute(_proposal("raw_cdp"))
    with pytest.raises(BrowserContractError, match="boolean"):
        await adapter.execute(_proposal("screenshot", full_page="yes"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        "not-a-list",
        ["not-an-object"],
        [{"name": 1, "type": "string"}],
        [{"name": "field", "type": "string", "required": "yes"}],
        [{"name": "field", "type": "unsupported"}],
        [],
    ],
)
async def test_execute_bridge_rejects_invalid_extraction_schemas(
    tmp_path: Path, schema: JsonValue
) -> None:
    adapter, _, _, _, _ = _adapter(tmp_path)
    with pytest.raises(BrowserContractError):
        await adapter.execute(_proposal("extract", instruction="Extract", schema=schema))
