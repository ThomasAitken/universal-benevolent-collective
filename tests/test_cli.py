"""Tests for the initial UBC command-line interface."""

import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from ubc_node import __version__
from ubc_node import cli as cli_module
from ubc_node.cli import main, run
from ubc_node.demo import DemoCancelled, DemoError, Phase5DemoResult


def test_default_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert run([]) == 0

    output = capsys.readouterr().out
    assert "Operate a Universal Benevolent Collective node" in output
    assert "doctor" in output


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["version"]) == 0
    assert capsys.readouterr().out == f"ubc-node {__version__}\n"


def test_doctor_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["doctor"]) == 0

    output = capsys.readouterr().out
    assert f"ubc-node {__version__}" in output
    assert "Python 3.13" in output
    assert "Project scaffold: ready" in output


def test_console_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ubc", "version"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0


def test_demo_command_runs_and_reports_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, object] = {}

    async def fake_demo(**kwargs: object) -> Phase5DemoResult:
        seen.update(kwargs)
        return Phase5DemoResult(
            tmp_path / "report.md",
            tmp_path / "findings.json",
            tmp_path / "events.jsonl",
            5,
        )

    monkeypatch.setattr(cli_module, "run_phase5_demo", fake_demo)
    assert (
        run(
            [
                "demo",
                "--workspace",
                str(tmp_path),
                "--provider",
                "openai",
                "--model",
                "gpt-test",
                "--base-url",
                "http://localhost:1234/v1",
                "--chrome-path",
                str(tmp_path / "chrome"),
                "--browser-runtime",
                "supervised",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Phase 5 demonstration complete" in output
    assert "Captured evidence: 5" in output
    assert seen["provider_name"] == "openai"
    assert seen["model"] == "gpt-test"
    assert seen["browser_runtime"] == "supervised"


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (DemoCancelled("cancelled"), 130, "cancelled"),
        (DemoError("broken"), 1, "demo failed: broken"),
        (ValueError("invalid"), 1, "demo failed: invalid"),
        (OSError("unavailable"), 1, "demo failed: unavailable"),
    ],
)
def test_demo_command_maps_expected_failures_to_exit_codes(
    error: Exception,
    status: int,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failing_demo(**_kwargs: object) -> Phase5DemoResult:
        raise error

    monkeypatch.setattr(cli_module, "run_phase5_demo", failing_demo)
    assert run(["demo"]) == status
    assert message in capsys.readouterr().err


def test_demo_command_handles_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted(_awaitable: Coroutine[Any, Any, object]) -> object:
        _awaitable.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.asyncio, "run", interrupted)
    assert run(["demo"]) == 130
    assert "cancelled by operator" in capsys.readouterr().err
