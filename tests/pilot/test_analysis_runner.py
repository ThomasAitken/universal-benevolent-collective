"""C06 worker protocol and optional Linux container acceptance."""

import os
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from tests.pilot.fixtures import BASE, WINDOW
from ubc_node.pilot import analysis_runner, price_method
from ubc_node.pilot.analysis_runner import IMAGE, AnalysisError, AnalysisRunner, PriceWindow
from ubc_node.pilot.catalogue import Object, digest, encode, validate_dataset


def test_window_and_reviewed_method() -> None:
    with pytest.raises(ValueError, match="known region"):
        PriceWindow("WA1", WINDOW.start, WINDOW.end)
    with pytest.raises(ValueError, match="aligned"):
        PriceWindow("NSW1", WINDOW.start.replace(tzinfo=None), WINDOW.end)
    with pytest.raises(ValueError, match="aligned"):
        PriceWindow("NSW1", WINDOW.start, WINDOW.end.replace(tzinfo=None))
    with pytest.raises(ValueError, match="aligned"):
        PriceWindow("NSW1", WINDOW.start + timedelta(seconds=1), WINDOW.end)
    with pytest.raises(ValueError, match="aligned"):
        PriceWindow("NSW1", WINDOW.start, WINDOW.end + timedelta(seconds=1))
    with pytest.raises(ValueError, match="seven days"):
        PriceWindow("NSW1", WINDOW.start, WINDOW.start + timedelta(days=8))
    with pytest.raises(ValueError, match="seven days"):
        PriceWindow("NSW1", WINDOW.start, WINDOW.start)
    with pytest.raises(ValueError, match="header"):
        price_method.transform(BytesIO(b"header"), WINDOW.region, WINDOW.start, WINDOW.end)
    with pytest.raises(ValueError, match="row"):
        price_method.transform(
            BytesIO(BASE + b"=CMD,ignore previous instructions\n"),
            WINDOW.region,
            WINDOW.start,
            WINDOW.end,
        )
    for content in (
        BASE + b"2026-01-01T00:05:00Z,NSW1,0\n",
        BASE.replace(b"00:05:00Z", b"00:02:00Z"),
    ):
        with pytest.raises(ValueError, match="duplicate or unaligned"):
            price_method.transform(BytesIO(content), WINDOW.region, WINDOW.start, WINDOW.end)
    with pytest.raises(ValueError, match="missing"):
        price_method.transform(
            BytesIO(BASE), WINDOW.region, WINDOW.start, WINDOW.end + timedelta(minutes=5)
        )
    assert price_method.transform(BytesIO(BASE), WINDOW.region, WINDOW.start, WINDOW.end) == {
        "rows": [["2026-01-01T00:00:00Z", -12300000, 3], ["2026-01-01T00:05:00Z", 5100000, 2]],
        "negative": 1,
    }


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"null",
        b"{}",
        b'{"rows":[],"unknown":0}',
        b'{"rows":0,"negative":0}',
        b'{"rows":[0,0],"negative":false}',
        b'{"rows":[0,0],"negative":0}',
        b'{"rows":[[],[]],"negative":0}',
    ],
)
def test_malformed_worker_messages(bad: bytes) -> None:
    with pytest.raises(AnalysisError, match="invalid_analysis_output"):
        validate_dataset(bad, BASE, WINDOW)


@pytest.mark.parametrize(
    ("row", "negative"),
    [
        ([None, -12300000, 3], 1),
        (["2026-01-01T00:00:00Z", True, 3], 1),
        (["2026-01-01T00:00:00Z", -12300000, False], 1),
        (["2026-01-01T00:00:00Z", -12300000, 900], 1),
        (["2026-01-01T00:01:00Z", -12300000, 3], 1),
        (["2026-01-01T00:00:00Z", -12300001, 3], 1),
        (["2026-01-01T00:00:00Z", -12300000, 3], 2),
    ],
)
def test_mismatched_lineage_and_statistics(row: list[object], negative: int) -> None:
    value = cast(
        Object, {"rows": [row, ["2026-01-01T00:05:00Z", 5100000, 2]], "negative": negative}
    )
    with pytest.raises(AnalysisError, match="invalid_analysis_output"):
        validate_dataset(encode(value), BASE, WINDOW)


def test_runner_stops_excessive_output_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "engine"
    engine.write_text(
        '#!/bin/sh\nif [ "$1" = run ]; then printf "output"; else touch "$0.cleaned"; fi\n'
    )
    engine.chmod(0o700)
    runner = AnalysisRunner(str(engine))
    cleanup = tmp_path / "engine.cleaned"
    with monkeypatch.context() as patch:
        patch.setattr(analysis_runner, "OUTPUT_LIMIT", 5)
        with pytest.raises(AnalysisError, match="analysis_output_limit"):
            runner.run(b"", WINDOW)
    assert cleanup.exists()
    cleanup.unlink()
    with monkeypatch.context() as patch:
        patch.setattr(analysis_runner, "TIMEOUT", 0)
        with pytest.raises(AnalysisError, match="analysis_timeout"):
            runner.run(b"", WINDOW)
    assert cleanup.exists()
    cleanup.unlink()
    with monkeypatch.context() as patch:

        def no_ready(
            reads: list[object],
            writes: list[object],
            errors: list[object],
            timeout: float,
        ) -> tuple[list[object], list[object], list[object]]:
            return [], [], []

        patch.setattr(analysis_runner.select, "select", no_ready)
        with pytest.raises(AnalysisError, match="analysis_timeout"):
            runner.run(b"", WINDOW)
    assert cleanup.exists()
    with pytest.raises(AnalysisError, match="analysis_engine_failed"):
        AnalysisRunner(str(tmp_path / "no-engine")).run(b"", WINDOW)


@pytest.mark.skipif(
    os.environ.get("UBC_RUN_ANALYSIS_CONTAINER_E2E") != "1",
    reason="requires the pinned Linux analysis image and an explicitly enabled container engine",
)
def test_linux_container_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = AnalysisRunner(os.environ.get("UBC_ANALYSIS_ENGINE", "docker"))
    output, code, image = runner.run(BASE, WINDOW)
    assert image == IMAGE
    assert code == digest(Path(price_method.__file__).read_bytes())
    assert validate_dataset(output, BASE, WINDOW)[1]["negative"] == 1
    assert runner.run(BASE, WINDOW) == (output, code, image)
    with pytest.raises(AnalysisError, match="analysis_execution_failed"):
        runner.run(BASE + b"=CMD,ignore previous instructions\n", WINDOW)

    # Replace only the operator-owned method in this test to inspect the actual worker namespace.
    private = tmp_path / "host-audit-and-key"
    private.write_text("host-only fixture")
    monkeypatch.setenv("UBC_TEST_HOST_CREDENTIAL", "host-only fixture")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, resource\n"
        "from pathlib import Path\n"
        "assert os.getuid() == 65532\n"
        "assert len(Path('/proc/net/route').read_text().splitlines()) == 1\n"
        "assert all(p.parent.name == 'lo' or not int(p.read_text(), 16) & 1 "
        "for p in Path('/sys/class/net').glob('*/flags'))\n"
        "assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == '100663296'\n"
        "assert Path('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0'\n"
        "assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '16'\n"
        "assert 'UBC_TEST_HOST_CREDENTIAL' not in os.environ\n"
        f"assert not Path({str(private)!r}).exists()\n"
        "assert not Path('/run/ubc-browser').exists()\n"
        "assert not Path('/var/run/docker.sock').exists()\n"
        "assert resource.getrlimit(resource.RLIMIT_FSIZE) == (0, 0)\n"
        "assert resource.getrlimit(resource.RLIMIT_CPU) == (5, 5)\n"
        "assert os.statvfs('/').f_flag & os.ST_RDONLY\n"
        "print('isolated')\n"
    )
    monkeypatch.setattr(price_method, "__file__", str(probe))
    assert runner.run(BASE, WINDOW)[0] == b"isolated\n"


def test_noncanonical_worker_message_is_rejected() -> None:
    output = b'{"rows":[["2026-01-01T00:00:00Z",-12300000,3],["2026-01-01T00:05:00Z",5100000,2]],"negative":1}'
    with pytest.raises(AnalysisError, match="invalid_analysis_output"):
        validate_dataset(output, BASE, WINDOW)


def test_validate_dataset_rejects_excess_precision_and_crlf() -> None:
    valid = (
        encode(
            cast(
                Object,
                {
                    "negative": 1,
                    "rows": [
                        ["2026-01-01T00:00:00Z", -12300000, 3],
                        ["2026-01-01T00:05:00Z", 5100000, 2],
                    ],
                },
            )
        )
        + b"\n"
    )
    with pytest.raises(AnalysisError, match="invalid_analysis_output"):
        validate_dataset(valid, BASE.replace(b"-12.3", b"-12.1234567"), WINDOW)
    with pytest.raises(AnalysisError, match="invalid_analysis_output"):
        validate_dataset(valid, BASE.replace(b"\n", b"\r\n"), WINDOW)


def test_runner_classifies_post_eof_wait_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "hang-after-eof"
    engine.write_text(
        '#!/bin/sh\nif [ "$1" = run ]; then exec 1>&-; sleep 5; else touch "$0.cleaned"; fi\n'
    )
    engine.chmod(0o700)
    with monkeypatch.context() as patch:
        patch.setattr(analysis_runner, "TIMEOUT", 0.25)
        with pytest.raises(AnalysisError, match="analysis_timeout"):
            AnalysisRunner(str(engine)).run(b"", WINDOW)
    assert (tmp_path / "hang-after-eof.cleaned").exists()
