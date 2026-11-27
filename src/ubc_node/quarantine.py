"""Trusted, operator-mediated acquisition of electricity price CSVs."""

import fcntl
import hashlib
import os
import re
import shutil
import socket
import sqlite3
import stat
import struct
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import uuid4

from ubc_node.browser_contracts import origin_for_url

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_SCAN_TIMEOUT = 60
_SCAN_REPLY_BYTES = 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,122}\.csv")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_HEADER = b"interval_utc,region,price\n"
_ROW = re.compile(
    rb"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z),"
    rb"(?:NSW1|QLD1|SA1|TAS1|VIC1),-?[0-9]{1,6}(?:\.[0-9]{1,6})?\n"
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    digest TEXT PRIMARY KEY,
    content BLOB NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS acquisitions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    task TEXT NOT NULL,
    session TEXT NOT NULL,
    source TEXT NOT NULL,
    expected_digest TEXT NOT NULL,
    filename TEXT,
    size INTEGER,
    digest TEXT,
    release_digest TEXT REFERENCES artifacts(digest),
    cleaned INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY,
    acquisition TEXT NOT NULL REFERENCES acquisitions(id),
    occurred_at TEXT NOT NULL,
    state TEXT NOT NULL,
    decision TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS events_by_acquisition ON events (acquisition, sequence);
"""


class DownloadRejected(ValueError):
    """A fixed policy decision suitable for the trusted acquisition journal."""


@dataclass(frozen=True, slots=True)
class DownloadScope:
    project: str
    task: str
    session: str
    source: str

    def __post_init__(self) -> None:
        if not all(
            _IDENTIFIER.fullmatch(value) for value in (self.project, self.task, self.session)
        ):
            raise ValueError("invalid project, task or session identifier")
        if len(self.source) > 2048 or not re.fullmatch(r"[!-~]+", self.source):
            raise ValueError("invalid source locator")
        if any(character in self.source for character in "?#\\"):
            raise ValueError("source locator must omit query, fragment and backslash")
        origin_for_url(self.source)


@dataclass(frozen=True, slots=True)
class DownloadRelease:
    receipt: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class QuarantineEvent:
    sequence: int
    occurred_at: str
    state: str
    decision: str | None


@dataclass(frozen=True, slots=True)
class AcquisitionRecord:
    receipt: str
    scope: DownloadScope
    expected_sha256: str
    filename: str | None
    size: int | None
    sha256: str | None
    released: bool
    cleaned: bool
    events: tuple[QuarantineEvent, ...]


def load_canaries(path: Path) -> tuple[bytes, ...]:
    """Load private secret canary literals from an operator-owned regular file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("canary file must be a private regular file owned by the operator")
        with os.fdopen(fd, "rb", closefd=False) as canary_file:
            config = canary_file.read(65537)
    finally:
        os.close(fd)
    if len(config) > 65536:
        raise ValueError("canary configuration exceeds 64 KiB")
    return tuple(config.splitlines())


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise DownloadRejected("scanner_unavailable")
    return remaining


@dataclass(frozen=True, slots=True)
class ClamAVScanner:
    endpoint: Path

    def scan(self, content: bytes) -> None:
        """Require a clean clamd verdict over a private local INSTREAM channel."""
        if not self.endpoint.is_absolute():
            raise DownloadRejected("scanner_unavailable")
        deadline = monotonic() + _SCAN_TIMEOUT
        reply = bytearray()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(_remaining_timeout(deadline))
                channel.connect(os.fspath(self.endpoint))
                channel.settimeout(_remaining_timeout(deadline))
                channel.sendall(b"zINSTREAM\0")
                view = memoryview(content)
                for offset in range(0, len(view), _CHUNK_BYTES):
                    chunk = view[offset : offset + _CHUNK_BYTES]
                    channel.settimeout(_remaining_timeout(deadline))
                    channel.sendall(struct.pack("!I", len(chunk)))
                    channel.sendall(chunk)
                channel.settimeout(_remaining_timeout(deadline))
                channel.sendall(bytes(4))
                while len(reply) < _SCAN_REPLY_BYTES and not reply.endswith(b"\0"):
                    channel.settimeout(_remaining_timeout(deadline))
                    block = channel.recv(_SCAN_REPLY_BYTES - len(reply))
                    if not block:
                        break
                    reply.extend(block)
        except OSError as error:
            raise DownloadRejected("scanner_unavailable") from error
        if re.fullmatch(rb"stream: [!-~]+ FOUND\0", reply):
            raise DownloadRejected("malware")
        if bytes(reply) != b"stream: OK\0":
            raise DownloadRejected("scanner_failed")


class DownloadQuarantine:
    """An exclusive local store whose release and audit commit is transactional."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    @contextmanager
    def _database(self) -> Generator[sqlite3.Connection]:
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("quarantine root must be a private directory owned by the operator")
        with (self.root / "lock").open("ab") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with closing(sqlite3.connect(self.root / "downloads.sqlite3")) as db:
                db.execute("PRAGMA foreign_keys = ON")
                db.execute("PRAGMA synchronous = FULL")
                db.executescript(_SCHEMA)
                yield db

    def _staging(self, session: str, receipt: str) -> Path:
        return self.root / "pending" / f"{session}-{receipt}"

    def recover(self) -> None:
        """Expire and clean abandoned attempts, leaving committed releases readable."""
        with self._database() as db:
            self._recover(db)

    def _recover(self, db: sqlite3.Connection) -> None:
        abandoned = db.execute("SELECT id, session FROM acquisitions WHERE cleaned = 0").fetchall()
        for receipt, session in cast(list[tuple[str, str]], abandoned):
            self._cleanup(db, receipt, session)

    def _cleanup(self, db: sqlite3.Connection, receipt: str, session: str) -> None:
        staging = self._staging(session, receipt)
        try:
            shutil.rmtree(staging)
        except FileNotFoundError:
            pass
        state = db.execute(
            "SELECT state FROM events WHERE acquisition = ? ORDER BY sequence DESC LIMIT 1",
            (receipt,),
        ).fetchone()[0]
        with db:
            if state in {"receiving", "quarantined", "scanning"}:
                _event(db, receipt, "expired", "interrupted")
            _event(db, receipt, "cleaned")
            db.execute("UPDATE acquisitions SET cleaned = 1 WHERE id = ?", (receipt,))

    def ingest(
        self,
        scope: DownloadScope,
        incoming: Path,
        filename: str,
        *,
        expected_sha256: str,
        scanner: ClamAVScanner,
        canaries: tuple[bytes, ...],
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> DownloadRelease:
        """Import a completed, operator-held file into private per-session staging."""
        if not 1 <= max_bytes <= MAX_DOWNLOAD_BYTES:
            raise ValueError("invalid download byte limit")
        if not _SHA256.fullmatch(expected_sha256):
            raise ValueError("expected SHA-256 must be 64 lowercase hexadecimal characters")
        if not 1 <= len(canaries) <= 32 or not all(1 <= len(value) <= 256 for value in canaries):
            raise ValueError("provide 1 to 32 secret canary values of 1 to 256 bytes each")
        receipt = uuid4().hex
        staging = self._staging(scope.session, receipt)
        with self._database() as db:
            self._recover(db)
            with db:
                db.execute(
                    "INSERT INTO acquisitions (id, project, task, session, source, "
                    "expected_digest) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        receipt,
                        scope.project,
                        scope.task,
                        scope.session,
                        scope.source,
                        expected_sha256,
                    ),
                )
                _event(db, receipt, "receiving")
            try:
                if not _FILENAME.fullmatch(filename):
                    raise DownloadRejected("filename_policy")
                staging.parent.mkdir(mode=0o700, exist_ok=True)
                staging.mkdir(mode=0o700)
                digest, size = _receive(incoming, staging / "input", max_bytes)
                with db:
                    db.execute(
                        "UPDATE acquisitions SET filename = ?, size = ?, digest = ? WHERE id = ?",
                        (filename, size, digest, receipt),
                    )
                    _event(db, receipt, "quarantined")
                if digest != expected_sha256:
                    raise DownloadRejected("digest_mismatch")
                with db:
                    _event(db, receipt, "scanning", "csv-v1,secret-canary,clamd")
                with (staging / "input").open("rb") as pending:
                    content = pending.read(size + 1)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise DownloadRejected("staging_integrity")
                _validate_content(content, canaries)
                scanner.scan(content)
                with db:
                    db.execute(
                        "INSERT INTO artifacts (digest, content) VALUES (?, ?) "
                        "ON CONFLICT(digest) DO NOTHING",
                        (digest, content),
                    )
                    db.execute(
                        "UPDATE acquisitions SET release_digest = ? WHERE id = ?", (digest, receipt)
                    )
                    _event(db, receipt, "released", "all_checks_passed")
                return DownloadRelease(receipt, digest, size)
            except DownloadRejected as error:
                with db:
                    _event(db, receipt, "rejected", str(error))
                raise
            finally:
                self._cleanup(db, receipt, scope.session)

    def read_release(self, project: str, receipt: str) -> bytes:
        """Read the content authorised by a committed receipt for this project."""
        with self._database() as db:
            row = db.execute(
                "SELECT content FROM acquisitions JOIN artifacts ON release_digest = artifacts.digest "
                "WHERE project = ? AND id = ?",
                (project, receipt),
            ).fetchone()
        if row is None:
            raise KeyError("unknown release for this project")
        return cast(bytes, row[0])

    def inspect_acquisition(self, project: str, receipt: str) -> AcquisitionRecord:
        """Return typed provenance and audit events for one project-scoped receipt."""
        with self._database() as db:
            row = db.execute(
                "SELECT id, project, task, session, source, expected_digest, filename, size, "
                "digest, release_digest, cleaned FROM acquisitions WHERE project = ? AND id = ?",
                (project, receipt),
            ).fetchone()
            if row is None:
                raise KeyError("unknown acquisition for this project")
            events = db.execute(
                "SELECT sequence, occurred_at, state, decision FROM events "
                "WHERE acquisition = ? ORDER BY sequence",
                (receipt,),
            ).fetchall()
        return AcquisitionRecord(
            receipt=cast(str, row[0]),
            scope=DownloadScope(
                cast(str, row[1]), cast(str, row[2]), cast(str, row[3]), cast(str, row[4])
            ),
            expected_sha256=cast(str, row[5]),
            filename=cast(str | None, row[6]),
            size=cast(int | None, row[7]),
            sha256=cast(str | None, row[8]),
            released=row[9] is not None,
            cleaned=bool(row[10]),
            events=tuple(
                QuarantineEvent(
                    sequence=cast(int, event[0]),
                    occurred_at=cast(str, event[1]),
                    state=cast(str, event[2]),
                    decision=cast(str | None, event[3]),
                )
                for event in events
            ),
        )


def _event(db: sqlite3.Connection, receipt: str, state: str, decision: str | None = None) -> None:
    db.execute(
        "INSERT INTO events (acquisition, occurred_at, state, decision) VALUES (?, ?, ?, ?)",
        (receipt, datetime.now(UTC).isoformat(), state, decision),
    )


def _receive(incoming: Path, target: Path, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        fd = os.open(incoming, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise DownloadRejected("not_regular")
            with os.fdopen(fd, "rb", closefd=False) as source, target.open("xb") as pending:
                while block := source.read(min(_CHUNK_BYTES, max_bytes - size + 1)):
                    size += len(block)
                    if size > max_bytes:
                        raise DownloadRejected("oversize")
                    digest.update(block)
                    pending.write(block)
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise DownloadRejected("incomplete_input")
        finally:
            os.close(fd)
    except OSError as error:
        raise DownloadRejected("input_io") from error
    return digest.hexdigest(), size


def _validate_content(content: bytes, canaries: tuple[bytes, ...]) -> None:
    if any(canary in content for canary in canaries):
        raise DownloadRejected("secret_canary")
    rows = BytesIO(content)
    if rows.readline(len(_HEADER)) != _HEADER or rows.tell() == len(content):
        raise DownloadRejected("csv_policy")
    for row in rows:
        match = _ROW.fullmatch(row)
        if match is None:
            raise DownloadRejected("csv_policy")
        try:
            datetime.fromisoformat(match[1].decode("ascii"))
        except ValueError as error:
            raise DownloadRejected("csv_policy") from error
