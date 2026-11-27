"""Deterministic electricity-price inputs shared by the pilot tests."""

from datetime import UTC, datetime, timedelta

from ubc_node.pilot.analysis_runner import PriceWindow
from ubc_node.pilot.catalogue import Object, Record

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

AUTHOR: Object = {
    "actor_type": "agent",
    "actor_id": "agent-123",
    "display_name": None,
    "model": {"model_profile_id": "model-123", "provider": "fixture", "model_name": "test"},
}


def draft_thesis(
    source: Record, revision_id: str = "revision_first000", *, as_of: str = START.isoformat()
) -> Object:
    source_id = "source_fixture123"
    return {
        "schema_version": "1.0.0",
        "thesis_id": "thesis_fixture0123",
        "revision": {
            "revision_id": revision_id,
            "revision_number": 1,
            "previous_revision_id": None,
            "change_type": "creation",
            "change_summary": "Baseline.",
            "content_hash": None,
        },
        "language": "en",
        "statement": "One of two NSW1 fixture intervals had a negative price.",
        "description": "Two consecutive five-minute intervals.",
        "thesis_type": "descriptive",
        "domains": ["electricity"],
        "keywords": ["project-prices"],
        "content_license": "CC0-1.0",
        "attribution_notice": "Synthetic fixture.",
        "epistemic_assessment": {
            "confidence_method": "basis_matrix_v1",
            "theoretical_basis": {
                "strength": "none",
                "explanation": "Descriptive.",
                "source_ids": [],
                "limitations": [],
            },
            "empirical_basis": {
                "strength": "low",
                "explanation": "Two intervals.",
                "source_ids": [source_id],
                "limitations": [],
            },
            "scientific_consensus": None,
            "last_assessed_at": as_of,
        },
        "sources": [
            {
                "source_id": source_id,
                "resource_type": "dataset",
                "title": "Fixture CSV",
                "authors": [],
                "publisher": "",
                "publication_year": 2026,
                "url": SOURCE,
                "archived_url": None,
                "identifiers": {"doi": None, "pmid": None, "arxiv": None, "isbn": None},
                "peer_review_status": "not_applicable",
                "accessed_at": as_of,
                "source_snapshot_id": source.id,
                "status_at_revision": "active",
                "status_checked_at": as_of,
            }
        ],
        "evidence": [
            {
                "evidence_id": "evidence-123",
                "source_id": source_id,
                "stance": "supports",
                "evidence_role": "key",
                "claim_or_finding": "One negative interval.",
                "explanation": "The sampled row is negative.",
                "locator": "CSV line 3",
                "verification_status": "content_checked",
                "independence_cluster": None,
                "added_at": as_of,
            }
        ],
        "hierarchy": {"parent_theses": []},
        "related_theses": [],
        "open_questions": [],
        "thesis_created_at": as_of,
        "revision_created_at": as_of,
    }
