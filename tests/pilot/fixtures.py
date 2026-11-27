"""Deterministic electricity-price inputs shared by the pilot tests."""

from datetime import UTC, datetime, timedelta

from ubc_node.pilot.analysis_runner import PriceWindow

START = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW = PriceWindow("NSW1", START, START + timedelta(minutes=10))
SOURCE = "https://data.example/prices.csv"
HEADER = b"interval_utc,region,price\n"
BASE = (
    HEADER
    + b"2026-01-01T00:05:00Z,NSW1,5.1\n2026-01-01T00:00:00Z,NSW1,-12.3\n"
    + b"2026-01-01T00:00:00Z,SA1,-1\n"
)
OUTCOME = BASE.replace(b"5.1", b"-5.1")
