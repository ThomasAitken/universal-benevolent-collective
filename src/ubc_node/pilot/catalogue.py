"""Single-node, operator-owned C06 catalogue and task-scoped research operations."""

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from ubc_node.contracts import EventKind, JsonValue, RunBudget, RunEvent, RunState, TaskSpec
from ubc_node.pilot.analysis_runner import AnalysisError, AnalysisRunner, PriceWindow
from ubc_node.pilot.price_method import INTERVAL
from ubc_node.quarantine import DownloadQuarantine, DownloadScope

type Object = dict[str, JsonValue]

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_PRICE = re.compile(r"-?[0-9]{1,6}(?:\.[0-9]{1,6})?")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    sequence INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    project TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    artifact BLOB
) STRICT;
CREATE TABLE IF NOT EXISTS dependencies (
    dependant TEXT NOT NULL REFERENCES records(id),
    input TEXT NOT NULL REFERENCES records(id),
    PRIMARY KEY (dependant, input)
) STRICT;
CREATE INDEX IF NOT EXISTS reverse_dependencies ON dependencies(input);
CREATE TABLE IF NOT EXISTS journal (
    sequence INTEGER PRIMARY KEY,
    event TEXT NOT NULL
) STRICT;
"""


def encode(value: JsonValue) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def object_value(value: JsonValue) -> Object:
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def id_value(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid research identifier")
    return value


def note(value: str) -> str:
    if not value.strip() or len(value) > 5000:
        raise ValueError("expected explanatory text of up to 5000 characters")
    return value


def now() -> datetime:
    return datetime.now(UTC)


def utc(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("expected a timezone-aware timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class Record:
    id: str
    kind: str
    project: str | None
    created_at: str
    data: Object
    inputs: tuple[str, ...]
    artifact: bytes | None


class CatalogueConnection(sqlite3.Connection):
    clock: Callable[[], datetime]


class ResearchCatalogue:
    """Trusted application API. A model receives bounded choices and proposals via its caller."""

    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.root = root
        self.clock = clock or now

    @contextmanager
    def transaction(self, *, readonly: bool = False) -> Generator[CatalogueConnection]:
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("research root must be a private operator-owned directory")
        db_path = self.root / "research.sqlite3"
        with closing(sqlite3.connect(db_path, factory=CatalogueConnection)) as db, db:
            db.clock = self.clock
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            initialized = (
                db.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name IN ('records', 'dependencies', 'journal')"
                ).fetchone()[0]
                == 3
            )
            if not initialized:
                db.executescript(_SCHEMA)
            if not readonly:
                db.execute("BEGIN IMMEDIATE")
            yield db

    def get(self, record_id: str) -> Record:
        with self.transaction(readonly=True) as db:
            return read(db, record_id)

    def journal(self) -> tuple[RunEvent, ...]:
        with self.transaction(readonly=True) as db:
            return tuple(
                RunEvent.from_record(object_value(json.loads(row[0])))
                for row in db.execute("SELECT event FROM journal ORDER BY sequence")
            )

    def affected(self, record_id: str) -> tuple[str, ...]:
        """Find transitive dependants in catalogue order after reopening the store."""
        with self.transaction(readonly=True) as db:
            read(db, record_id)
            rows = db.execute(
                """
                WITH RECURSIVE affected(id) AS (
                    SELECT dependant FROM dependencies WHERE input = ?
                    UNION
                    SELECT dependant FROM dependencies JOIN affected ON input = id
                )
                SELECT records.id FROM affected JOIN records USING (id) ORDER BY records.sequence
                """,
                (record_id,),
            )
            return tuple(str(row[0]) for row in rows)

    def programme(self, programme_id: str, title: str) -> Record:
        with self.transaction() as db:
            return put(db, id_value(programme_id), "programme", None, {"title": note(title)})

    def admit(
        self,
        project: str,
        programme: str,
        *,
        owner: str,
        admitted_by: str,
        objective: str,
        window: PriceWindow,
        source_locator: str,
        rights: str,
        followup: str | None = None,
    ) -> Record:
        """Record the human-admitted charter. A separate grant records owner opt-in."""
        if not 8 <= len(project) <= 64:
            raise ValueError("project identifier must contain 8 to 64 characters")
        DownloadScope(project, "charter", "charter", source_locator)
        region, start, end = window.arguments()
        with self.transaction() as db:
            require(db, programme, "programme", None)
            inputs = (programme,)
            if followup is not None:
                proposal = read(db, followup)
                if (
                    proposal.kind != "followup"
                    or programme not in read(db, str(proposal.project)).inputs
                ):
                    raise ValueError("follow-up is outside this programme")
                put(
                    db,
                    f"admission:{followup}",
                    "admission",
                    project,
                    {"admitted_by": id_value(admitted_by)},
                    (followup,),
                )
                inputs += (followup,)
            return put(
                db,
                project,
                "project",
                project,
                {
                    "owner": id_value(owner),
                    "admitted_by": id_value(admitted_by),
                    "objective": note(objective),
                    "region": region,
                    "start": start,
                    "end": end,
                    "source_locator": source_locator,
                    "rights": note(rights),
                    "price_unit": "AUD/MWh",
                    "interval_minutes": 5,
                    "quality_policy": "reject missing, duplicate or unaligned selected intervals",
                },
                inputs,
            )

    def opt_in(self, choice: str, programme: str, owner: str, projects: tuple[str, ...]) -> Record:
        """One owner grant authorizes one selection from up to eight admitted projects."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,122}", choice):
            raise ValueError("invalid choice identifier")
        if not 1 <= len(set(projects)) == len(projects) <= 8:
            raise ValueError("expected one to eight distinct choices")
        with self.transaction() as db:
            for project in projects:
                charter = require(db, project, "project", project)
                if programme not in charter.inputs or charter.data["owner"] != owner:
                    raise ValueError("project is outside the owner's programme")
            return put(db, id_value(choice), "choice", None, {"owner": owner}, projects)

    def choose(self, choice: str, project: str) -> TaskSpec:
        with self.transaction() as db:
            grant = require(db, choice, "choice", None)
            if project not in grant.inputs:
                raise ValueError("project is outside this choice")
            charter = require(db, project, "project", project)
            budget = RunBudget(max_duration_seconds=20, max_downloads=0)
            task = put(
                db,
                f"task-{choice}",
                "task",
                project,
                {
                    "budget": {
                        "max_duration_seconds": budget.max_duration_seconds,
                        "max_downloads": budget.max_downloads,
                    }
                },
                (choice, project),
            )
            return TaskSpec(
                task.id,
                str(charter.data["objective"]),
                budget,
                {"project": project},
            )

    def revoke(self, task: str, reason: str) -> Record:
        with self.transaction() as db:
            active = read(db, task)
            require(db, task, "task", active.project)
            return put(
                db,
                f"revoke:{task}",
                "revocation",
                active.project,
                {"reason": note(reason)},
                (task,),
                task=task,
            )

    def follow_up(self, task: str, title: str, rationale: str) -> Record:
        with self.transaction() as db:
            project = active_project(db, task)
            return put(
                db,
                f"followup:{task}",
                "followup",
                project,
                {"title": note(title), "rationale": note(rationale), "status": "pending_admission"},
                (task,),
                task=task,
            )

    def pending_followups(self, programme: str) -> tuple[Record, ...]:
        with self.transaction(readonly=True) as db:
            require(db, programme, "programme", None)
            rows = db.execute(
                """
                SELECT proposal.id FROM records AS proposal
                JOIN dependencies ON dependencies.dependant = proposal.project
                WHERE proposal.kind = 'followup' AND dependencies.input = ?
                    AND NOT EXISTS (SELECT 1 FROM records WHERE id = 'admission:' || proposal.id)
                ORDER BY proposal.sequence
                """,
                (programme,),
            )
            return tuple(read(db, str(row[0])) for row in rows)

    def register_source(
        self,
        task: str,
        quarantine: DownloadQuarantine,
        receipt: str,
        *,
        version: str,
        released_at: datetime,
        replaces: str | None = None,
        change: str,
    ) -> Record:
        """Bind a C04 receipt to the charter and preserve the exact quarantined bytes."""
        with self.transaction() as db:
            project = active_project(db, task)
            charter = read(db, project)
            acquisition = quarantine.inspect_acquisition(project, receipt)
            if (
                not acquisition.released
                or acquisition.scope.task != task
                or acquisition.scope.source != charter.data["source_locator"]
            ):
                raise ValueError("receipt is outside the approved acquisition scope")
            content = quarantine.read_release(project, receipt)
            if digest(content) != acquisition.expected_sha256:
                raise ValueError("quarantine release digest mismatch")
            release = next(event for event in acquisition.events if event.state == "released")
            imported = utc(datetime.fromisoformat(release.occurred_at))
            if utc(released_at) > imported:
                raise ValueError("upstream release cannot postdate its acquisition")
            source = f"source:{project}:{id_value(version)}"
            existing = find(db, source)
            if existing is None and latest(db, project, "source") != replaces:
                raise ValueError("stale source predecessor")
            if replaces is not None and str(read(db, replaces).data["released_at"]) >= utc(
                released_at
            ):
                raise ValueError("replacement must have a later upstream release time")
            return put(
                db,
                source,
                "source",
                project,
                {
                    "schema": "electricity-price-csv-v1",
                    "version": version,
                    "receipt": receipt,
                    "locator": acquisition.scope.source,
                    "session": acquisition.scope.session,
                    "released_at": utc(released_at),
                    "acquired_at": imported,
                    "acquisition_decision": release.decision,
                    "acquisition": "released",
                    "sha256": digest(content),
                    "size": len(content),
                    "rights": charter.data["rights"],
                    "replaces": replaces,
                    "change": note(change),
                },
                (task,),
                content,
                task=task,
            )

    def analyse(
        self, task: str, source_id: str, runner: AnalysisRunner, *, rerun: bool = False
    ) -> Record:
        """Commit a dataset and manifest together, or compare a rerun against the accepted bytes."""
        with self.transaction() as db:
            project = active_project(db, task)
            source = require(db, source_id, "source", project)
            charter = read(db, project)
            existing = find(db, f"analysis:{task}")
            if existing is not None and existing.data["source"] != source_id:
                raise ValueError("task already analysed a different input")
            if existing is not None and not rerun:
                return existing
        window = PriceWindow(
            str(charter.data["region"]),
            datetime.fromisoformat(str(charter.data["start"])),
            datetime.fromisoformat(str(charter.data["end"])),
        )
        assert source.artifact is not None
        try:
            output, code, image = runner.run(source.artifact, window)
            dataset, diagnostics = validate_dataset(output, source.artifact, window)
        except AnalysisError as error:
            with self.transaction() as db:
                journal(db, task, EventKind.STEP_FAILED, RunState.FAILED, {"reason": str(error)})
            raise
        with self.transaction() as db:
            active_project(db, task)
            dataset_release = put(
                db,
                f"dataset:{task}",
                "dataset",
                project,
                {
                    "schema": "price-intervals-v1",
                    "sha256": digest(dataset),
                    "columns": ["interval_utc", "price_micro_aud_per_mwh", "source_csv_line"],
                    "rights": source.data["rights"],
                    "quality": diagnostics,
                    "region": window.region,
                },
                (source_id,),
                dataset,
                task=task,
            )
            return put(
                db,
                f"analysis:{task}",
                "analysis",
                project,
                {
                    "method": "negative-price-frequency-v1",
                    "code_sha256": code,
                    "image": image,
                    "parameters": list(window.arguments()),
                    "source": source_id,
                    "source_sha256": source.data["sha256"],
                    "dataset": dataset_release.id,
                    "output_sha256": dataset_release.data["sha256"],
                    "diagnostics": diagnostics,
                },
                (task, dataset_release.id),
                task=task,
            )


def find(db: sqlite3.Connection, key: str) -> Record | None:
    row = db.execute(
        "SELECT id, kind, project, created_at, payload, artifact FROM records WHERE id = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    inputs = tuple(
        str(item[0])
        for item in db.execute(
            "SELECT input FROM dependencies WHERE dependant = ? ORDER BY input", (key,)
        )
    )
    return Record(
        str(row[0]),
        str(row[1]),
        cast(str | None, row[2]),
        str(row[3]),
        object_value(json.loads(row[4])),
        inputs,
        cast(bytes | None, row[5]),
    )


def read(db: sqlite3.Connection, key: str) -> Record:
    record = find(db, key)
    if record is None:
        raise KeyError("unknown research record")
    return record


def require(db: sqlite3.Connection, key: str, kind: str, project: str | None) -> Record:
    record = read(db, key)
    if (record.kind, record.project) != (kind, project):
        raise ValueError("wrong record kind or project")
    return record


def latest(db: sqlite3.Connection, project: str, kind: str) -> str | None:
    row = db.execute(
        "SELECT id FROM records WHERE project = ? AND kind = ? ORDER BY sequence DESC LIMIT 1",
        (project, kind),
    ).fetchone()
    return None if row is None else str(row[0])


def active_project(db: sqlite3.Connection, task: str) -> str:
    record = read(db, task)
    if record.kind != "task" or record.project is None or find(db, f"revoke:{task}") is not None:
        raise ValueError("task is not active")
    return record.project


def put(
    db: CatalogueConnection,
    key: str,
    kind: str,
    project: str | None,
    data: Object,
    inputs: tuple[str, ...] = (),
    artifact: bytes | None = None,
    *,
    task: str | None = None,
) -> Record:
    content = encode(data)
    previous = find(db, key)
    links = tuple(sorted(set(inputs)))
    if previous is not None:
        if (
            previous.kind,
            previous.project,
            encode(previous.data),
            previous.inputs,
            previous.artifact,
        ) != (
            kind,
            project,
            content,
            links,
            artifact,
        ):
            raise ValueError("conflicting retry")
        return previous
    stamp = utc(db.clock())
    db.execute(
        "INSERT INTO records (id, kind, project, created_at, payload, artifact) VALUES (?, ?, ?, ?, ?, ?)",
        (key, kind, project, stamp, content.decode(), artifact),
    )
    db.executemany("INSERT INTO dependencies VALUES (?, ?)", ((key, link) for link in links))
    journal(
        db, task or key, EventKind.RESEARCH_RECORDED, RunState.COMPLETED, {"id": key, "kind": kind}
    )
    return Record(key, kind, project, stamp, data, links, artifact)


def journal(
    db: CatalogueConnection, task: str, kind: EventKind, state: RunState, data: Object
) -> None:
    sequence = int(db.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM journal").fetchone()[0])
    event = RunEvent(sequence, task, task, kind, state, db.clock(), data)
    db.execute("INSERT INTO journal VALUES (?, ?)", (sequence, encode(event.to_record()).decode()))


def validate_dataset(output: bytes, content: bytes, window: PriceWindow) -> tuple[bytes, Object]:
    """Validate the worker response before accepting a release or a score."""
    try:
        document = object_value(json.loads(output))
        rows = document["rows"]
        expected = int((window.end - window.start).total_seconds() // 300)
        if (
            set(document) != {"rows", "negative"}
            or not isinstance(rows, list)
            or len(rows) != expected
            or type(document["negative"]) is not int
        ):
            raise ValueError
        source_lines = content.split(b"\n")
        negatives = 0
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError
            interval, price, line = row
            if (
                not isinstance(interval, str)
                or type(price) is not int
                or type(line) is not int
                or not 2 <= line <= len(source_lines)
                or interval
                != (window.start.astimezone(UTC) + index * INTERVAL)
                .isoformat()
                .replace("+00:00", "Z")
            ):
                raise ValueError
            label, region, raw_price = source_lines[line - 1].decode("ascii").split(",")
            if not _PRICE.fullmatch(raw_price):
                raise ValueError
            price_micro = int(Decimal(raw_price) * 1_000_000)
            if (label, region, price_micro) != (interval, window.region, price):
                raise ValueError
            negatives += price < 0
        if document["negative"] != negatives or output != encode(document) + b"\n":
            raise ValueError
    except (ValueError, KeyError, RecursionError) as error:
        raise AnalysisError("invalid_analysis_output") from error
    return encode({"rows": rows}), {
        "intervals": expected,
        "negative": negatives,
        "completeness": "complete",
    }
