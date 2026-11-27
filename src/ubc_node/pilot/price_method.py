"""Reviewed C06 electricity-price transform. Runs as a standalone, stdlib-only program."""

import json
import re
import signal
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import BinaryIO, TypedDict

HEADER = b"interval_utc,region,price\n"
ROW = re.compile(
    rb"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z),"
    rb"(NSW1|QLD1|SA1|TAS1|VIC1),(-?[0-9]{1,6}(?:\.[0-9]{1,6})?)\n"
)
INTERVAL = timedelta(minutes=5)


class Output(TypedDict):
    rows: list[list[int | str]]
    negative: int


def transform(source: BinaryIO, region: str, start: datetime, end: datetime) -> Output:
    """Keep the exact source row number and express prices in millionths of AUD/MWh."""
    if source.readline(len(HEADER)) != HEADER:
        raise ValueError("invalid price header")
    selected: dict[datetime, list[int | str]] = {}
    for line_number, line in enumerate(source, 2):
        match = ROW.fullmatch(line)
        if match is None:
            raise ValueError("invalid price row")
        label, row_region, price = (part.decode("ascii") for part in match.groups())
        interval = datetime.fromisoformat(label)
        if row_region == region and start <= interval < end:
            if interval in selected or (interval - start) % INTERVAL:
                raise ValueError("duplicate or unaligned interval")
            selected[interval] = [label, int(Decimal(price) * 1_000_000), line_number]
    if len(selected) != (end - start) // INTERVAL:
        raise ValueError("missing interval")
    rows = [selected[interval] for interval in sorted(selected)]
    return {"rows": rows, "negative": sum(int(row[1]) < 0 for row in rows)}


if __name__ == "__main__":
    signal.alarm(10)
    region, start, end = sys.argv[1:]
    try:
        output = transform(
            sys.stdin.buffer, region, datetime.fromisoformat(start), datetime.fromisoformat(end)
        )
    except ValueError:
        sys.exit(2)
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))
