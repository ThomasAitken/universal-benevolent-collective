"""Policy-shaped Stagehand v4 adapter with no raw browser escape hatches."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, create_model

from ubc_node.browser_contracts import (
    ArtifactRef,
    BrowserContractError,
    BrowserProfileMode,
    BrowserSessionConfig,
    BrowserSessionState,
    ExtractionField,
    ExtractionResult,
    ExtractionSchema,
    ExtractionType,
    InteractionCandidate,
    InteractionKind,
    PageInspection,
    TabInfo,
    origin_for_url,
)
from ubc_node.contracts import (
    ActionKind,
    ActionProposal,
    FatalRunError,
    HumanHandoff,
    JsonValue,
    TaskSpec,
)
from ubc_node.takeover import (
    ClipboardClearer,
    TakeoverDecision,
    TakeoverHandler,
    TakeoverOutcome,
    TakeoverPromptContext,
    clear_system_clipboard,
)


class BrowserSessionError(RuntimeError):
    """Base error for browser session failures."""


class BrowserSessionInactiveError(BrowserSessionError, FatalRunError):
    """Raised when an operation is attempted outside the active state."""


class NavigationPolicyError(BrowserSessionError, FatalRunError):
    """Raised after an unapproved navigation causes session revocation."""


class UnknownCandidateError(BrowserSessionError):
    """Raised when an opaque interaction token is absent, stale, or unsuitable."""


class UnknownTabError(BrowserSessionError):
    """Raised when a UBC tab token is not part of this session."""


class DownloadError(BrowserSessionError):
    """Raised when a download is missing or violates workspace limits."""


class _LocatorLike(Protocol):
    async def click(self) -> None: ...

    async def fill(self, value: str) -> None: ...


class _PageLike(Protocol):
    page_id: str

    async def goto(self, url: str, *, wait_until: str | None = None) -> object: ...

    async def url(self) -> str: ...

    async def title(self) -> str: ...

    async def snapshot(self, *, include_iframes: bool | None = None) -> object: ...

    async def screenshot(
        self,
        *,
        full_page: bool | None = None,
        path: str | Path | None = None,
        type: str | None = None,
    ) -> bytes: ...

    def locator(self, selector: str) -> _LocatorLike: ...

    async def close(self) -> None: ...


class _ContextLike(Protocol):
    async def pages(self) -> list[_PageLike]: ...

    async def active_page(self) -> _PageLike | None: ...

    async def new_page(self, url: str | None = None) -> _PageLike: ...

    async def set_active_page(self, page: _PageLike) -> None: ...


class _BrowserLike(Protocol):
    context: _ContextLike
    closed: bool

    async def close(self) -> None: ...


class _ObservedActionLike(Protocol):
    selector: str
    description: str
    method: str | None


class _SnapshotLike(Protocol):
    formatted_tree: object


class _ObserveResultLike(Protocol):
    data: Sequence[_ObservedActionLike]


class _ExtractResultLike(Protocol):
    data: BaseModel


class _StagehandLike(Protocol):
    async def observe(self, instruction: str, *, page: _PageLike) -> _ObserveResultLike: ...

    async def extract(
        self,
        instruction: str,
        schema: type[BaseModel],
        *,
        page: _PageLike,
    ) -> _ExtractResultLike: ...

    async def close(self) -> None: ...


type HandoffHandler = Callable[[HumanHandoff], Awaitable[None]]

_CLEAR_TRANSIENT_FORMS_SCRIPT = """() => {
  const fields = document.querySelectorAll(
    'input[type="password"], input[type="text"], input[type="email"], ' +
    'input[type="tel"], input[type="search"], input[type="number"], ' +
    'input:not([type]), textarea'
  );
  for (const field of fields) {
    field.value = '';
  }
}"""


@dataclass(frozen=True, slots=True)
class _CandidateTarget:
    tab_id: str
    selector: str
    kind: InteractionKind


class StagehandBrowserAdapter:
    """Expose only UBC-approved operations over a private Stagehand session."""

    def __init__(
        self,
        *,
        config: BrowserSessionConfig,
        browser: _BrowserLike,
        stagehand: _StagehandLike,
        handoff_handler: HandoffHandler | None = None,
        takeover_handler: TakeoverHandler | None = None,
        clipboard_clearer: ClipboardClearer | None = None,
    ) -> None:
        self._config = config
        self._browser = browser
        self._stagehand = stagehand
        self._handoff_handler = handoff_handler
        self._takeover_handler = takeover_handler
        self._clipboard_clearer = clipboard_clearer or clear_system_clipboard
        self._state = BrowserSessionState.ACTIVE
        self._tab_tokens: dict[str, str] = {}
        self._pages: dict[str, _PageLike] = {}
        self._candidates: dict[str, _CandidateTarget] = {}
        self._downloads_dir = config.workspace / "downloads"
        self._screenshots_dir = config.workspace / "screenshots"
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    async def launch_local(
        cls,
        config: BrowserSessionConfig,
        *,
        model: str | None = None,
        model_api_key: str | None = None,
        chrome_path: Path | None = None,
        handoff_handler: HandoffHandler | None = None,
        takeover_handler: TakeoverHandler | None = None,
        clipboard_clearer: ClipboardClearer | None = None,
        proxy_server: str | None = None,
        proxy_bypass: str | None = None,
        temporary_profile_path: Path | None = None,
        debugging_port: int | None = None,
        chromium_sandbox: bool | None = None,
        accept_downloads: bool = True,
    ) -> "StagehandBrowserAdapter":  # pragma: no cover - exercised by opt-in browser test
        """Launch the pinned Stagehand local browser with a policy-bound profile."""
        from stagehand import Stagehand, local_browser

        if (
            temporary_profile_path is not None
            and config.profile_mode is not BrowserProfileMode.TEMPORARY
        ):
            raise BrowserContractError(
                "an explicit temporary profile path requires temporary profile mode"
            )
        profile_path = config.profile_path or temporary_profile_path
        if profile_path is not None:
            profile_path.mkdir(parents=True, exist_ok=True, mode=0o700)

        browser = await local_browser.launch(
            port=debugging_port,
            executable_path=chrome_path,
            user_data_dir=profile_path,
            preserve_user_data_dir=config.profile_mode is BrowserProfileMode.PERSISTENT,
            headless=config.headless,
            downloads_path=config.workspace / "downloads",
            accept_downloads=accept_downloads,
            chromium_sandbox=chromium_sandbox,
            proxy_server=proxy_server,
            proxy_bypass=proxy_bypass,
        )
        try:
            stagehand = await Stagehand.create(
                browser=browser,
                model=model,
                model_api_key=model_api_key,
            )
        except Exception:
            await browser.close()
            raise
        return cls(
            config=config,
            browser=cast(_BrowserLike, browser),
            stagehand=cast(_StagehandLike, stagehand),
            handoff_handler=handoff_handler,
            takeover_handler=takeover_handler,
            clipboard_clearer=clipboard_clearer,
        )

    @property
    def state(self) -> BrowserSessionState:
        """Return the capability-session state."""
        return self._state

    async def open_url(self, url: str) -> PageInspection:
        """Navigate the active tab to a pre-approved origin."""
        self._require_active()
        self._require_approved_url(url)
        page = await self._active_page()
        self._invalidate_candidates()
        await page.goto(url, wait_until="domcontentloaded")
        await self._enforce_page_origin(page)
        return await self.inspect()

    async def inspect(self) -> PageInspection:
        """Return a bounded snapshot labeled as untrusted page content."""
        self._require_active()
        page = await self._active_page()
        await self._enforce_page_origin(page)
        tab_id = self._token_for_page(page)
        url, title, snapshot = await asyncio.gather(page.url(), page.title(), page.snapshot())
        if isinstance(snapshot, Mapping):
            content_value = cast(Mapping[str, object], snapshot).get("formatted_tree", "")
        else:
            content_value = cast(_SnapshotLike, snapshot).formatted_tree
        content = content_value if isinstance(content_value, str) else str(content_value)
        truncated = len(content) > self._config.max_inspection_chars
        return PageInspection(
            tab_id=tab_id,
            url=url,
            title=title,
            content=content[: self._config.max_inspection_chars],
            content_truncated=truncated,
        )

    async def screenshot(self, *, full_page: bool = False) -> ArtifactRef:
        """Save a screenshot inside the task workspace."""
        self._require_active()
        page = await self._active_page()
        await self._enforce_page_origin(page)
        target = self._screenshots_dir / f"{uuid4()}.png"
        image = await page.screenshot(full_page=full_page, path=target, type="png")
        if not target.exists():
            target.write_bytes(image)
        return self._artifact(target)

    async def observe(self, instruction: str) -> tuple[InteractionCandidate, ...]:
        """Use Stagehand only to identify candidates; retain selectors privately."""
        self._require_active()
        if not instruction.strip():
            raise BrowserContractError("observe instruction must not be empty")
        page = await self._active_page()
        await self._enforce_page_origin(page)
        self._invalidate_candidates()
        tab_id = self._token_for_page(page)
        observed = await self._stagehand.observe(instruction, page=page)
        candidates: list[InteractionCandidate] = []
        for action in observed.data:
            kind = self._interaction_kind(action.method)
            if kind is None or not action.selector:
                continue
            candidate_id = str(uuid4())
            self._candidates[candidate_id] = _CandidateTarget(tab_id, action.selector, kind)
            candidates.append(InteractionCandidate(candidate_id, action.description, kind))
        return tuple(candidates)

    async def click(self, candidate_id: str) -> PageInspection:
        """Execute a deterministic click on an observed opaque candidate."""
        page, target = await self._candidate(candidate_id, InteractionKind.CLICK)
        await page.locator(target.selector).click()
        self._invalidate_candidates()
        await self._enforce_page_origin(page)
        return await self.inspect()

    async def fill(self, candidate_id: str, value: str) -> PageInspection:
        """Fill an observed input without retaining or returning the value."""
        if len(value) > self._config.max_fill_chars:
            raise BrowserContractError("fill value exceeds the session limit")
        page, target = await self._candidate(candidate_id, InteractionKind.FILL)
        await page.locator(target.selector).fill(value)
        self._invalidate_candidates()
        return await self.inspect()

    async def extract(self, instruction: str, schema: ExtractionSchema) -> ExtractionResult:
        """Extract data through a constrained dynamically generated Pydantic schema."""
        self._require_active()
        if not instruction.strip():
            raise BrowserContractError("extract instruction must not be empty")
        page = await self._active_page()
        await self._enforce_page_origin(page)
        model_type = self._extraction_model(schema)
        result = await self._stagehand.extract(instruction, model_type, page=page)
        data = cast(dict[str, JsonValue], result.data.model_dump(mode="json"))
        return ExtractionResult(data)

    async def list_tabs(self) -> tuple[TabInfo, ...]:
        """List approved tabs using only opaque UBC identifiers."""
        self._require_active()
        pages = await self._sync_tabs()
        active = await self._browser.context.active_page()
        tabs: list[TabInfo] = []
        for page in pages:
            url = await page.url()
            if url != "about:blank":
                await self._enforce_page_origin(page)
            tabs.append(
                TabInfo(
                    tab_id=self._token_for_page(page),
                    url=url,
                    title=await page.title(),
                    active=active is not None and page.page_id == active.page_id,
                )
            )
        return tuple(tabs)

    async def new_tab(self, url: str) -> TabInfo:
        """Open an approved URL in a new active tab."""
        self._require_active()
        self._require_approved_url(url)
        page = await self._browser.context.new_page(url)
        await self._browser.context.set_active_page(page)
        self._invalidate_candidates()
        await self._enforce_page_origin(page)
        return TabInfo(self._token_for_page(page), await page.url(), await page.title(), True)

    async def switch_tab(self, tab_id: str) -> TabInfo:
        """Switch to a tab selected by its UBC token."""
        self._require_active()
        await self._sync_tabs()
        page = self._pages.get(tab_id)
        if page is None:
            raise UnknownTabError(f"unknown tab: {tab_id}")
        await self._enforce_page_origin(page)
        await self._browser.context.set_active_page(page)
        self._invalidate_candidates()
        return TabInfo(tab_id, await page.url(), await page.title(), True)

    async def close_tab(self, tab_id: str) -> None:
        """Close one tab without exposing its underlying page object."""
        self._require_active()
        await self._sync_tabs()
        page = self._pages.get(tab_id)
        if page is None:
            raise UnknownTabError(f"unknown tab: {tab_id}")
        await page.close()
        self._forget_page(page)
        self._invalidate_candidates()
        remaining = await self._browser.context.pages()
        if remaining and await self._browser.context.active_page() is None:
            await self._browser.context.set_active_page(remaining[0])

    async def download(self, candidate_id: str, *, timeout_seconds: float = 10) -> ArtifactRef:
        """Click an observed download and return only its task-workspace artifact."""
        if timeout_seconds <= 0:
            raise BrowserContractError("download timeout must be positive")
        page, target = await self._candidate(candidate_id, InteractionKind.CLICK)
        existing = {path.name for path in self._downloads_dir.iterdir()}
        await page.locator(target.selector).click()
        self._invalidate_candidates()
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            completed = [
                path
                for path in self._downloads_dir.iterdir()
                if path.name not in existing
                and not path.name.endswith(".crdownload")
                and path.is_file()
            ]
            if completed:
                newest = max(completed, key=lambda path: path.stat().st_mtime_ns)
                if newest.stat().st_size > self._config.max_download_bytes:
                    newest.unlink()
                    raise DownloadError("download exceeded the session size limit")
                return self._artifact(newest)
            await asyncio.sleep(0.05)
        raise DownloadError("download did not complete before the timeout")

    async def pause(self) -> None:
        """Suspend all model-driven browser operations."""
        if self._state is not BrowserSessionState.ACTIVE:
            raise BrowserSessionInactiveError(f"cannot pause a {self._state} session")
        self._state = BrowserSessionState.PAUSED
        self._invalidate_candidates()

    async def resume(self) -> None:
        """Return a paused session to active operation."""
        if self._state is not BrowserSessionState.PAUSED:
            raise BrowserSessionInactiveError(f"cannot resume a {self._state} session")
        self._state = BrowserSessionState.ACTIVE
        pages = await self._sync_tabs()
        for page in pages:
            url = await page.url()
            if url != "about:blank":
                await self._enforce_page_origin(page)

    async def close(self) -> None:
        """Close the Stagehand client and browser; repeated closes are safe."""
        if self._state in {BrowserSessionState.CLOSED, BrowserSessionState.REVOKED}:
            return
        self._state = BrowserSessionState.CLOSED
        await self._shutdown()

    async def revoke(self) -> None:
        """Irrevocably close the browser capability session."""
        if self._state is BrowserSessionState.REVOKED:
            return
        self._state = BrowserSessionState.REVOKED
        await self._shutdown()

    async def handoff(self, request: HumanHandoff) -> Mapping[str, JsonValue]:
        """Pause automation during human takeover and return only a redacted record."""
        return await self._run_takeover(request, task=None)

    async def handoff_for_task(
        self,
        task: TaskSpec,
        request: HumanHandoff,
    ) -> Mapping[str, JsonValue]:
        """Pause automation with task context and return only a redacted record."""
        return await self._run_takeover(request, task=task)

    async def _run_takeover(
        self,
        request: HumanHandoff,
        *,
        task: TaskSpec | None,
    ) -> Mapping[str, JsonValue]:
        self._require_active()
        if self._takeover_handler is None and self._handoff_handler is None:
            raise BrowserSessionError("no human handoff handler is configured")
        if self._takeover_handler is not None and self._config.headless:
            raise BrowserSessionError(
                "human takeover requires a visible browser window (headless=False)"
            )
        await self.pause()
        try:
            if self._takeover_handler is not None:
                context = TakeoverPromptContext(
                    origin=request.origin,
                    reason=request.reason,
                    instructions=request.instructions,
                    task_id=task.task_id if task is not None else None,
                    task_objective=task.objective if task is not None else None,
                )
                decision = await self._takeover_handler(context)
            else:
                assert self._handoff_handler is not None
                await self._handoff_handler(request)
                decision = TakeoverDecision(
                    outcome=TakeoverOutcome.RETURNED,
                    authentication_completed=True,
                )

            cleared = await self._clear_transient_state()
            if decision.outcome is TakeoverOutcome.DECLINED:
                await self.revoke()
                return {
                    "authentication_completed": False,
                    "control_returned": False,
                    "origin": request.origin,
                    "transient_state_cleared": cleared,
                }

            if self._state is BrowserSessionState.PAUSED:
                await self.resume()
            self._require_active()
            page = await self._active_page()
            await self._enforce_page_origin(page)
            active_origin = origin_for_url(await page.url())
            return {
                "authentication_completed": decision.authentication_completed,
                "control_returned": True,
                "origin": active_origin,
                "transient_state_cleared": cleared,
            }
        except Exception:
            await self.revoke()
            raise

    async def _clear_transient_state(self) -> bool:
        clipboard_cleared = await self._clipboard_clearer()
        pages = await self._browser.context.pages()
        for page in pages:
            evaluate = getattr(page, "evaluate", None)
            if callable(evaluate):
                await cast(Callable[[str], Awaitable[object]], evaluate)(
                    _CLEAR_TRANSIENT_FORMS_SCRIPT
                )
        return clipboard_cleared

    async def observe_state(self) -> Mapping[str, JsonValue]:
        """Return a safe, bounded snapshot of current tabs and active page state."""
        if self._state is not BrowserSessionState.ACTIVE:
            return {
                "session_state": self._state.value,
                "tabs": [],
                "active_page": None,
            }
        tabs = await self.list_tabs()
        active_tab = next((tab for tab in tabs if tab.active), None)
        active_page: JsonValue = None
        if active_tab is not None and active_tab.url != "about:blank":
            active_page = self._inspection_record(await self.inspect())
        return {
            "session_state": self._state.value,
            "tabs": [self._tab_record(tab) for tab in tabs],
            "active_page": active_page,
        }

    async def execute(self, proposal: ActionProposal) -> Mapping[str, JsonValue]:
        """Bridge Phase 0 browser proposals into the typed Phase 1 operations."""
        if proposal.kind is not ActionKind.BROWSER:
            raise BrowserContractError("browser runtime only accepts browser proposals")
        operation = self._required_string(proposal.parameters, "operation")

        if operation == "open":
            return self._inspection_record(
                await self.open_url(self._required_string(proposal.parameters, "url"))
            )
        if operation == "inspect":
            return self._inspection_record(await self.inspect())
        if operation == "screenshot":
            artifact = await self.screenshot(
                full_page=self._optional_boolean(proposal.parameters, "full_page", False)
            )
            return self._artifact_record(artifact)
        if operation == "observe":
            candidates = await self.observe(
                self._required_string(proposal.parameters, "instruction")
            )
            return {
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "description": candidate.description,
                        "kind": candidate.kind.value,
                    }
                    for candidate in candidates
                ]
            }
        if operation == "click":
            return self._inspection_record(
                await self.click(self._required_string(proposal.parameters, "candidate_id"))
            )
        if operation == "fill":
            return self._inspection_record(
                await self.fill(
                    self._required_string(proposal.parameters, "candidate_id"),
                    self._required_string(proposal.parameters, "value"),
                )
            )
        if operation == "extract":
            schema = self._schema_from_json(proposal.parameters.get("schema"))
            result = await self.extract(
                self._required_string(proposal.parameters, "instruction"), schema
            )
            return {"data": dict(result.data)}
        if operation == "tabs":
            return {"tabs": [self._tab_record(tab) for tab in await self.list_tabs()]}
        if operation == "new_tab":
            return self._tab_record(
                await self.new_tab(self._required_string(proposal.parameters, "url"))
            )
        if operation == "switch_tab":
            return self._tab_record(
                await self.switch_tab(self._required_string(proposal.parameters, "tab_id"))
            )
        if operation == "close_tab":
            await self.close_tab(self._required_string(proposal.parameters, "tab_id"))
            return {"closed": True}
        if operation == "download":
            artifact = await self.download(
                self._required_string(proposal.parameters, "candidate_id")
            )
            return self._artifact_record(artifact)
        if operation == "pause":
            await self.pause()
            return {"state": self.state.value}
        if operation == "resume":
            await self.resume()
            return {"state": self.state.value}
        if operation == "close":
            await self.close()
            return {"state": self.state.value}
        if operation == "revoke":
            await self.revoke()
            return {"state": self.state.value}
        raise BrowserContractError(f"unsupported browser operation: {operation}")

    async def _active_page(self) -> _PageLike:
        page = await self._browser.context.active_page()
        if page is None:
            raise BrowserSessionError("browser session has no active tab")
        self._token_for_page(page)
        return page

    async def _candidate(
        self, candidate_id: str, expected_kind: InteractionKind
    ) -> tuple[_PageLike, _CandidateTarget]:
        self._require_active()
        target = self._candidates.get(candidate_id)
        if target is None:
            raise UnknownCandidateError("unknown or stale interaction candidate")
        if target.kind is not expected_kind:
            raise UnknownCandidateError(
                f"candidate is {target.kind.value}, not {expected_kind.value}"
            )
        page = await self._active_page()
        if self._token_for_page(page) != target.tab_id:
            raise UnknownCandidateError("candidate belongs to a different active tab")
        await self._enforce_page_origin(page)
        return page, target

    async def _sync_tabs(self) -> list[_PageLike]:
        pages = await self._browser.context.pages()
        current_page_ids = {page.page_id for page in pages}
        for page_id in set(self._tab_tokens) - current_page_ids:
            token = self._tab_tokens.pop(page_id)
            self._pages.pop(token, None)
        for page in pages:
            self._token_for_page(page)
        return pages

    def _token_for_page(self, page: _PageLike) -> str:
        token = self._tab_tokens.get(page.page_id)
        if token is None:
            token = str(uuid4())
            self._tab_tokens[page.page_id] = token
        self._pages[token] = page
        return token

    def _forget_page(self, page: _PageLike) -> None:
        token = self._tab_tokens.pop(page.page_id, None)
        if token is not None:
            self._pages.pop(token, None)

    def _require_active(self) -> None:
        if self._state is not BrowserSessionState.ACTIVE:
            raise BrowserSessionInactiveError(f"browser session is {self._state}")

    def _require_approved_url(self, url: str) -> None:
        try:
            origin = origin_for_url(url)
        except BrowserContractError as error:
            raise NavigationPolicyError(str(error)) from error
        if origin not in self._config.allowed_origins:
            raise NavigationPolicyError(f"origin is not approved: {origin}")

    async def _enforce_page_origin(self, page: _PageLike) -> None:
        url = await page.url()
        try:
            origin = origin_for_url(url)
        except BrowserContractError as error:
            await self.revoke()
            raise NavigationPolicyError(str(error)) from error
        if origin not in self._config.allowed_origins:
            await self.revoke()
            raise NavigationPolicyError(f"browser reached an unapproved origin: {origin}")

    def _invalidate_candidates(self) -> None:
        self._candidates.clear()

    async def _shutdown(self) -> None:
        self._invalidate_candidates()
        try:
            await self._stagehand.close()
        finally:
            if not self._browser.closed:
                await self._browser.close()

    def _artifact(self, path: Path) -> ArtifactRef:
        resolved = path.resolve()
        if not resolved.is_relative_to(self._config.workspace):
            raise DownloadError("artifact escaped the task workspace")
        size = resolved.stat().st_size
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return ArtifactRef(str(resolved.relative_to(self._config.workspace)), size, digest)

    @staticmethod
    def _interaction_kind(method: str | None) -> InteractionKind | None:
        normalized = (method or "").lower()
        if normalized == "click":
            return InteractionKind.CLICK
        if normalized in {"fill", "type"}:
            return InteractionKind.FILL
        return None

    @staticmethod
    def _extraction_model(schema: ExtractionSchema) -> type[BaseModel]:
        type_map: dict[ExtractionType, type[str] | type[int] | type[float] | type[bool]] = {
            ExtractionType.STRING: str,
            ExtractionType.INTEGER: int,
            ExtractionType.NUMBER: float,
            ExtractionType.BOOLEAN: bool,
        }
        definitions: dict[str, tuple[object, object]] = {}
        for field in schema.fields:
            python_type = type_map[field.type]
            definitions[field.name] = (
                (python_type, ...) if field.required else (python_type | None, ...)
            )
        # Pydantic intentionally accepts runtime field definitions that cannot
        # be represented precisely by a static type checker.
        return cast(
            type[BaseModel],
            create_model(
                "UBCBrowserExtraction",
                __config__=ConfigDict(extra="forbid"),
                **definitions,  # pyright: ignore[reportCallIssue, reportArgumentType]
            ),
        )

    @staticmethod
    def _required_string(parameters: Mapping[str, JsonValue], key: str) -> str:
        value = parameters.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BrowserContractError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _optional_boolean(parameters: Mapping[str, JsonValue], key: str, default: bool) -> bool:
        value = parameters.get(key, default)
        if not isinstance(value, bool):
            raise BrowserContractError(f"{key} must be a boolean")
        return value

    @staticmethod
    def _schema_from_json(value: JsonValue | None) -> ExtractionSchema:
        if not isinstance(value, list):
            raise BrowserContractError("schema must be a list of field objects")
        fields: list[ExtractionField] = []
        for item in value:
            if not isinstance(item, dict):
                raise BrowserContractError("each extraction field must be an object")
            name = item.get("name")
            field_type = item.get("type")
            required = item.get("required", True)
            if not isinstance(name, str) or not isinstance(field_type, str):
                raise BrowserContractError("extraction fields require string name and type")
            if not isinstance(required, bool):
                raise BrowserContractError("extraction field required must be boolean")
            try:
                parsed_type = ExtractionType(field_type)
            except ValueError as error:
                raise BrowserContractError(f"unsupported extraction type: {field_type}") from error
            fields.append(ExtractionField(name, parsed_type, required))
        return ExtractionSchema(tuple(fields))

    @staticmethod
    def _inspection_record(inspection: PageInspection) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], asdict(inspection))

    @staticmethod
    def _artifact_record(artifact: ArtifactRef) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], asdict(artifact))

    @staticmethod
    def _tab_record(tab: TabInfo) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], asdict(tab))
