"""Append-only JSON Lines event storage for Phase 0."""

import json
import os
from pathlib import Path
from threading import Lock
from typing import cast

from ubc_node.core.contracts import JsonValue, RunEvent


class EventLogError(ValueError):
    """Raised when an event log cannot be decoded safely."""


class JsonlEventLog:
    """A small, inspectable, append-only event sink."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def append(self, event: RunEvent) -> None:
        """Append and fsync one canonical JSON record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            event.to_record(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{line}\n")
            stream.flush()
            os.fsync(stream.fileno())

    def read_all(self) -> tuple[RunEvent, ...]:
        """Read all complete records, rejecting corrupt or non-object lines."""
        if not self.path.exists():
            return ()

        events: list[RunEvent] = []
        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise TypeError("record is not an object")
                    events.append(RunEvent.from_record(cast(dict[str, JsonValue], record)))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise EventLogError(
                        f"invalid event record at {self.path}:{line_number}"
                    ) from error
        return tuple(events)
