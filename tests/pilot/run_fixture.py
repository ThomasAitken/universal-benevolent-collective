"""Operator entry point for the local two-cycle C07 acceptance fixture."""

import argparse
import asyncio
import fcntl
import json
import os
import sqlite3
import stat
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from tests.clamd_fixture import clamd_listener, scanner_for
from tests.egress_fixture import APPROVED, PILOT_PAGES, TLS_PORT
from tests.fakes import ScriptedAgentBackend
from tests.pilot.fixtures import AUTHOR, BASE, OUTCOME, SOURCE, START, WINDOW, draft_thesis
from ubc_node.artifacts.quarantine import ClamAVScanner, DownloadQuarantine, DownloadScope
from ubc_node.browser.browser_contracts import (
    BrowserSession,
    BrowserSessionState,
    PageInspection,
    TabInfo,
)
from ubc_node.browser.isolation.isolated_browser import IsolatedBrowserError, PodmanBrowserRuntime
from ubc_node.browser.isolation.isolation_contracts import IsolatedBrowserRuntime
from ubc_node.core.contracts import ActionProposal, EventKind, RunState, TaskSpec
from ubc_node.core.event_log import JsonlEventLog
from ubc_node.pilot.analysis_runner import AnalysisRunner
from ubc_node.pilot.browser_research import run_browser_research
from ubc_node.pilot.catalogue import (
    Object,
    Record,
    ResearchCatalogue,
    active_project,
    digest,
    encode,
    find,
    object_value,
    put,
)
from ubc_node.pilot.forecasts import freeze_question, resolve_forecast, submit_forecast
from ubc_node.pilot.thesis_review import Decision, ThesisReview

ROOT = Path(__file__).resolve().parents[2]
PROGRAMME = "fixture-markets"
PROJECT = "fixture-nsw-prices"
ALTERNATE = "fixture-vic-prices"
OWNER = "fixture-owner"
DEADLINE = START + timedelta(hours=1)
OUTCOME_TIME = START + timedelta(hours=2)
CANARY = b"fixture-c07-private-sentinel"
FIXTURE_ORIGIN = f"https://{APPROVED}:{TLS_PORT}"
BASE_ANALYSIS = "analysis:task-c07-v1"
BASE_REVISION = "revision_first000"
FORECAST = "forecast:question:c07-next-release"

type Cycle = Literal[1, 2]


class FixtureScanner(ClamAVScanner):
    """Exercise the real INSTREAM client with an exact synthetic input."""

    def scan(self, content: bytes) -> None:
        with (
            clamd_listener() as listener,
            scanner_for(listener, b"stream: OK\0", expected=content) as client,
        ):
            client.scan(content)


def fixture_browser(url: str, text: str) -> IsolatedBrowserRuntime:
    session = Mock(
        spec=BrowserSession,
        state=BrowserSessionState.ACTIVE,
        open_url=AsyncMock(
            return_value=PageInspection("tab", url, "Fixture", text, False, False, 200)
        ),
        list_tabs=AsyncMock(return_value=(TabInfo("tab", url, "Fixture", True),)),
    )
    return cast(IsolatedBrowserRuntime, Mock(launch=AsyncMock(return_value=session)))


class FixtureAgent:
    """Deterministic choices using the granted charters and accepted diagnostics."""

    def choose(self, charters: tuple[Record, ...], region: str) -> str:
        return next(charter.id for charter in charters if charter.data["region"] == region)

    def forecast(self, analysis: Record) -> int:
        return 7500 if object_value(analysis.data["diagnostics"])["negative"] else 5000

    def followup(self, before: Record, after: Record) -> tuple[str, str]:
        previous = object_value(before.data["diagnostics"])["negative"]
        current = object_value(after.data["diagnostics"])["negative"]
        return (
            "Measure revision sensitivity in NSW1",
            f"The correction moved the count from {previous} to {current}. "
            "Proposed charter: compare exactly two vintages of one further day of NSW1 "
            "five-minute AUD/MWh CSVs from the same approved locator, reject invalid intervals, "
            "and report changed signs without causal attribution. Request a fresh owner grant.",
        )


class FixtureRun:
    def __init__(
        self,
        workspace: Path,
        cycle: Cycle,
        runner: AnalysisRunner,
        scanner: ClamAVScanner,
        browser: PodmanBrowserRuntime | None = None,
    ) -> None:
        self.workspace = workspace
        self.cycle = cycle
        self.runner = runner
        self.scanner = scanner
        self.browser = browser
        self.stamp = START if cycle == 1 else OUTCOME_TIME
        self.version = f"v{cycle}"
        self.choice = f"c07-{self.version}"
        self.task_id = f"task-{self.choice}"
        self.proposal_id = f"proposal:{self.task_id}"
        self.catalogue = ResearchCatalogue(workspace / "catalogue", clock=lambda: self.stamp)
        self.theses = ThesisReview(self.catalogue, ROOT / "docs")
        self.quarantine = DownloadQuarantine(workspace / "quarantine")
        self.agent = FixtureAgent()

    def lookup(self, key: str) -> Record | None:
        with self.catalogue.transaction(readonly=True) as db:
            return find(db, key)

    def run(self) -> Record:
        cat = self.catalogue
        if self.cycle == 1:
            self.seed()
            previous_source, previous_analysis = None, None
            affected: list[str] = []
        else:
            baseline = self.lookup("fixture:cycle-1")
            review = self.lookup(f"review:{baseline.data['proposal']}") if baseline else None
            if (
                baseline is None
                or review is None
                or object_value(review.data["request"])["decision"] != "approve"
            ):
                raise ValueError("cycle 2 requires an approved baseline proposal")
            cat.get(FORECAST)
            previous_analysis = cat.get(str(baseline.data["analysis"]))
            previous_source = cat.get(str(previous_analysis.data["source"]))
            discovered = cat.affected(previous_source.id)
            with cat.transaction() as db:
                key = "fixture:impacts"
                impact = find(db, key)
                if impact is None:
                    impact = put(
                        db,
                        key,
                        "fixture_impacts",
                        PROJECT,
                        {"affected": list(discovered)},
                        (previous_source.id,),
                    )
            affected = cast(list[str], impact.data["affected"])

        grant = cat.opt_in(self.choice, PROGRAMME, OWNER, (PROJECT, ALTERNATE))
        project = self.agent.choose(tuple(cat.get(key) for key in grant.inputs), WINDOW.region)
        task = cat.choose(grant.id, project)
        with cat.transaction(readonly=True) as db:
            active_project(db, task.task_id)
        checkpoint = self.lookup(f"fixture:cycle-{self.cycle}")
        if checkpoint is not None:
            return checkpoint
        evidence = self.capture(task)
        source = self.acquire(project, previous_source)
        analysis = cat.analyse(task.task_id, source.id, self.runner)

        base = self.theses.pin(task.task_id, BASE_REVISION) if previous_analysis else None
        proposal = self.propose(analysis, source, evidence, base)
        links = [evidence.id, analysis.id, proposal.id]
        if previous_analysis is None:
            question = freeze_question(
                cat,
                task.task_id,
                question_id="c07-next-release",
                outcome_version="v2",
                rationale="Check whether the next vintage shows two negative intervals.",
                threshold=2,
                cutoff=START,
                deadline=DEADLINE,
            )
            forecast = submit_forecast(
                cat, task.task_id, question.id, analysis.id, self.agent.forecast(analysis)
            )
            followup = None
        else:
            title, rationale = self.agent.followup(previous_analysis, analysis)
            followup = cat.follow_up(task.task_id, title, rationale)
            forecast = resolve_forecast(cat, FORECAST, analysis.id)
            links.extend(("fixture:impacts", followup.id))
        links.append(forecast.id)

        with cat.transaction() as db:
            active_project(db, task.task_id)
            return put(
                db,
                f"fixture:cycle-{self.cycle}",
                "fixture_checkpoint",
                project,
                {
                    "analysis": analysis.id,
                    "source": source.id,
                    "evidence": evidence.id,
                    "proposal": proposal.id,
                    "forecast_record": forecast.id,
                    "forecast_assessment": (
                        "Keep the demonstration rule unchanged after one outcome. Compare calibration "
                        "over more releases before changing the rule."
                        if previous_analysis is not None
                        else None
                    ),
                    "followup": followup.id if followup else None,
                    "earlier_dependants": list(affected),
                },
                tuple(links),
                task=task.task_id,
            )

    def seed(self) -> None:
        self.catalogue.programme(PROGRAMME, "Electricity market fixture")
        for project, region in ((PROJECT, "NSW1"), (ALTERNATE, "VIC1")):
            self.catalogue.admit(
                project,
                PROGRAMME,
                owner=OWNER,
                admitted_by="fixture-operator",
                objective="Measure the frequency of negative settlement prices",
                window=replace(WINDOW, region=region),
                source_locator=SOURCE,
                rights="Synthetic fixture. CC0-1.0.",
            )

    def capture(self, task: TaskSpec) -> Record:
        url = f"{FIXTURE_ORIGIN}/pilot/{self.version}"
        text = PILOT_PAGES[f"/pilot/{self.version}"]
        image = self.browser.image_digest if self.browser else "sha256:" + "0" * 64
        mode = "isolated_chromium" if self.browser else "fixture_browser"
        configuration: Object = {
            "browser": mode,
            "image": image,
            "scanner": "fixture_instream" if isinstance(self.scanner, FixtureScanner) else "clamd",
        }
        with self.catalogue.transaction() as db:
            active_project(db, task.task_id)
            previous = find(db, "fixture:configuration")
            if previous is not None and previous.data != configuration:
                raise ValueError(
                    "workspace configuration mismatch; keep the same browser manifest and "
                    "scanner settings or use a new workspace"
                )
            put(db, "fixture:configuration", "fixture_configuration", None, configuration)
            key = f"evidence:{task.task_id}"
            evidence = find(db, key)
            if evidence is not None:
                return evidence

        filename = f"browser-{self.version}-{uuid4().hex}.jsonl"
        path = self.workspace / filename
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        audit = JsonlEventLog(path)
        result = asyncio.run(self.browse(task, url, text, image, audit))
        if result is not RunState.COMPLETED:
            raise ValueError(f"browser research {result.value}; see {filename}")
        observations = [
            object_value(event.payload["observation"])
            for event in audit.read_all()
            if event.kind is EventKind.BROWSER_ACTION_COMPLETED
        ]
        if len(observations) != 1 or text not in str(observations[0]["content"]):
            raise ValueError(f"fixture page changed; see {filename}")
        with self.catalogue.transaction() as db:
            project = active_project(db, task.task_id)
            return put(
                db,
                key,
                "browser_evidence",
                project,
                {
                    "journal": filename,
                    "journal_sha256": digest(path.read_bytes()),
                    "observation": observations[0],
                    "mode": mode,
                    "rights": "Synthetic fixture. CC0-1.0.",
                },
                (task.task_id,),
                task=task.task_id,
            )

    async def browse(
        self,
        task: TaskSpec,
        url: str,
        text: str,
        image: str,
        audit: JsonlEventLog,
    ) -> RunState:
        agent = ScriptedAgentBackend(
            [
                ActionProposal.browser(
                    "Read the fixture release note", {"operation": "open", "url": url}
                ),
                ActionProposal.complete("Release note captured for operator inspection."),
            ]
        )
        runtime = self.browser or fixture_browser(url, text)
        execution = asyncio.create_task(
            run_browser_research(
                task,
                {f"release-{self.version}": url},
                runtime=runtime,
                image_digest=image,
                agent=agent,
                event_sink=audit,
            )
        )
        try:
            while not execution.done():
                await asyncio.wait((execution,), timeout=0.2)
                if self.lookup(f"revoke:{task.task_id}") is not None:
                    raise ValueError("task is not active")
            return execution.result().state
        finally:
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)

    def acquire(self, project: str, previous: Record | None) -> Record:
        content = BASE if self.cycle == 1 else OUTCOME
        incoming = self.workspace / f"incoming-{self.version}.csv"
        with self.catalogue.transaction(readonly=True) as db:
            active_project(db, self.task_id)
        try:
            incoming.write_bytes(content)
            release = self.quarantine.ingest(
                DownloadScope(project, self.task_id, self.version, SOURCE),
                incoming,
                "prices.csv",
                expected_sha256=digest(content),
                scanner=self.scanner,
                canaries=(CANARY,),
                reuse_release=True,
            )
        finally:
            incoming.unlink(missing_ok=True)
        return self.catalogue.register_source(
            self.task_id,
            self.quarantine,
            release.receipt,
            version=self.version,
            replaces=previous.id if previous else None,
            released_at=self.stamp,
            change="Initial fixture data." if previous is None else "Corrected CSV line 2 to -5.1.",
        )

    def propose(
        self,
        analysis: Record,
        source: Record,
        evidence: Record,
        base: Record | None,
    ) -> Record:
        diagnostics = object_value(analysis.data["diagnostics"])
        statement = (
            f"{diagnostics['negative']} of {diagnostics['intervals']} NSW1 fixture intervals "
            "had a negative price."
        )
        draft = draft_thesis(
            source,
            BASE_REVISION if base is None else "revision_update000",
            as_of=self.stamp.isoformat(),
        )
        draft["statement"] = statement
        observation = object_value(evidence.data["observation"])
        entries = cast(list[Object], draft["evidence"])
        entries[0].update({"locator": "CSV lines 2-3", "claim_or_finding": statement})
        if base is None:
            rationale = (
                f"Baseline count from both rows. Interval definition: {observation['evidence_id']}."
            )
        else:
            rationale = (
                "The corrected first CSV row is also negative. Negative prices still occurred, "
                "but the frequency increased from one half to all observed intervals. "
                "Retain the earlier release and forecast as dated records. "
                f"Release note: {observation['evidence_id']}."
            )
            object_value(draft["revision"]).update(
                {
                    "revision_number": 2,
                    "previous_revision_id": base.id,
                    "change_type": "evidence_update",
                    "change_summary": "Count recomputed after a source correction.",
                }
            )
            draft["thesis_created_at"] = base.data["thesis_created_at"]
            entries[0]["explanation"] = "Both NSW1 rows have prices below zero."
        return self.theses.submit(
            self.task_id,
            analysis.id,
            actor=AUTHOR,
            rationale=rationale,
            draft=draft,
            base=base.id if base else None,
        )

    def _verify_cycle(self, cycle: Cycle) -> Object:
        task_id = f"task-c07-v{cycle}"
        analysis = self.catalogue.get(f"analysis:{task_id}")
        dataset = self.catalogue.get(str(analysis.data["dataset"]))
        source = self.catalogue.get(str(analysis.data["source"]))
        if dataset.artifact is None or source.artifact is None:
            raise ValueError(f"cycle {cycle} dataset or source artifact is missing")
        source_digest = digest(source.artifact)
        if (
            source_digest != source.data["sha256"]
            or source_digest != analysis.data["source_sha256"]
        ):
            raise ValueError(f"cycle {cycle} CSV digest mismatch")
        self.catalogue.analyse(task_id, source.id, self.runner, rerun=True)
        rows = object_value(json.loads(dataset.artifact))["rows"]
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
            raise ValueError(f"cycle {cycle} dataset rows are malformed")
        sample = rows[0]
        line = int(str(sample[2]))
        evidence = self.catalogue.get(f"evidence:{task_id}")
        audit = self.workspace / str(evidence.data["journal"])
        if digest(audit.read_bytes()) != evidence.data["journal_sha256"]:
            raise ValueError(f"cycle {cycle} browser journal digest mismatch")
        return {
            "analysis": analysis.id,
            "dataset": dataset.id,
            "source": source.id,
            "source_sha256": source.data["sha256"],
            "sample": sample,
            "source_csv_line": source.artifact.splitlines()[line - 1].decode(),
            "browser_evidence": evidence.id,
            "browser_journal": evidence.data["journal"],
        }

    def verify(self) -> Object:
        result = self._verify_cycle(1)
        update = self.lookup("fixture:cycle-2")
        if update is not None:
            result["update"] = self._verify_cycle(2)
            forecast = self.catalogue.get(FORECAST)
            question = self.catalogue.get("question:c07-next-release")
            analysis = self.catalogue.get(str(update.data["analysis"]))
            score = self.catalogue.get(str(update.data["forecast_record"]))
            diagnostics = object_value(analysis.data["diagnostics"])
            outcome = int(int(str(diagnostics["negative"])) >= int(str(question.data["threshold"])))
            for probability, field in (
                ("forecast_basis_points", "forecast_brier"),
                ("baseline_basis_points", "baseline_brier"),
            ):
                error = Decimal(str(forecast.data[probability])) / 10_000 - outcome
                if format(error**2, ".8f") != score.data[field]:
                    raise ValueError(f"{field} does not reproduce")
            if (
                outcome != score.data["outcome"]
                or analysis.data["source"] != question.data["target"]
            ):
                raise ValueError("forecast resolution does not match the frozen rule")
            result["score"] = score.data
        return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        default="run",
        choices=("run", "review", "revoke", "verify", "inspect", "journal"),
    )
    parser.add_argument("--workspace", type=Path, default=ROOT / ".ubc" / "c07")
    parser.add_argument("--cycle", type=int, choices=(1, 2), default=1)
    parser.add_argument("--browser-manifest", type=Path)
    parser.add_argument("--scanner-socket", type=Path)
    parser.add_argument("--engine", default="docker")
    parser.add_argument("--record")
    parser.add_argument("--artifact", action="store_true", help="Print a stored CSV or dataset.")
    parser.add_argument(
        "--receipt", help="Inspect a quarantine acquisition in the fixture project."
    )
    parser.add_argument("--reviewer")
    parser.add_argument("--decision", choices=("approve", "request_changes", "reject"))
    parser.add_argument("--reason")
    parser.add_argument("--independence")
    parser.add_argument("--conflict", action="append", default=[])
    args = parser.parse_args(argv)

    try:
        workspace: Path = args.workspace.absolute()
        if args.action == "run":
            workspace.parent.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(mode=0o700, exist_ok=True)
        elif not workspace.exists():
            raise ValueError(f"workspace does not exist: {workspace}")
        info = workspace.lstat()
        if (
            workspace.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("workspace must be a private operator-owned directory")
        browser = (
            PodmanBrowserRuntime(ROOT, args.browser_manifest) if args.browser_manifest else None
        )
        scanner = (
            ClamAVScanner(args.scanner_socket)
            if args.scanner_socket
            else FixtureScanner(Path("/unused"))
        )
        fixture = FixtureRun(
            workspace, cast(Cycle, args.cycle), AnalysisRunner(args.engine), scanner, browser
        )
        if args.action in {"run", "verify"}:
            with (workspace / "execution.lock").open("ab") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.action == "verify":
                    print(encode(fixture.verify()).decode())
                    return 0
                record = fixture.run()
        elif args.action == "review":
            if not all((args.reviewer, args.decision, args.reason, args.independence)):
                raise ValueError(
                    "review requires --reviewer, --decision, --reason and --independence"
                )
            fixture.catalogue.get(f"fixture:cycle-{args.cycle}")
            record = fixture.theses.review(
                fixture.proposal_id,
                reviewer=args.reviewer,
                decision=cast(Decision, args.decision),
                explanation=args.reason,
                independence_basis=args.independence,
                conflicts=tuple(args.conflict),
            )
        elif args.action == "revoke":
            if not args.reason:
                raise ValueError("revoke requires --reason")
            record = fixture.catalogue.revoke(fixture.task_id, args.reason)
        elif args.action == "journal":
            print(encode([event.to_record() for event in fixture.catalogue.journal()]).decode())
            return 0
        else:
            if args.receipt:
                acquisition = fixture.quarantine.inspect_acquisition(PROJECT, args.receipt)
                print(json.dumps(asdict(acquisition), sort_keys=True))
                return 0
            record = fixture.catalogue.get(args.record or f"fixture:cycle-{args.cycle}")
            if args.artifact:
                if record.artifact is None:
                    raise ValueError("record has no stored artifact")
                print(record.artifact.decode(), end="")
                return 0
        print(
            encode(
                {
                    "id": record.id,
                    "kind": record.kind,
                    "inputs": list(record.inputs),
                    "created_at": record.created_at,
                    "data": record.data,
                }
            ).decode()
        )
    except KeyboardInterrupt:
        return 130
    except (KeyError, OSError, ValueError, sqlite3.Error, IsolatedBrowserError) as error:
        print(f"C07 fixture: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
