"""In-memory Stagehand-shaped doubles for browser adapter tests."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

type AsyncCallback = Callable[[], Awaitable[None]]


class FakeLocator:
    def __init__(
        self,
        *,
        on_click: AsyncCallback | None = None,
        on_fill: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._on_click = on_click
        self._on_fill = on_fill
        self.clicks = 0
        self.fills: list[str] = []

    async def click(self) -> None:
        self.clicks += 1
        if self._on_click is not None:
            await self._on_click()

    async def fill(self, value: str) -> None:
        self.fills.append(value)
        if self._on_fill is not None:
            await self._on_fill(value)


class FakePage:
    def __init__(
        self,
        page_id: str,
        url: str = "about:blank",
        *,
        title: str = "Fixture",
        content: object = "page content",
    ) -> None:
        self.page_id = page_id
        self.current_url = url
        self.current_title = title
        self.content = content
        self.closed = False
        self.goto_target: str | None = None
        self.screenshot_bytes = b"PNG"
        self.write_screenshot = True
        self.snapshot_as_model = False
        self.locators: dict[str, FakeLocator] = {}

    async def goto(self, url: str, *, wait_until: str | None = None) -> object:
        assert wait_until == "domcontentloaded"
        self.current_url = self.goto_target or url
        return object()

    async def url(self) -> str:
        return self.current_url

    async def title(self) -> str:
        return self.current_title

    async def snapshot(self, *, include_iframes: bool | None = None) -> object:
        del include_iframes
        if self.snapshot_as_model:
            return FakeSnapshotResult(self.content)
        return {"formatted_tree": self.content}

    async def screenshot(
        self,
        *,
        full_page: bool | None = None,
        path: str | Path | None = None,
        type: str | None = None,
    ) -> bytes:
        del full_page
        assert type == "png"
        if path is not None and self.write_screenshot:
            Path(path).write_bytes(self.screenshot_bytes)
        return self.screenshot_bytes

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.setdefault(selector, FakeLocator())

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, pages: Sequence[FakePage]) -> None:
        self.all_pages = list(pages)
        self.active = self.all_pages[0] if self.all_pages else None
        self.new_page_url_override: str | None = None

    async def pages(self) -> list[FakePage]:
        return [page for page in self.all_pages if not page.closed]

    async def active_page(self) -> FakePage | None:
        if self.active is not None and self.active.closed:
            return None
        return self.active

    async def new_page(self, url: str | None = None) -> FakePage:
        page = FakePage(
            f"page-{len(self.all_pages) + 1}", self.new_page_url_override or url or "about:blank"
        )
        self.all_pages.append(page)
        return page

    async def set_active_page(self, page: FakePage) -> None:
        self.active = page


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1


@dataclass(frozen=True)
class FakeObservedAction:
    selector: str
    description: str
    method: str | None


@dataclass(frozen=True)
class FakeSnapshotResult:
    formatted_tree: object


@dataclass(frozen=True)
class FakeObserveResult:
    data: Sequence[FakeObservedAction]


@dataclass(frozen=True)
class FakeExtractResult:
    data: BaseModel


class FakeStagehand:
    def __init__(self) -> None:
        self.observed: Sequence[FakeObservedAction] = ()
        self.extract_values: dict[str, object] = {}
        self.closed = False
        self.observe_calls: list[tuple[str, FakePage]] = []
        self.extract_calls: list[tuple[str, type[BaseModel], FakePage]] = []

    async def observe(self, instruction: str, *, page: FakePage) -> FakeObserveResult:
        self.observe_calls.append((instruction, page))
        return FakeObserveResult(self.observed)

    async def extract(
        self,
        instruction: str,
        schema: type[BaseModel],
        *,
        page: FakePage,
    ) -> FakeExtractResult:
        self.extract_calls.append((instruction, schema, page))
        return FakeExtractResult(schema.model_validate(self.extract_values))

    async def close(self) -> None:
        self.closed = True
