"""Tests for the initial UBC command-line interface."""

import sys

import pytest

from ubc_node import __version__
from ubc_node.cli import main, run


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
