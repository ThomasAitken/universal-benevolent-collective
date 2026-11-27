"""C06 catalogue and review acceptance with deterministic releases and clock."""

import copy
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from jsonschema.exceptions import ValidationError

from tests.clamd_fixture import clamd_fixture as clamd_fixture
from tests.clamd_fixture import scanner_for
from tests.pilot.fixtures import AUTHOR, BASE, HEADER, OUTCOME, SOURCE, START, WINDOW
from tests.pilot.fixtures import draft_thesis as _draft
from ubc_node.artifacts import quarantine
from ubc_node.artifacts.quarantine import DownloadQuarantine, DownloadScope
from ubc_node.pilot.analysis_runner import AnalysisError, AnalysisRunner, PriceWindow
from ubc_node.pilot.catalogue import Record, ResearchCatalogue, digest, object_value
from ubc_node.pilot.forecasts import freeze_question, resolve_forecast, submit_forecast
from ubc_node.pilot.thesis_review import Decision, ThesisReview


@dataclass
class Pilot:
    catalogue: ResearchCatalogue
    quarantine: DownloadQuarantine
    clamd: tuple[Path, socket.socket]
    tmp: Path
    when: datetime = START

    def task(self, choice: str, project: str = "project-prices") -> str:
        self.catalogue.opt_in(choice, "markets", "owner-123", (project,))
        return self.catalogue.choose(choice, project).task_id

    def download(
        self, task: str, content: bytes, *, locator: str = SOURCE, project: str = "project-prices"
    ) -> str:
        filename = self.tmp / "incoming"
        filename.write_bytes(content)
        with scanner_for(self.clamd, b"stream: OK\0", expected=content) as scanner:
            return self.quarantine.ingest(
                DownloadScope(project, task, "fixture", locator),
                filename,
                "prices.csv",
                expected_sha256=digest(content),
                scanner=scanner,
                canaries=(b"secret-fixture",),
            ).receipt

    def source(
        self,
        task: str,
        version: str,
        content: bytes = BASE,
        replaces: str | None = None,
        *,
        project: str = "project-prices",
    ) -> Record:
        receipt = self.download(task, content, project=project)
        return self.catalogue.register_source(
            task,
            self.quarantine,
            receipt,
            version=version,
            replaces=replaces,
            released_at=self.when,
            change=f"Fixture {version}.",
        )


@pytest.fixture
def pilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clamd: tuple[Path, socket.socket]
) -> Pilot:
    pilot = Pilot(
        ResearchCatalogue(tmp_path / "research"),
        DownloadQuarantine(tmp_path / "downloads"),
        clamd,
        tmp_path,
    )

    def clock() -> datetime:
        return pilot.when

    pilot.catalogue.clock = clock

    class QuarantineClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return clock()

    monkeypatch.setattr(quarantine, "datetime", QuarantineClock)
    pilot.catalogue.programme("markets", "Electricity markets")
    for project in ("project-prices", "project-future"):
        pilot.catalogue.admit(
            project,
            "markets",
            owner="owner-123",
            admitted_by="reviewer-123",
            objective="Count negative intervals",
            window=WINDOW,
            source_locator=SOURCE,
            rights="Synthetic fixture. CC0-1.0.",
        )
    return pilot


@pytest.fixture
def runner(tmp_path: Path) -> AnalysisRunner:
    """Exercise the engine transport with a local interpreter in deterministic tests."""
    engine = tmp_path / "fixture-engine"
    engine.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "if sys.argv[1] == 'run':\n"
        "    os.execv(sys.executable, [sys.executable, *sys.argv[sys.argv.index('-ISB'):]])\n"
    )
    engine.chmod(0o700)
    return AnalysisRunner(str(engine))


def test_choices_followup_revocation_and_scope(pilot: Pilot, runner: AnalysisRunner) -> None:
    catalogue = pilot.catalogue
    assert catalogue.programme("markets", "Electricity markets") == catalogue.get("markets")
    with pytest.raises(ValueError, match="conflicting retry"):
        catalogue.programme("markets", "Changed")
    with pytest.raises(ValueError, match="identifier"):
        catalogue.programme("market:other", "Another programme")
    with pytest.raises(ValueError, match="explanatory text"):
        catalogue.programme("empty", " ")
    with pytest.raises(ValueError, match="identifier"):
        catalogue.opt_in("not.a.task", "markets", "owner-123", ("project-prices",))
    with pytest.raises(ValueError, match="distinct"):
        catalogue.opt_in("choice", "markets", "owner-123", ("project-prices", "project-prices"))
    with pytest.raises(ValueError, match="outside"):
        catalogue.opt_in("choice", "markets", "someone-else", ("project-prices",))
    catalogue.programme("other", "Another programme")
    with pytest.raises(ValueError, match="outside"):
        catalogue.opt_in("choice", "other", "owner-123", ("project-prices",))
    catalogue.opt_in("choice", "markets", "owner-123", ("project-prices", "project-future"))
    with pytest.raises(ValueError, match="outside"):
        catalogue.choose("choice", "other")
    task = catalogue.choose("choice", "project-prices")
    assert catalogue.choose("choice", "project-prices") == task
    assert catalogue.get(task.task_id).data["budget"] == {
        "max_duration_seconds": 20,
        "max_downloads": 0,
    }
    with pytest.raises(ValueError, match="conflicting retry"):
        catalogue.choose("choice", "project-future")
    followup = catalogue.follow_up(
        task.task_id, "Study timing", "Investigate concentration by hour."
    )
    assert catalogue.pending_followups("markets") == (followup,)
    assert catalogue.pending_followups("other") == ()
    catalogue.revoke(task.task_id, "Authorisation withdrawn.")
    with pytest.raises(ValueError, match="not active"):
        catalogue.follow_up(task.task_id, "New", "New reason.")
    with pytest.raises(ValueError, match="not active"):
        catalogue.analyse("markets", "missing", runner)
    with pytest.raises(ValueError, match="wrong record"):
        catalogue.revoke("markets", "Invalid target.")
    with pytest.raises(KeyError, match="unknown"):
        catalogue.get("missing")
    public = pilot.tmp / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        ResearchCatalogue(public).get("markets")
    alias = pilot.tmp / "alias"
    alias.symlink_to(catalogue.root)
    with pytest.raises(ValueError, match="private"):
        ResearchCatalogue(alias).get("markets")


def test_source_analysis_reproduction_and_forecast(pilot: Pilot, runner: AnalysisRunner) -> None:
    first_task = pilot.task("first")
    cat = pilot.catalogue
    first = pilot.source(first_task, "v1")
    analysis = cat.analyse(first_task, first.id, runner)
    dataset = cat.get(str(analysis.data["dataset"]))
    assert dataset.artifact == (
        b'{"rows":[["2026-01-01T00:00:00Z",-12300000,3],["2026-01-01T00:05:00Z",5100000,2]]}'
    )
    assert analysis.data["diagnostics"] == {
        "intervals": 2,
        "negative": 1,
        "completeness": "complete",
    }
    assert analysis.data["output_sha256"] == digest(dataset.artifact)
    count = len(cat.journal())
    assert cat.analyse(first_task, first.id, runner) == analysis
    assert cat.analyse(first_task, first.id, runner, rerun=True) == analysis
    assert len(cat.journal()) == count
    assert first == cat.register_source(
        first_task,
        pilot.quarantine,
        str(first.data["receipt"]),
        version="v1",
        released_at=START,
        change="Fixture v1.",
    )
    question = freeze_question(
        cat,
        first_task,
        question_id="next",
        rationale="Will the later release have two negative intervals?",
        outcome_version="v2",
        threshold=2,
        cutoff=START,
        deadline=START + timedelta(hours=1),
    )
    forecast = submit_forecast(cat, first_task, question.id, analysis.id, 7500)
    with pytest.raises(ValueError, match="conflicting retry"):
        submit_forecast(cat, first_task, question.id, analysis.id, 7700)
    with pytest.raises(ValueError, match="pending"):
        resolve_forecast(cat, forecast.id, analysis.id)
    with pytest.raises(ValueError, match="forecast submission"):
        resolve_forecast(cat, question.id, analysis.id)

    pilot.when += timedelta(hours=2)
    fresh = ResearchCatalogue(cat.root, clock=cat.clock)
    task = pilot.task("second")
    second = pilot.source(task, "v2", OUTCOME, first.id)
    updated = fresh.analyse(task, second.id, runner)
    with pytest.raises(ValueError, match="different input"):
        fresh.analyse(task, first.id, runner)
    score = resolve_forecast(fresh, forecast.id, updated.id)
    assert score.data["outcome"] == 1
    assert score.data["forecast_brier"] == "0.06250000"
    assert score.data["baseline_brier"] == "0.25000000"
    assert resolve_forecast(fresh, forecast.id, updated.id) == score
    assert fresh.affected(first.id) == (dataset.id, analysis.id, forecast.id, score.id)

    pilot.when += timedelta(hours=1)
    correction_task = pilot.task("correction")
    correction = pilot.source(correction_task, "v3", BASE, second.id)
    corrected = fresh.analyse(correction_task, correction.id, runner)
    with pytest.raises(ValueError, match="direct correction"):
        resolve_forecast(fresh, forecast.id, updated.id, supersedes=score.id)
    revised_score = resolve_forecast(fresh, forecast.id, corrected.id, supersedes=score.id)
    assert revised_score.data["forecast_brier"] == "0.56250000"
    assert revised_score.data["outcome"] == 0
    assert fresh.get(score.id) == score
    assert fresh.get(forecast.id) == forecast


def _review(review: ThesisReview, proposal: Record, reviewer: str = "reviewer-123") -> Record:
    return review.review(
        proposal.id,
        reviewer=reviewer,
        decision="approve",
        explanation="Checked fixture rows.",
        independence_basis="Reviewed the source independently.",
    )


def test_thesis_review_pins_revision_and_rejects_stale_base(
    pilot: Pilot, runner: AnalysisRunner
) -> None:
    task = pilot.task("first")
    source = pilot.source(task, "v1")
    analysis = pilot.catalogue.analyse(task, source.id, runner)
    thesis = ThesisReview(pilot.catalogue, Path(__file__).resolve().parents[2] / "docs")
    draft = _draft(source)
    with pytest.raises(ValidationError):
        thesis.submit(task, analysis.id, actor=AUTHOR, rationale="Malformed.", draft={})
    proposal = thesis.submit(task, analysis.id, actor=AUTHOR, rationale="Baseline.", draft=draft)
    assert "CSV line 3" in str(proposal.data["diff"])
    review = _review(thesis, proposal)
    assert _review(thesis, proposal) == review
    assert (
        thesis.submit(task, analysis.id, actor=AUTHOR, rationale="Baseline.", draft=draft)
        == proposal
    )
    with pytest.raises(ValueError, match="conflicting review retry"):
        _review(thesis, proposal, "reviewer-456")
    with pytest.raises(ValueError, match="independent reviewer"):
        _review(thesis, proposal, "agent-123")
    revision_id = "revision_first000"
    revision = pilot.catalogue.get(revision_id)
    assert str(object_value(revision.data["revision"])["content_hash"]).startswith("sha256:")
    assert object_value(object_value(proposal.data["draft"])["revision"])["content_hash"] is None

    update, stale, unchanged = (pilot.task(choice) for choice in ("update", "stale", "unchanged"))
    for followup in (update, stale, unchanged):
        assert thesis.pin(followup, revision_id) == revision
    no_change = thesis.submit(
        unchanged, analysis.id, actor=AUTHOR, rationale="Same findings.", base=revision_id
    )
    _review(thesis, no_change)
    assert no_change.data["draft"] is None
    updated_draft = copy.deepcopy(draft)
    updated_draft["thesis_created_at"] = revision.data["thesis_created_at"]
    updated_draft["statement"] = "A negative interval was in the first five minutes of the fixture."
    updated_draft["revision"] = {
        "revision_id": "revision_second00",
        "revision_number": 2,
        "previous_revision_id": revision_id,
        "change_type": "substantive_revision",
        "change_summary": "Refine the statement.",
        "content_hash": None,
    }
    candidate = thesis.submit(
        update,
        analysis.id,
        actor=AUTHOR,
        rationale="Refine.",
        base=revision_id,
        draft=updated_draft,
    )
    old_candidate = thesis.submit(
        stale, analysis.id, actor=AUTHOR, rationale="Refine.", base=revision_id, draft=updated_draft
    )
    _review(thesis, candidate)
    with pytest.raises(ValueError, match="stale thesis review"):
        _review(thesis, old_candidate)
    assert pilot.catalogue.get(revision_id) == revision


def test_source_rejects_out_of_scope_and_stale_receipts(
    pilot: Pilot, runner: AnalysisRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalogue = pilot.catalogue
    task, other = pilot.task("first"), pilot.task("second")
    receipt = pilot.download(task, BASE)

    def register(task: str, receipt: str, stamp: datetime = START) -> Record:
        return catalogue.register_source(
            task,
            pilot.quarantine,
            receipt,
            version="v1",
            released_at=stamp,
            change="First release.",
        )

    with pytest.raises(ValueError, match="scope"):
        register(other, receipt)
    with pytest.raises(ValueError, match="postdate"):
        register(task, receipt, START + timedelta(seconds=1))
    with pytest.raises(ValueError, match="aware"):
        register(task, receipt, START.replace(tzinfo=None))
    first = register(task, receipt)
    wrong_locator = pilot.download(task, BASE, locator="https://data.example/other.csv")
    with pytest.raises(ValueError, match="scope"):
        register(task, wrong_locator)
    with pytest.raises(ValueError, match="stale source predecessor"):
        pilot.source(other, "v2")
    with pytest.raises(ValueError, match="later upstream release"):
        pilot.source(other, "v2", replaces=first.id)
    with pytest.raises(ValueError, match="wrong record kind or project"):
        catalogue.analyse(pilot.task("other-project", "project-future"), first.id, runner)

    def corrupted(_self: DownloadQuarantine, _project: str, _receipt: str) -> bytes:
        return b"corrupted"

    monkeypatch.setattr(DownloadQuarantine, "read_release", corrupted)
    with pytest.raises(ValueError, match="digest mismatch"):
        register(task, receipt)


def test_followup_admission_is_atomic(pilot: Pilot) -> None:
    cat = pilot.catalogue
    task = pilot.task("first")
    proposal = cat.follow_up(
        task, "Hourly concentration", "Investigate the timing of observed prices."
    )

    def admit(project: str, programme: str = "markets", followup: str | None = None) -> Record:
        return cat.admit(
            project,
            programme,
            owner="owner-123",
            admitted_by="reviewer-123",
            objective="Count negative intervals.",
            window=WINDOW,
            source_locator=SOURCE,
            rights="CC0-1.0",
            followup=followup,
        )

    with pytest.raises(ValueError, match="identifier"):
        admit("short", "markets")
    with pytest.raises(ValueError, match="outside this programme"):
        admit("project-new", "markets", followup=task)
    cat.programme("another", "Another programme")
    with pytest.raises(ValueError, match="outside this programme"):
        admit("project-new", "another", followup=proposal.id)
    with pytest.raises(ValueError, match="conflicting retry"):
        admit("project-prices", "markets", followup=proposal.id)
    assert cat.pending_followups("markets") == (proposal,)
    admitted = admit("project-new", "markets", followup=proposal.id)
    assert proposal.id in admitted.inputs
    assert cat.pending_followups("markets") == ()
    with pytest.raises(ValueError, match="conflicting retry"):
        admit("project-newer", "markets", followup=proposal.id)
    assert pilot.task("new", "project-new")


@pytest.mark.parametrize(
    "content",
    [
        BASE.replace(b"00:05:00", b"00:03:00", 1),
        BASE + b"2026-01-01T00:00:00Z,NSW1,5\n",
        HEADER + b"2026-01-01T00:00:00Z,NSW1,5\n",
    ],
)
def test_worker_quality_failure_does_not_commit(
    pilot: Pilot, runner: AnalysisRunner, content: bytes
) -> None:
    task = pilot.task("first")
    source = pilot.source(task, "v1", content)
    with pytest.raises(AnalysisError, match="analysis_execution_failed"):
        pilot.catalogue.analyse(task, source.id, runner)
    assert pilot.catalogue.journal()[-1].payload == {"reason": "analysis_execution_failed"}
    assert pilot.catalogue.affected(source.id) == ()


def test_revocation_during_analysis_rejects_stale_results(
    pilot: Pilot,
    runner: AnalysisRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = pilot.task("first")
    source = pilot.source(task, "v1")
    run = AnalysisRunner.run

    def revoke(self: AnalysisRunner, content: bytes, window: PriceWindow) -> tuple[bytes, str, str]:
        result = run(self, content, window)
        pilot.catalogue.revoke(task, "Stop accepting results.")
        return result

    monkeypatch.setattr(AnalysisRunner, "run", revoke)
    with pytest.raises(ValueError, match="not active"):
        pilot.catalogue.analyse(task, source.id, runner)
    assert pilot.catalogue.affected(source.id) == ()


def test_forecast_temporal_rejections(pilot: Pilot, runner: AnalysisRunner) -> None:
    cat = pilot.catalogue
    first_task, target_task = pilot.task("baseline"), pilot.task("target")
    first = pilot.source(first_task, "baseline")
    deadline = START + timedelta(hours=1)

    def freeze(question_id: str = "next", threshold: int = 2, cutoff: datetime = START) -> Record:
        return freeze_question(
            cat,
            first_task,
            question_id=question_id,
            rationale="Forecast the corrected release.",
            outcome_version="target",
            threshold=threshold,
            cutoff=cutoff,
            deadline=deadline,
        )

    with pytest.raises(ValueError, match="threshold"):
        freeze(threshold=3)
    with pytest.raises(ValueError, match="cutoff"):
        freeze(cutoff=deadline)
    with pytest.raises(ValueError, match="cutoff"):
        freeze(cutoff=deadline + timedelta(seconds=1))
    question = freeze()
    pilot.when += timedelta(minutes=1)
    analysis = cat.analyse(first_task, first.id, runner)
    with pytest.raises(ValueError, match="pre-outcome"):
        submit_forecast(cat, first_task, question.id, analysis.id, 6000)
    with pytest.raises(ValueError, match="basis points"):
        submit_forecast(cat, first_task, question.id, analysis.id, 10_001)
    other = freeze("timely", cutoff=pilot.when)
    forecast = submit_forecast(cat, first_task, other.id, analysis.id, 6000)
    assert forecast == submit_forecast(cat, first_task, other.id, analysis.id, 6000)
    target = pilot.source(target_task, "target", OUTCOME, first.id)
    outcome_analysis = cat.analyse(target_task, target.id, runner)
    with pytest.raises(ValueError, match="must precede"):
        freeze("contaminated")
    with pytest.raises(ValueError, match="pre-outcome"):
        submit_forecast(cat, first_task, question.id, analysis.id, 6000)
    pilot.when = deadline
    with pytest.raises(ValueError, match="must precede"):
        freeze("late")
    with pytest.raises(ValueError, match="before deadline"):
        resolve_forecast(cat, forecast.id, outcome_analysis.id)
    assert cat.affected(forecast.id) == ()


def test_thesis_draft_rejects_invalid_metadata_and_false_references(
    pilot: Pilot,
    runner: AnalysisRunner,
) -> None:
    task = pilot.task("first")
    cat = pilot.catalogue
    source = pilot.source(task, "v1")
    analysis = cat.analyse(task, source.id, runner)
    thesis = ThesisReview(cat, Path(__file__).resolve().parents[2] / "docs")
    original = _draft(source)
    bad_hash = copy.deepcopy(original)
    object_value(bad_hash["revision"])["content_hash"] = "sha256:" + "a" * 64
    with pytest.raises(ValueError, match="metadata"):
        thesis.submit(task, analysis.id, actor=AUTHOR, rationale="Bad hash.", draft=bad_hash)
    with pytest.raises(ValueError, match="exceeds"):
        thesis.submit(
            task, analysis.id, actor=AUTHOR, rationale="Too large.", draft={"text": "x" * 131072}
        )
    with pytest.raises(ValueError, match="must cite"):
        thesis.submit(
            task,
            analysis.id,
            actor=AUTHOR,
            rationale="Missing evidence.",
            draft={**original, "evidence": []},
        )
    with pytest.raises(ValueError, match="requires a pinned"):
        thesis.submit(task, analysis.id, actor=AUTHOR, rationale="No change.")
    bad_citation = copy.deepcopy(original)
    assessment = object_value(bad_citation["epistemic_assessment"])
    object_value(assessment["empirical_basis"])["source_ids"] = ["source_missing123"]
    with pytest.raises(ValueError, match="declared source"):
        thesis.submit(
            task, analysis.id, actor=AUTHOR, rationale="False citation.", draft=bad_citation
        )
    proposal = thesis.submit(task, analysis.id, actor=AUTHOR, rationale="Baseline.", draft=original)
    with pytest.raises(ValueError, match="proposal"):
        _review(thesis, analysis)
    with pytest.raises(ValueError, match="unsupported review decision"):
        thesis.review(
            proposal.id,
            reviewer="reviewer-123",
            decision=cast(Decision, "abstain"),
            explanation="Abstaining.",
            independence_basis="Reviewed independently.",
        )
    with pytest.raises(ValueError, match="invalid reviewer identifier"):
        thesis.review(
            proposal.id,
            reviewer="short",
            decision="approve",
            explanation="Short ID.",
            independence_basis="Reviewed independently.",
        )
    with pytest.raises(ValueError, match="conflicts"):
        thesis.review(
            proposal.id,
            reviewer="reviewer-123",
            decision="approve",
            explanation="Conflicted.",
            independence_basis="Reviewed independently.",
            conflicts=("Related project.",),
        )
    _review(thesis, proposal)
    other = pilot.task("other")
    with pytest.raises(KeyError, match="unknown"):
        thesis.submit(
            other, analysis.id, actor=AUTHOR, rationale="Forgot to pin.", base="revision_first000"
        )
    thesis.pin(other, "revision_first000")
    with pytest.raises(ValueError, match="has not been pinned"):
        thesis.submit(
            other, analysis.id, actor=AUTHOR, rationale="Wrong pin.", base="revision_second00"
        )
    with pytest.raises(ValueError, match="identifier is already"):
        thesis.submit(
            other, analysis.id, actor=AUTHOR, rationale="Duplicate revision ID.", draft=original
        )
    with pytest.raises(ValueError, match="stale thesis base"):
        thesis.submit(
            other,
            analysis.id,
            actor=AUTHOR,
            rationale="Duplicate thesis.",
            draft=_draft(source, "revision_alternate"),
        )
    future_task = pilot.task("future-thesis", "project-future")
    future_source = pilot.source(future_task, "v1", project="project-future")
    future_analysis = cat.analyse(future_task, future_source.id, runner)
    with pytest.raises(ValueError, match="another project"):
        thesis.submit(
            future_task,
            future_analysis.id,
            actor=AUTHOR,
            rationale="Cross-project thesis collision.",
            draft=_draft(future_source, "revision_future00"),
        )
    no_change = thesis.submit(
        other, analysis.id, actor=AUTHOR, rationale="Same finding.", base="revision_first000"
    )
    cat.revoke(other, "Do not accept new work.")
    with pytest.raises(ValueError, match="not active"):
        _review(thesis, no_change)
    assert (
        thesis.review(
            no_change.id,
            reviewer="reviewer-456",
            decision="reject",
            explanation="Opt-in withdrawn.",
            independence_basis="No author role.",
        ).kind
        == "review"
    )


def test_catalogue_uses_a_utc_wall_clock(tmp_path: Path) -> None:
    cat = ResearchCatalogue(tmp_path / "catalogue")
    assert datetime.fromisoformat(
        cat.programme("market", "Markets").created_at
    ).utcoffset() == timedelta(0)
