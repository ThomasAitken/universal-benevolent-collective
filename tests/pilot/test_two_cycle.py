"""Exercise the C07 operator entry point and resume from real process exits."""

import asyncio
import fcntl
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from tests.pilot.run_fixture import (
    BASE_ANALYSIS,
    BASE_REVISION,
    CANARY,
    FORECAST,
    PROGRAMME,
    PROJECT,
    FixtureRun,
    FixtureScanner,
    main,
)
from ubc_node.browser.browser_contracts import BrowserSession, BrowserSessionState
from ubc_node.browser.isolation.isolated_browser import PodmanBrowserRuntime
from ubc_node.pilot.analysis_runner import AnalysisRunner
from ubc_node.pilot.forecasts import resolve_forecast

CRASH = """
import os
import sys
from tests.pilot import run_fixture as pilot

stage = sys.argv.pop(1)
owner, method = {
    "release": (pilot.DownloadQuarantine, "ingest"),
    "analysis": (pilot.ResearchCatalogue, "analyse"),
    "proposal": (pilot.ThesisReview, "submit"),
    "score": (pilot, "resolve_forecast"),
}[stage]
original = getattr(owner, method)

def crash_after_commit(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(78)

setattr(owner, method, crash_after_commit)
raise SystemExit(pilot.main())
"""


@pytest.fixture
def engine(tmp_path: Path) -> AnalysisRunner:
    path = tmp_path / "fixture-engine"
    path.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1] == 'run':\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[sys.argv.index('-ISB'):]])\n"
    )
    path.chmod(0o700)
    return AnalysisRunner(str(path))


def _review(cycle: str, decision: str = "approve") -> list[str]:
    return [
        "review",
        "--cycle",
        cycle,
        "--reviewer",
        "independent-fixture-reviewer",
        "--decision",
        decision,
        "--reason",
        "Checked the exact source rows.",
        "--independence",
        "Fixture reviewer with no role in creating the proposal.",
    ]


@pytest.mark.parametrize("stage", ["release", "analysis", "proposal", "score"])
def test_two_cycle_restarts(
    tmp_path: Path,
    engine: AnalysisRunner,
    stage: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "pilot"
    args = ["--workspace", str(workspace), "--engine", engine.engine]
    scanner = FixtureScanner(Path("/unused"))
    first = FixtureRun(workspace, 1, engine, scanner)
    second = FixtureRun(workspace, 2, engine, scanner)
    assert main([*args, "run", "--cycle", "2"]) == 1
    assert "approved baseline proposal" in capsys.readouterr().err
    assert not (workspace / "incoming-v2.csv").exists()

    def crash(cycle: str) -> None:
        crashed = subprocess.run(
            [sys.executable, "-c", CRASH, stage, *args, "run", "--cycle", cycle],
            check=False,
            capture_output=True,
            timeout=20,
        )
        assert crashed.returncode == 78, crashed.stderr.decode()

    if stage != "score":
        crash("1")
    assert main([*args, "run"]) == 0
    assert first.lookup("source:fixture-nsw-prices:v2") is None
    assert first.lookup(BASE_REVISION) is None
    assert first.lookup(FORECAST) is not None
    assert main([*args, *_review("1")]) == 0
    original_revision = first.catalogue.get(BASE_REVISION)
    original_analysis = first.catalogue.get(BASE_ANALYSIS)
    original_forecast = first.catalogue.get(FORECAST)

    if stage == "score":
        crash("2")
    assert main([*args, "run", "--cycle", "2"]) == 0
    updated = second.catalogue.get("fixture:cycle-2")
    dependants = updated.data["earlier_dependants"]
    assert isinstance(dependants, list)
    assert {BASE_ANALYSIS, FORECAST, BASE_REVISION, "proposal:task-c07-v1"} <= {
        str(record) for record in dependants
    }
    assert second.lookup("revision_update000") is None
    queue = second.catalogue.pending_followups(PROGRAMME)
    assert [item.id for item in queue] == ["followup:task-c07-v2"]
    assert queue[0].project == PROJECT
    assert main([*args, *_review("2")]) == 0
    assert main([*args, "verify"]) == 0
    verification = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert verification["source_csv_line"] == "2026-01-01T00:00:00Z,NSW1,-12.3"
    assert verification["update"]["source_csv_line"] == "2026-01-01T00:00:00Z,NSW1,-12.3"
    assert verification["score"]["forecast_brier"] == "0.06250000"
    assert verification["score"]["baseline_brier"] == "0.25000000"

    journal = second.catalogue.journal()
    assert main([*args, "run"]) == main([*args, "run", "--cycle", "2"]) == 0
    assert main([*args, *_review("1")]) == main([*args, *_review("2")]) == 0
    assert second.catalogue.journal() == journal
    assert second.catalogue.get(BASE_REVISION) == original_revision
    assert second.catalogue.get(BASE_ANALYSIS) == original_analysis
    assert second.catalogue.get(FORECAST) == original_forecast
    assert second.catalogue.get("revision_update000").data["statement"] == (
        "2 of 2 NSW1 fixture intervals had a negative price."
    )
    with closing(sqlite3.connect(workspace / "quarantine" / "downloads.sqlite3")) as db:
        assert db.execute(
            "SELECT session, cleaned FROM acquisitions ORDER BY session"
        ).fetchall() == [
            ("v1", 1),
            ("v2", 1),
        ]
    assert CANARY not in b"".join(path.read_bytes() for path in workspace.glob("*.jsonl"))
    assert CANARY.decode() not in str(journal)
    assert not list(workspace.glob("incoming-*.csv"))


def test_nonapproval_keeps_outcome_hidden(tmp_path: Path, engine: AnalysisRunner) -> None:
    args = ["--workspace", str(tmp_path / "pilot"), "--engine", engine.engine]
    assert main([*args, "run"]) == 0
    assert main([*args, *_review("1", "request_changes")]) == 0
    assert main([*args, "run", "--cycle", "2"]) == 1
    second = FixtureRun(tmp_path / "pilot", 2, engine, FixtureScanner(Path("/unused")))
    assert second.lookup(second.task_id) is None
    assert second.lookup("source:fixture-nsw-prices:v2") is None


def test_revoked_outcome_cannot_make_a_new_score(
    tmp_path: Path,
    engine: AnalysisRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = ["--workspace", str(tmp_path / "pilot"), "--engine", engine.engine]
    assert main([*args, "run"]) == 0
    assert main([*args, *_review("1")]) == 0
    second = FixtureRun(tmp_path / "pilot", 2, engine, FixtureScanner(Path("/unused")))

    def revoke_before_resolving(*_: object) -> None:
        second.catalogue.revoke(second.task_id, "Owner withdrew grant.")
        resolve_forecast(second.catalogue, FORECAST, "analysis:task-c07-v2")

    monkeypatch.setattr("tests.pilot.run_fixture.resolve_forecast", revoke_before_resolving)
    assert main([*args, "run", "--cycle", "2"]) == 1
    assert second.lookup("fixture:cycle-2") is None
    assert (
        second.lookup("score:forecast:question:c07-next-release:source:fixture-nsw-prices:v2")
        is None
    )
    assert main([*args, "run", "--cycle", "2"]) == 1


async def test_revoke_retires_the_active_browser(tmp_path: Path, engine: AnalysisRunner) -> None:
    workspace = tmp_path / "pilot"
    workspace.mkdir(mode=0o700)
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def stalled_page(*_: object) -> None:
        loop.call_soon_threadsafe(started.set)
        await asyncio.Future()

    session = Mock(
        spec=BrowserSession,
        state=BrowserSessionState.ACTIVE,
        open_url=stalled_page,
    )
    runtime = Mock(
        spec=PodmanBrowserRuntime,
        image_digest="sha256:" + "0" * 64,
        launch=AsyncMock(return_value=session),
    )
    fixture = FixtureRun(workspace, 1, engine, FixtureScanner(Path("/unused")), runtime)
    fixture.seed()
    grant = fixture.catalogue.opt_in(fixture.choice, PROGRAMME, "fixture-owner", (PROJECT,))
    task = fixture.catalogue.choose(grant.id, PROJECT)

    active = asyncio.create_task(asyncio.to_thread(fixture.capture, task))
    try:
        await asyncio.wait_for(started.wait(), 5)
        fixture.catalogue.revoke(fixture.task_id, "Owner withdrew grant.")
        with pytest.raises(ValueError, match="not active"):
            await asyncio.wait_for(active, 5)
    finally:
        if not active.done():
            active.cancel()
    session.revoke.assert_awaited_once()
    assert fixture.lookup(f"evidence:{task.task_id}") is None
    assert fixture.catalogue.get(f"revoke:{task.task_id}").data["reason"] == "Owner withdrew grant."


def test_cli_inspect_journal_revoke_and_error_guards(
    tmp_path: Path,
    engine: AnalysisRunner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does-not-exist"
    assert main(["--workspace", str(missing), "inspect"]) == 1
    assert "workspace does not exist" in capsys.readouterr().err
    assert not missing.exists()

    workspace = tmp_path / "pilot"
    args = ["--workspace", str(workspace), "--engine", engine.engine]
    assert main([*args, "run"]) == 0
    assert main([*args, *_review("1")]) == 0

    # Configuration mismatch on cycle 2 produces an actionable diagnostic.
    assert (
        main([*args, "--scanner-socket", str(tmp_path / "clamd.ctl"), "run", "--cycle", "2"]) == 1
    )
    assert "workspace configuration mismatch" in capsys.readouterr().err

    assert main([*args, "run", "--cycle", "2"]) == 0
    capsys.readouterr()

    # Exercise inspect (--record, --artifact, --receipt) and journal.
    assert main([*args, "inspect", "--record", "source:fixture-nsw-prices:v2"]) == 0
    source_record = json.loads(capsys.readouterr().out)
    receipt = source_record["data"]["receipt"]

    assert main([*args, "inspect", "--record", "source:fixture-nsw-prices:v2", "--artifact"]) == 0
    assert "-5.1" in capsys.readouterr().out

    assert main([*args, "inspect", "--receipt", receipt]) == 0
    acquisition = json.loads(capsys.readouterr().out)
    assert acquisition["receipt"] == receipt
    assert acquisition["released"] is True

    assert main([*args, "journal"]) == 0
    events = json.loads(capsys.readouterr().out)
    assert isinstance(events, list) and events

    # Execution lock contention fails cleanly.
    with (workspace / "execution.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main([*args, "verify"]) == 1
        assert "C07 fixture:" in capsys.readouterr().err

    # Tampering with cycle 2's browser journal is caught by verify.
    evidence_v2 = FixtureRun(workspace, 2, engine, FixtureScanner(Path("/unused"))).catalogue.get(
        "evidence:task-c07-v2"
    )
    journal_path = workspace / str(evidence_v2.data["journal"])
    journal_path.write_text("tampered\n", encoding="utf-8")
    assert main([*args, "verify"]) == 1
    assert "cycle 2 browser journal digest mismatch" in capsys.readouterr().err

    # CLI revoke records the revocation cleanly.
    assert main([*args, "revoke", "--cycle", "2", "--reason", "Operator halted fixture."]) == 0
    revocation = json.loads(capsys.readouterr().out)
    assert revocation["id"] == "revoke:task-c07-v2"
    assert revocation["data"]["reason"] == "Operator halted fixture."
