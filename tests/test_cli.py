"""Tests for the initial UBC command-line interface."""

import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from ubc_node import __version__
from ubc_node import cli as cli_module
from ubc_node.cli import main, run
from ubc_node.demos.demo import DemoCancelled, DemoError, Phase5DemoResult


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


def test_release_manifest_command_writes_verified_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).parents[1]
    output = tmp_path / "manifest.json"
    assert (
        run(
            [
                "release-manifest",
                "--root",
                str(root),
                "--release-id",
                "test-release",
                "--constitution-version",
                "test-draft",
                "--browser-image-reference",
                "localhost/ubc-browser",
                "--browser-image-digest",
                f"sha256:{'a' * 64}",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert '"schema_version": 1' in output.read_text()
    assert str(output) in capsys.readouterr().out


def test_release_manifest_command_reports_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run(
            [
                "release-manifest",
                "--root",
                str(tmp_path),
                "--release-id",
                "test-release",
                "--constitution-version",
                "test-draft",
                "--browser-image-reference",
                "localhost/ubc-browser",
                "--browser-image-digest",
                f"sha256:{'a' * 64}",
                "--output",
                str(tmp_path / "manifest.json"),
            ]
        )
        == 1
    )
    assert "release manifest failed" in capsys.readouterr().err


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


@pytest.mark.parametrize("completed", [True, False])
def test_research_command_uses_private_audit_and_verified_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed: bool
) -> None:
    from unittest.mock import AsyncMock

    from ubc_node.core.contracts import BudgetUsage, RunResult, RunState

    journal = tmp_path / "run.jsonl"
    state = RunState.COMPLETED if completed else RunState.FAILED
    runner = AsyncMock(return_value=RunResult("run", "task", state, BudgetUsage(), 1, error="test"))
    monkeypatch.setattr(cli_module, "run_browser_research", runner)
    monkeypatch.setenv("OPENAI_API_KEY", "test-provider-key")
    argv = [
        "research",
        "--journal",
        str(journal),
        "--task",
        "task",
        "--objective",
        "review price notes",
        "--source",
        "notes",
        "https://market.test/notes",
    ]
    assert run(argv) == (0 if completed else 1)
    assert journal.stat().st_mode & 0o777 == 0o600
    assert runner.call_args.args[1] == {"notes": "https://market.test/notes"}
    assert run(argv) == 1
    assert run([*argv, "--source", "notes", "https://market.test/source"]) == 1
    bad_journal = tmp_path / "invalid-url.jsonl"
    assert (
        run(
            [
                "research",
                "--journal",
                str(bad_journal),
                "--task",
                "task",
                "--objective",
                "review price notes",
                "--source",
                "notes",
                "https://market.test",
            ]
        )
        == 1
    )
    assert not bad_journal.exists()
    runner.assert_awaited_once()


@pytest.mark.parametrize(("error", "code"), [(KeyboardInterrupt(), 130), (TimeoutError(), 1)])
def test_research_command_maps_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException, code: int
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr(cli_module, "_run_research", AsyncMock(side_effect=error))
    assert (
        run(
            [
                "research",
                "--journal",
                str(tmp_path / "interrupted.jsonl"),
                "--task",
                "task",
                "--objective",
                "test cancellation",
                "--source",
                "source",
                "https://market.test/",
            ]
        )
        == code
    )
