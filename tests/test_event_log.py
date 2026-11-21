"""Tests for the append-only JSONL event log."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ubc_node.contracts import EventKind, RunEvent, RunState
from ubc_node.event_log import EventLogError, JsonlEventLog


def _event(sequence: int) -> RunEvent:
    return RunEvent(
        sequence=sequence,
        run_id="run-1",
        task_id="task-1",
        kind=EventKind.RUN_CREATED,
        state=RunState.CREATED,
        occurred_at=datetime(2026, 11, 21, tzinfo=UTC),
        payload={"sequence_copy": sequence},
    )


def test_missing_log_reads_as_empty(tmp_path: Path) -> None:
    assert JsonlEventLog(tmp_path / "missing.jsonl").read_all() == ()


def test_events_are_appended_as_canonical_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "run-1.jsonl"
    log = JsonlEventLog(path)
    log.append(_event(1))
    log.append(_event(2))

    assert log.read_all() == (_event(1), _event(2))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["sequence"] == 1
    assert lines[0].startswith('{"kind"')


@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        "[]\n",
        '{"payload": {}}\n',
    ],
)
def test_corrupt_event_record_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "corrupt.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(EventLogError, match=r"corrupt\.jsonl:1"):
        JsonlEventLog(path).read_all()
