"""Opt-in C04 deployment gate against a real local ClamAV daemon socket."""

import hashlib
import os
from pathlib import Path

import pytest

from ubc_node.artifacts.quarantine import (
    ClamAVScanner,
    DownloadQuarantine,
    DownloadRejected,
    DownloadScope,
)

pytestmark = [
    pytest.mark.quarantine_scanner,
    pytest.mark.skipif(
        os.environ.get("UBC_RUN_QUARANTINE_SCANNER_E2E") != "1",
        reason="requires an explicitly enabled local ClamAV daemon socket",
    ),
]

_CLEAN = b"interval_utc,region,price\n2026-01-01T00:00:00Z,NSW1,-12.30\n"
_SCOPE = DownloadScope("electricity", "deployment-gate", "clamd-e2e", "https://data.example/p.csv")


def _scanner_socket() -> Path:
    raw = os.environ.get("UBC_CLAMD_SOCKET")
    if not raw:
        pytest.fail("set UBC_CLAMD_SOCKET to the operator-managed clamd Unix socket")
    return Path(raw)


def test_real_clamd_releases_clean_csv_and_rejects_stopped_scanner(tmp_path: Path) -> None:
    store = DownloadQuarantine(tmp_path / "store")
    source = tmp_path / "clean.csv"
    source.write_bytes(_CLEAN)
    digest = hashlib.sha256(_CLEAN).hexdigest()

    release = store.ingest(
        _SCOPE,
        source,
        "prices.csv",
        expected_sha256=digest,
        scanner=ClamAVScanner(_scanner_socket()),
        canaries=(b"tripwire",),
    )

    assert release.sha256 == digest
    assert store.read_release(_SCOPE.project, release.receipt) == _CLEAN
    record = store.inspect_acquisition(_SCOPE.project, release.receipt)
    assert record.released is True
    assert record.cleaned is True
    assert record.events[-2].state == "released"

    with pytest.raises(DownloadRejected, match="scanner_unavailable"):
        store.ingest(
            _SCOPE,
            source,
            "prices.csv",
            expected_sha256=digest,
            scanner=ClamAVScanner(tmp_path / "stopped.ctl"),
            canaries=(b"tripwire",),
        )


def test_real_clamd_rejects_flagged_benign_csv_fixture(tmp_path: Path) -> None:
    flagged_env = os.environ.get("UBC_CLAMD_FLAGGED_CSV")
    if not flagged_env:
        pytest.skip("set UBC_CLAMD_FLAGGED_CSV to a valid CSV matched by a fixture signature")
    flagged = Path(flagged_env)
    content = flagged.read_bytes()
    store = DownloadQuarantine(tmp_path / "store")

    with pytest.raises(DownloadRejected, match="malware"):
        store.ingest(
            _SCOPE,
            flagged,
            "prices.csv",
            expected_sha256=hashlib.sha256(content).hexdigest(),
            scanner=ClamAVScanner(_scanner_socket()),
            canaries=(b"tripwire",),
        )
