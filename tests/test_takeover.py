"""Unit tests for the Phase 3 human takeover CLI handler and clipboard clearing."""

import subprocess
from types import SimpleNamespace

import pytest

from ubc_node.browser.takeover import (
    CliTakeoverHandler,
    TakeoverDecision,
    TakeoverOutcome,
    TakeoverPromptContext,
    clear_system_clipboard,
)

CONTEXT = TakeoverPromptContext(
    origin="http://127.0.0.1:8080",
    reason="Fake verification checkpoint",
    instructions="Enter code 123456 in Chrome, then return control.",
    task_id="task-verify",
    task_objective="Complete verification and confirm status",
)


def test_takeover_decision_rejects_declined_with_authentication_completed() -> None:
    returned = TakeoverDecision(TakeoverOutcome.RETURNED, authentication_completed=True)
    assert returned.outcome is TakeoverOutcome.RETURNED
    assert returned.authentication_completed is True

    declined = TakeoverDecision(TakeoverOutcome.DECLINED, authentication_completed=False)
    assert declined.outcome is TakeoverOutcome.DECLINED

    with pytest.raises(ValueError, match="authentication_completed"):
        TakeoverDecision(TakeoverOutcome.DECLINED, authentication_completed=True)


@pytest.mark.asyncio
async def test_cli_takeover_handler_requires_explicit_return_or_decline() -> None:
    outputs: list[str] = []
    answers = iter(["", "maybe", "RETURN"])
    handler = CliTakeoverHandler(
        input_fn=lambda _prompt: next(answers),
        output_fn=outputs.append,
    )

    decision = await handler(CONTEXT)
    assert decision == TakeoverDecision(TakeoverOutcome.RETURNED, authentication_completed=True)
    assert "task-verify — Complete verification and confirm status" in outputs[0]
    assert "http://127.0.0.1:8080" in outputs[0]
    assert "Fake verification checkpoint" in outputs[0]
    assert "PAUSED" in outputs[0]
    assert "Switch back to this terminal" in outputs[0]
    assert "Type 'return' to resume the agent" in outputs[0]
    assert outputs.count("Please explicitly enter 'return' or 'decline'.") == 2

    outputs2: list[str] = []
    decline_handler = CliTakeoverHandler(
        input_fn=lambda _prompt: "d",
        output_fn=outputs2.append,
    )
    minimal_context = TakeoverPromptContext(
        origin="http://127.0.0.1:8080",
        reason="Login",
        instructions="Sign in",
    )
    declined = await decline_handler(minimal_context)
    assert declined == TakeoverDecision(TakeoverOutcome.DECLINED, authentication_completed=False)
    assert "unspecified" in outputs2[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_name", "available_bins", "expected_cmd"),
    [
        ("darwin", {"pbcopy"}, ["pbcopy"]),
        ("win32", {"clip"}, ["clip"]),
        ("linux", {"wl-copy"}, ["wl-copy", "--clear"]),
        ("linux", {"xclip"}, ["xclip", "-selection", "clipboard"]),
    ],
)
async def test_clear_system_clipboard_selects_platform_command(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    available_bins: set[str],
    expected_cmd: list[str],
) -> None:
    recorded: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        recorded.append(command)
        return SimpleNamespace(returncode=0)

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available_bins else None

    monkeypatch.setattr("ubc_node.browser.takeover.sys.platform", platform_name)
    monkeypatch.setattr("ubc_node.browser.takeover.shutil.which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert await clear_system_clipboard() is True
    assert recorded == [expected_cmd]


@pytest.mark.asyncio
async def test_clear_system_clipboard_handles_missing_binary_and_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_which(_name: str) -> str | None:
        return None

    def pbcopy_which(_name: str) -> str | None:
        return "/usr/bin/pbcopy"

    monkeypatch.setattr("ubc_node.browser.takeover.sys.platform", "linux")
    monkeypatch.setattr("ubc_node.browser.takeover.shutil.which", missing_which)
    assert await clear_system_clipboard() is False

    monkeypatch.setattr("ubc_node.browser.takeover.sys.platform", "freebsd")
    assert await clear_system_clipboard() is False

    def failing_run(*_args: object, **_kwargs: object) -> object:
        raise OSError("clipboard unavailable")

    monkeypatch.setattr("ubc_node.browser.takeover.sys.platform", "darwin")
    monkeypatch.setattr("ubc_node.browser.takeover.shutil.which", pbcopy_which)
    monkeypatch.setattr(subprocess, "run", failing_run)
    assert await clear_system_clipboard() is False
