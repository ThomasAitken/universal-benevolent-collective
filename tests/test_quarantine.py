"""C04 acceptance using a local clamd protocol fixture and disposable stores."""

import hashlib
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from tests.clamd_fixture import clamd_fixture as clamd_fixture
from tests.clamd_fixture import read_scan, scanner_for
from ubc_node import quarantine
from ubc_node.cli import run
from ubc_node.quarantine import (
    ClamAVScanner,
    DownloadQuarantine,
    DownloadRejected,
    DownloadScope,
    load_canaries,
)

_HEADER = b"interval_utc,region,price\n"
_CLEAN = _HEADER + b"2026-01-01T00:00:00Z,NSW1,-12.3\n"
_SCOPE = DownloadScope("prices", "baseline", "session-1", "https://data.example/prices.csv")
_CANARY = b"tripwire"


def _disguised_archive() -> bytes:
    content = BytesIO()
    with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("repeated.txt", b"x" * (32 * 1024))
    return content.getvalue()


def _events(store: DownloadQuarantine) -> list[tuple[str, str | None]]:
    with closing(sqlite3.connect(store.root / "downloads.sqlite3")) as db:
        return cast(
            list[tuple[str, str | None]],
            db.execute("SELECT state, decision FROM events ORDER BY sequence").fetchall(),
        )


def _ingest(
    store: DownloadQuarantine,
    source: Path,
    scanner: ClamAVScanner,
    *,
    content: bytes = _CLEAN,
    filename: str = "prices.csv",
    scope: DownloadScope = _SCOPE,
    max_bytes: int = quarantine.MAX_DOWNLOAD_BYTES,
) -> quarantine.DownloadRelease:
    return store.ingest(
        scope,
        source,
        filename,
        scanner=scanner,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        canaries=(_CANARY,),
        max_bytes=max_bytes,
    )


def test_clean_release_is_scoped_deduplicated_and_audited(
    tmp_path: Path, clamd: tuple[Path, socket.socket]
) -> None:
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    store = DownloadQuarantine(tmp_path / "store")
    with scanner_for(clamd, b"stream: ", b"OK\0", expected=_CLEAN) as scanner:
        receipt = _ingest(store, source, scanner, max_bytes=len(_CLEAN))
    with scanner_for(clamd, b"stream: OK\0", expected=_CLEAN) as scanner:
        other = _ingest(
            store,
            source,
            scanner,
            scope=DownloadScope("another-project", "review", "session-2", _SCOPE.source),
        )
    assert receipt.sha256 == other.sha256 == hashlib.sha256(_CLEAN).hexdigest()
    assert receipt.receipt != other.receipt
    assert receipt.size == len(_CLEAN)
    assert store.read_release(_SCOPE.project, receipt.receipt) == _CLEAN
    with pytest.raises(KeyError):
        store.read_release("another-project", receipt.receipt)
    assert store.read_release("another-project", other.receipt) == _CLEAN
    assert list((store.root / "pending").iterdir()) == []
    assert (store.root / "pending").lstat().st_mode & 0o077 == 0
    expected = [
        ("receiving", None),
        ("quarantined", None),
        ("scanning", "csv-v1,secret-canary,clamd"),
        ("released", "all_checks_passed"),
        ("cleaned", None),
    ]
    store.recover()
    assert _events(store) == expected * 2
    record = store.inspect_acquisition(_SCOPE.project, receipt.receipt)
    assert record.scope == _SCOPE
    assert record.filename == "prices.csv"
    assert record.size == len(_CLEAN)
    assert record.sha256 == receipt.sha256
    assert record.expected_sha256 == receipt.sha256
    assert record.released is True
    assert record.cleaned is True
    assert [(event.state, event.decision) for event in record.events] == expected
    with pytest.raises(KeyError):
        store.inspect_acquisition("another-project", receipt.receipt)
    with closing(sqlite3.connect(store.root / "downloads.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM artifacts").fetchone() == (1,)


@pytest.mark.parametrize(
    ("content", "filename", "limit", "reason"),
    [
        (_CLEAN, "../prices.csv", 1000, "filename_policy"),
        (_CLEAN, "prices.zip", 1000, "filename_policy"),
        (_CLEAN, "prices.csv", len(_CLEAN) - 1, "oversize"),
        (_disguised_archive(), "prices.csv", 1000, "csv_policy"),
        (b"\x7fELF", "prices.csv", 1000, "csv_policy"),
        (_HEADER, "prices.csv", 1000, "csv_policy"),
        (_HEADER + b"2026-02-31T00:00:00Z,NSW1,1\n", "prices.csv", 1000, "csv_policy"),
        (_HEADER + b"2026-01-01T00:00:00Z,NSW1,=1+1\n", "prices.csv", 1000, "csv_policy"),
        (_HEADER + b"2026-01-01T00:00:00Z,NSW1,Alice\n", "prices.csv", 1000, "csv_policy"),
        (_CLEAN + _CANARY, "prices.csv", 1000, "secret_canary"),
    ],
)
def test_rejects_before_calling_scanner(
    tmp_path: Path, content: bytes, filename: str, limit: int, reason: str
) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    source = tmp_path / "download"
    source.write_bytes(content)
    with pytest.raises(DownloadRejected, match=reason):
        _ingest(
            store,
            source,
            ClamAVScanner(Path("unconfigured")),
            content=content,
            filename=filename,
            max_bytes=limit,
        )
    _assert_unreleased(store, reason)
    assert _CANARY not in (store.root / "downloads.sqlite3").read_bytes()


def _assert_unreleased(store: DownloadQuarantine, reason: str) -> None:
    events = _events(store)
    assert events[-2:] == [("rejected", reason), ("cleaned", None)]
    assert ("released", "all_checks_passed") not in events
    with closing(sqlite3.connect(store.root / "downloads.sqlite3")) as db:
        assert db.execute("SELECT count(*) FROM artifacts").fetchone() == (0,)
    assert not list(store.root.glob("pending/*"))


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        ((b"stream: Test.Signature FOUND\0",), "malware"),
        ((b"INSTREAM size limit exceeded. ERROR\0",), "scanner_failed"),
        ((b"stream: OK",), "scanner_failed"),
        ((b"x" * 1024,), "scanner_failed"),
    ],
)
def test_scanner_rejects_malware_and_requires_complete_clean_result(
    tmp_path: Path, clamd: tuple[Path, socket.socket], replies: tuple[bytes, ...], reason: str
) -> None:
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    store = DownloadQuarantine(tmp_path / "store")
    with (
        scanner_for(clamd, *replies, expected=_CLEAN) as scanner,
        pytest.raises(DownloadRejected, match=reason),
    ):
        _ingest(store, source, scanner)
    _assert_unreleased(store, reason)


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("link", "input_io"),
        ("fifo", "not_regular"),
        ("missing", "input_io"),
        ("prefix", "digest_mismatch"),
        ("relative_socket", "scanner_unavailable"),
        ("missing_socket", "scanner_unavailable"),
    ],
)
def test_rejects_unsafe_inputs_and_missing_scanner(tmp_path: Path, kind: str, reason: str) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    source = tmp_path / "download"
    if kind == "link":
        original = tmp_path / "original"
        original.write_bytes(_CLEAN)
        source.symlink_to(original)
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind != "missing":
        source.write_bytes(_CLEAN)
    endpoint = Path("unconfigured") if kind == "relative_socket" else tmp_path / "absent"
    content = _CLEAN + b"2026-01-01T00:05:00Z,NSW1,0\n" if kind == "prefix" else _CLEAN
    with pytest.raises(DownloadRejected, match=reason):
        _ingest(store, source, ClamAVScanner(endpoint), content=content)
    _assert_unreleased(store, reason)


@pytest.mark.parametrize(
    ("max_bytes", "digest", "canaries", "reason"),
    [
        (0, "a" * 64, (_CANARY,), "byte limit"),
        (quarantine.MAX_DOWNLOAD_BYTES + 1, "a" * 64, (_CANARY,), "byte limit"),
        (1024, "unverified", (_CANARY,), "expected SHA"),
        (1024, "a" * 64, (), "secret canary"),
        (1024, "a" * 64, (b"",), "secret canary"),
    ],
)
def test_invalid_import_configuration_fails_before_receiving(
    tmp_path: Path, max_bytes: int, digest: str, canaries: tuple[bytes, ...], reason: str
) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    with pytest.raises(ValueError, match=reason):
        store.ingest(
            _SCOPE,
            tmp_path / "unused",
            "prices.csv",
            scanner=ClamAVScanner(Path("/unused")),
            expected_sha256=digest,
            canaries=canaries,
            max_bytes=max_bytes,
        )
    assert not store.root.exists()


@pytest.mark.parametrize(
    ("project", "source"),
    [
        ("../project", _SCOPE.source),
        ("project", "https://data.example/unescaped space"),
        ("project", "https://data.example/" + "x" * 2048),
        ("project", "https://data.example/path?credential"),
        ("project", "https://user:pw@data.example/prices.csv"),
    ],
)
def test_acquisition_scope_rejects_unsafe_provenance(project: str, source: str) -> None:
    with pytest.raises(ValueError):
        DownloadScope(project, _SCOPE.task, _SCOPE.session, source)


@pytest.mark.parametrize("kind", ["symlink", "public"])
def test_store_requires_private_directory(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "store"
    if kind == "symlink":
        root.symlink_to(tmp_path)
    else:
        root.mkdir(mode=0o755)
        root.chmod(0o755)
    with pytest.raises(ValueError, match="private directory"):
        DownloadQuarantine(root).recover()


def _cli_args(tmp_path: Path, endpoint: Path) -> list[str]:
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    canaries = tmp_path / "canaries"
    canaries.write_bytes(_CANARY + b"\n")
    canaries.chmod(0o600)
    return [
        "quarantine",
        "--store",
        str(tmp_path / "store"),
        "import",
        str(source),
        "--filename",
        "prices.csv",
        "--project",
        _SCOPE.project,
        "--task",
        _SCOPE.task,
        "--session",
        _SCOPE.session,
        "--source",
        _SCOPE.source,
        "--scanner-socket",
        str(endpoint),
        "--expected-sha256",
        hashlib.sha256(_CLEAN).hexdigest(),
        "--canaries-file",
        str(canaries),
    ]


def test_cli_import_and_restart(
    tmp_path: Path, clamd: tuple[Path, socket.socket], capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cli_args(tmp_path, clamd[0])
    with scanner_for(clamd, b"stream: OK\0", expected=_CLEAN):
        assert run(args) == 0
    assert f"sha256:{hashlib.sha256(_CLEAN).hexdigest()}" in capsys.readouterr().out
    assert run(["quarantine", "--store", str(tmp_path / "store"), "recover"]) == 0
    Path(args[-1]).write_bytes(b"x" * 65537)
    assert run(args) == 1
    assert "canary configuration exceeds" in capsys.readouterr().err


def test_cancelled_scan_expires_without_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel(_scanner: ClamAVScanner, _content: bytes) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ClamAVScanner, "scan", cancel)
    args = _cli_args(tmp_path, Path("/unused"))
    assert run(args) == 130
    store = DownloadQuarantine(tmp_path / "store")
    assert _events(store)[-2:] == [("expired", "interrupted"), ("cleaned", None)]
    assert list((store.root / "pending").iterdir()) == []


def test_input_changed_during_receipt_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    original_fstat = os.fstat
    seen_input = False

    def fstat(fd: int) -> os.stat_result:
        nonlocal seen_input
        info = original_fstat(fd)
        if info.st_ino == source.stat().st_ino:
            if seen_input:
                os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns - 1_000_000_000))
                return original_fstat(fd)
            seen_input = True
        return info

    monkeypatch.setattr(quarantine.os, "fstat", fstat)
    store = DownloadQuarantine(tmp_path / "store")
    with pytest.raises(DownloadRejected, match="incomplete_input"):
        _ingest(store, source, ClamAVScanner(Path("/unused")))
    _assert_unreleased(store, "incomplete_input")


def test_staging_corruption_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    receive = quarantine._receive  # pyright: ignore[reportPrivateUsage]

    def corrupt(incoming: Path, target: Path, max_bytes: int) -> tuple[str, int]:
        result = receive(incoming, target, max_bytes)
        target.write_bytes(_CLEAN[:-1])
        return result

    monkeypatch.setattr(quarantine, "_receive", corrupt)
    store = DownloadQuarantine(tmp_path / "store")
    with pytest.raises(DownloadRejected, match="staging_integrity"):
        _ingest(store, source, ClamAVScanner(Path("/unused")))
    _assert_unreleased(store, "staging_integrity")


def test_killed_import_stays_private_and_repeated_recovery_is_idempotent(
    tmp_path: Path, clamd: tuple[Path, socket.socket]
) -> None:
    endpoint, listener = clamd
    args = _cli_args(tmp_path, endpoint)
    store = DownloadQuarantine(tmp_path / "store")
    with subprocess.Popen([sys.executable, "-m", "ubc_node", *args]) as child:
        try:
            channel, _ = listener.accept()
            with channel:
                assert read_scan(channel) == _CLEAN
                child.kill()
                child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
    with closing(sqlite3.connect(store.root / "downloads.sqlite3")) as db:
        receipt = cast(str, db.execute("SELECT id FROM acquisitions").fetchone()[0])
    with pytest.raises(KeyError):
        store.read_release(_SCOPE.project, receipt)
    pending = store.root / "pending" / f"{_SCOPE.session}-{receipt}" / "input"
    assert pending.exists()
    assert _events(store)[-1][0] == "scanning"
    store.recover()
    store.recover()
    assert _events(store)[-2:] == [("expired", "interrupted"), ("cleaned", None)]
    assert not pending.exists()
    with pytest.raises(KeyError):
        store.read_release(_SCOPE.project, receipt)
    record = store.inspect_acquisition(_SCOPE.project, receipt)
    assert record.released is False
    assert record.cleaned is True
    assert [(event.state, event.decision) for event in record.events][-2:] == [
        ("expired", "interrupted"),
        ("cleaned", None),
    ]


def test_scanner_streams_large_csv_and_enforces_positive_deadline(
    tmp_path: Path, clamd: tuple[Path, socket.socket], monkeypatch: pytest.MonkeyPatch
) -> None:
    large = _HEADER + b"2026-01-01T00:00:00Z,NSW1,-12.30\n" * 2500
    assert len(large) > 65536
    source = tmp_path / "large.csv"
    source.write_bytes(large)
    store = DownloadQuarantine(tmp_path / "store")
    with scanner_for(clamd, b"stream: OK\0", expected=large) as scanner:
        release = _ingest(store, source, scanner, content=large)
    assert store.read_release(_SCOPE.project, release.receipt) == large

    ticks = iter([100.0, 200.0])
    monkeypatch.setattr(quarantine, "monotonic", lambda: next(ticks))
    with pytest.raises(DownloadRejected, match="scanner_unavailable"):
        ClamAVScanner(clamd[0]).scan(_CLEAN)


def test_load_canaries_requires_private_regular_file(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.write_bytes(b"secret-1\nsecret-2\n")
    private.chmod(0o600)
    assert load_canaries(private) == (b"secret-1", b"secret-2")

    public = tmp_path / "public"
    public.write_bytes(b"secret\n")
    public.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        load_canaries(public)

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError, match="private regular file"):
        load_canaries(fifo)

    link = tmp_path / "link"
    link.symlink_to(private)
    with pytest.raises(OSError):
        load_canaries(link)


def test_stray_session_directory_does_not_block_new_attempt(
    tmp_path: Path, clamd: tuple[Path, socket.socket]
) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    (store.root / "pending" / _SCOPE.session).mkdir(parents=True)
    store.root.chmod(0o700)
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    with scanner_for(clamd, b"stream: OK\0", expected=_CLEAN) as scanner:
        release = _ingest(store, source, scanner)
    assert store.read_release(_SCOPE.project, release.receipt) == _CLEAN


def test_reuse_release_matches_exact_released_scope_and_recovers_staging(
    tmp_path: Path, clamd: tuple[Path, socket.socket]
) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    source = tmp_path / "download"
    source.write_bytes(_CLEAN)
    digest = hashlib.sha256(_CLEAN).hexdigest()

    with scanner_for(clamd, b"stream: OK\0", expected=_CLEAN) as scanner:
        first = _ingest(store, source, scanner)

    # Default reuse_release=False scans again and creates a fresh acquisition.
    with scanner_for(clamd, b"stream: OK\0", expected=_CLEAN) as scanner:
        second = _ingest(store, source, scanner)
    assert second.receipt != first.receipt

    # reuse_release=True returns the first committed release without contacting clamd.
    reused = store.ingest(
        _SCOPE,
        source,
        "prices.csv",
        expected_sha256=digest,
        scanner=ClamAVScanner(Path("/unused")),
        canaries=(_CANARY,),
        reuse_release=True,
    )
    assert reused == first

    # Simulate an uncleaned-but-released row and verify _recover runs before reuse.
    pending = store.root / "pending" / f"{_SCOPE.session}-{first.receipt}"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "input").write_bytes(_CLEAN)
    with closing(sqlite3.connect(store.root / "downloads.sqlite3")) as db, db:
        db.execute("UPDATE acquisitions SET cleaned = 0 WHERE id = ?", (first.receipt,))
    reused_after_crash = store.ingest(
        _SCOPE,
        source,
        "prices.csv",
        expected_sha256=digest,
        scanner=ClamAVScanner(Path("/unused")),
        canaries=(_CANARY,),
        reuse_release=True,
    )
    assert reused_after_crash == first
    assert not pending.exists()
    assert store.inspect_acquisition(_SCOPE.project, first.receipt).cleaned is True

    # Mismatched session, filename, or max_bytes must not reuse the prior release.
    other_session = DownloadScope(_SCOPE.project, _SCOPE.task, "other-session", _SCOPE.source)
    with pytest.raises(DownloadRejected, match="scanner_unavailable"):
        store.ingest(
            other_session,
            source,
            "prices.csv",
            expected_sha256=digest,
            scanner=ClamAVScanner(Path("/unused")),
            canaries=(_CANARY,),
            reuse_release=True,
        )
    with pytest.raises(DownloadRejected, match="scanner_unavailable"):
        store.ingest(
            _SCOPE,
            source,
            "other.csv",
            expected_sha256=digest,
            scanner=ClamAVScanner(Path("/unused")),
            canaries=(_CANARY,),
            reuse_release=True,
        )
    with pytest.raises(DownloadRejected, match="oversize"):
        store.ingest(
            _SCOPE,
            source,
            "prices.csv",
            expected_sha256=digest,
            scanner=ClamAVScanner(Path("/unused")),
            canaries=(_CANARY,),
            max_bytes=len(_CLEAN) - 1,
            reuse_release=True,
        )

    # A rejected acquisition in a new session must not be reused.
    with pytest.raises(DownloadRejected, match="scanner_unavailable"):
        store.ingest(
            other_session,
            source,
            "prices.csv",
            expected_sha256=digest,
            scanner=ClamAVScanner(Path("/unused")),
            canaries=(_CANARY,),
            reuse_release=True,
        )
