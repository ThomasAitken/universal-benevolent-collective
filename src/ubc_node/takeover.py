"""Phase 3 human takeover protocol: CLI control handoff and transient state clearing."""

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum


class TakeoverOutcome(StrEnum):
    """Explicit human decision at the end of a browser takeover."""

    RETURNED = "returned"
    DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class TakeoverDecision:
    """Structured result of a human takeover without free-form secret fields."""

    outcome: TakeoverOutcome
    authentication_completed: bool = False

    def __post_init__(self) -> None:
        if self.outcome is TakeoverOutcome.DECLINED and self.authentication_completed:
            raise ValueError("declined takeover cannot mark authentication_completed=True")


@dataclass(frozen=True, slots=True)
class TakeoverPromptContext:
    """Information shown to the human operator when takeover begins."""

    origin: str
    reason: str
    instructions: str
    task_id: str | None = None
    task_objective: str | None = None


type TakeoverHandler = Callable[[TakeoverPromptContext], Awaitable[TakeoverDecision]]
type ClipboardClearer = Callable[[], Awaitable[bool]]


async def clear_system_clipboard() -> bool:
    """Clear the local OS clipboard without reading its previous contents."""
    command = _clipboard_clear_command()
    if command is None:
        return False
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            input=b"",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _clipboard_clear_command() -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        return ["pbcopy"]
    if sys.platform == "win32" and shutil.which("clip"):
        return ["clip"]
    if sys.platform.startswith("linux"):
        if shutil.which("wl-copy"):
            return ["wl-copy", "--clear"]
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
    return None


class CliTakeoverHandler:
    """Simple CLI human takeover prompt requiring explicit return or decline."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._input = input_fn
        self._output = output_fn

    async def __call__(self, context: TakeoverPromptContext) -> TakeoverDecision:
        """Display takeover details and wait for explicit human return or decline."""
        task_line = (
            f"{context.task_id} — {context.task_objective}"
            if context.task_id and context.task_objective
            else (context.task_id or context.task_objective or "unspecified")
        )
        banner = "\n".join(
            [
                "",
                "=" * 64,
                "HUMAN TAKEOVER REQUIRED",
                "Model loop, screenshots, DOM capture, and telemetry are PAUSED.",
                f"Task:         {task_line}",
                f"Site:         {context.origin}",
                f"Reason:       {context.reason}",
                f"Instructions: {context.instructions}",
                "=" * 64,
                "Use the visible browser window to complete the checkpoint.",
                "Type 'return' to return control, or 'decline' to stop the run.",
            ]
        )
        self._output(banner)

        while True:
            raw = await asyncio.to_thread(self._input, "Takeover decision [return/decline]: ")
            choice = raw.strip().lower()
            if choice in {"return", "r"}:
                return TakeoverDecision(
                    outcome=TakeoverOutcome.RETURNED,
                    authentication_completed=True,
                )
            if choice in {"decline", "d"}:
                return TakeoverDecision(
                    outcome=TakeoverOutcome.DECLINED,
                    authentication_completed=False,
                )
            self._output("Please explicitly enter 'return' or 'decline'.")
