"""Pilot thesis submission and independent local review under the v1 schemas."""

import json
import re
import sqlite3
from difflib import unified_diff
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from referencing import Registry, Resource
from referencing.jsonschema import Schema

from ubc_node.pilot.catalogue import (
    Object,
    Record,
    ResearchCatalogue,
    active_project,
    digest,
    encode,
    find,
    note,
    now,
    object_value,
    put,
    read,
    require,
    utc,
)

type Decision = Literal["approve", "request_changes", "reject"]

_SCHEMA_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,199}")
_REVIEW_STATUS: dict[str, str] = {
    "approve": "approved",
    "request_changes": "changes_requested",
    "reject": "rejected",
}


def _objects(document: Object, field: str) -> list[Object]:
    values = document[field]
    assert isinstance(values, list)
    return [object_value(value) for value in values]


def _validate_source_references(draft: Object, source_id: str) -> None:
    assessment = object_value(draft["epistemic_assessment"])
    references = _objects(draft, "open_questions")
    for field in ("theoretical_basis", "empirical_basis", "scientific_consensus"):
        basis = assessment[field]
        if basis is not None:
            references.append(object_value(basis))
    for reference in references:
        ids = reference["source_ids"]
        assert isinstance(ids, list)
        if any(citation != source_id for citation in ids):
            raise ValueError("assessment and open questions must cite a declared source")


def _current(db: sqlite3.Connection, project: str, thesis: str) -> str | None:
    row = db.execute(
        """
        SELECT id, project FROM records WHERE kind = 'revision'
            AND json_extract(payload, '$.thesis_id') = ?
        ORDER BY sequence DESC LIMIT 1
        """,
        (thesis,),
    ).fetchone()
    if row is None:
        return None
    if row[1] != project:
        raise ValueError("thesis belongs to another project")
    return str(row[0])


def _diff(base: Object, draft: Object) -> str:
    return "\n".join(
        unified_diff(
            json.dumps(base, indent=2, sort_keys=True).splitlines(),
            json.dumps(draft, indent=2, sort_keys=True).splitlines(),
            fromfile="pinned revision",
            tofile="proposal",
            lineterm="",
        )
    )


class ThesisReview:
    """The model's caller exposes submission and pinned reads. Review stays operator-owned."""

    def __init__(self, catalogue: ResearchCatalogue, schemas: Path) -> None:
        self.catalogue = catalogue
        revision = object_value(
            json.loads((schemas / "knowledge-base-thesis.schema.json").read_bytes())
        )
        workflow = object_value(
            json.loads((schemas / "knowledge-base-thesis-workflow.schema.json").read_bytes())
        )
        revision_uri = str(revision["$id"])
        registry = Registry[Schema]().with_resource(revision_uri, Resource.from_contents(revision))
        self.revision: Validator = Draft202012Validator(
            revision, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        self.workflow: Validator = Draft202012Validator(
            workflow, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
        )

        self.actor: Validator = Draft202012Validator(
            {"$ref": revision_uri + "#/$defs/actor"}, registry=registry
        )

    def pin(self, task: str, revision_id: str) -> Record:
        with self.catalogue.transaction() as db:
            project = active_project(db, task)
            revision = require(db, revision_id, "revision", project)
            put(db, f"pin:{task}", "pin", project, {}, (task, revision_id), task=task)
            return revision

    def submit(
        self,
        task: str,
        analysis_id: str,
        *,
        actor: Object,
        rationale: str,
        base: str | None = None,
        draft: Object | None = None,
    ) -> Record:
        """Submit a draft, revision, or reasoned no-change result with pinned evidence."""
        with self.catalogue.transaction() as db:
            project = active_project(db, task)
            analysis = require(db, analysis_id, "analysis", project)
            source = read(db, str(analysis.data["source"]))
            key = f"proposal:{task}"
            existing = find(db, key)
            pinned: Object = {}
            if base is not None:
                pin = require(db, f"pin:{task}", "pin", project)
                if base not in pin.inputs:
                    raise ValueError("base revision has not been pinned for this task")
                pinned = require(db, base, "revision", project).data
            if draft is None and base is None:
                raise ValueError("no change requires a pinned revision")
            if draft is not None:
                draft = {
                    **draft,
                    "provenance": {
                        "submitted_by": actor,
                        "project_ids": [project],
                        "task_ids": [task],
                        "run_ids": [task],
                        "source_snapshot_ids": [source.id],
                    },
                }
                if len(encode(draft)) > 128 * 1024:
                    raise ValueError("thesis proposal exceeds the pilot limit")
                self.revision.validate(draft)
                thesis = str(draft["thesis_id"])
                metadata = object_value(draft["revision"])
                expected = (
                    1
                    if base is None
                    else int(str(object_value(pinned["revision"])["revision_number"])) + 1
                )
                if (
                    metadata["previous_revision_id"] != base
                    or metadata["revision_number"] != expected
                    or metadata["content_hash"] is not None
                    or draft["related_theses"] != []
                    or object_value(draft["hierarchy"])["parent_theses"] != []
                    or (
                        base is not None
                        and (
                            thesis != pinned["thesis_id"]
                            or draft["thesis_created_at"] != pinned["thesis_created_at"]
                            or metadata["change_type"] == "creation"
                        )
                    )
                ):
                    raise ValueError("invalid pilot revision metadata or relationships")
                evidence = _objects(draft, "evidence")
                sources = _objects(draft, "sources")
                if (
                    len(sources) != 1
                    or not evidence
                    or sources[0]["source_snapshot_id"] != source.id
                    or sources[0]["url"] != source.data["locator"]
                    or any(
                        item["source_id"] != sources[0]["source_id"]
                        or not str(item["locator"]).strip()
                        for item in evidence
                    )
                    or len({str(item["evidence_id"]) for item in evidence}) != len(evidence)
                ):
                    raise ValueError("evidence must cite the pinned analysis source and a locator")
                _validate_source_references(draft, str(sources[0]["source_id"]))
                if existing is None and find(db, str(metadata["revision_id"])) is not None:
                    raise ValueError("revision identifier is already in use")
            else:
                thesis = str(pinned["thesis_id"])
                # The standalone result uses the same actor contract as a revision draft.
                self.actor.validate(actor)
            if existing is None and _current(db, project, thesis) != base:
                raise ValueError("stale thesis base")
            inputs = (task, analysis_id) if base is None else (task, analysis_id, base)
            return put(
                db,
                key,
                "proposal",
                project,
                {
                    "task": task,
                    "thesis": thesis,
                    "base": base,
                    "draft": draft,
                    "author": actor,
                    "rationale": note(rationale),
                    "evidence": {
                        "analysis": analysis_id,
                        "source": source.id,
                        "locator": source.data["locator"],
                    },
                    "diff": "" if draft is None else _diff(pinned, draft),
                },
                inputs,
                task=task,
            )

    def review(
        self,
        proposal_id: str,
        *,
        reviewer: str,
        decision: Decision,
        explanation: str,
        independence_basis: str,
        conflicts: tuple[str, ...] = (),
    ) -> Record:
        """Record one terminal human review and atomically accept a local revision if approved."""
        if decision not in _REVIEW_STATUS:
            raise ValueError("unsupported review decision")
        if not _SCHEMA_IDENTIFIER.fullmatch(reviewer):
            raise ValueError("invalid reviewer identifier")
        with self.catalogue.transaction() as db:
            proposal = read(db, proposal_id)
            if proposal.kind != "proposal" or proposal.project is None:
                raise ValueError("expected a thesis proposal")
            author = object_value(proposal.data["author"])
            if reviewer == author["actor_id"] or (decision == "approve" and conflicts):
                raise ValueError(
                    "review requires an independent reviewer without disclosed conflicts on approval"
                )
            key = f"review:{proposal_id}"
            request: Object = {
                "reviewer": reviewer,
                "decision": decision,
                "explanation": note(explanation),
                "independence_basis": note(independence_basis),
                "conflicts": [note(item) for item in conflicts],
            }
            existing = find(db, key)
            if existing is not None:
                if request != existing.data["request"]:
                    raise ValueError("conflicting review retry")
                return existing
            base = proposal.data["base"]
            thesis = str(proposal.data["thesis"])
            if decision == "approve":
                active_project(db, str(proposal.data["task"]))
                if _current(db, proposal.project, thesis) != base:
                    raise ValueError("stale thesis review")
            revision: Object | None = None
            draft = proposal.data["draft"]
            revision_id = base
            if draft is not None:
                revision = object_value(draft)
                metadata = object_value(revision["revision"])
                revision_id = metadata["revision_id"]
            stamp = utc(now())
            status = _REVIEW_STATUS[decision]
            workflow: Object = {
                "schema_version": "1.0.0",
                "thesis_id": thesis,
                "revision_id": revision_id,
                "lifecycle_status": "under_review",
                "status_rationale": "Pilot review recorded in the local catalogue.",
                "capability_gate": {
                    "policy_id": "local-pilot-v1",
                    "outcome": "human_override"
                    if decision == "approve" and author["actor_type"] != "human"
                    else "not_required",
                    "model_profile_id": None,
                    "evaluation_ids": [],
                    "checked_at": stamp,
                    "explanation": explanation,
                },
                "review": {
                    "status": status,
                    "policy_id": "local-pilot-v1",
                    "required_review_count": 1,
                    "human_approval_required": True,
                    "published_at": None,
                    "decisions": [
                        {
                            "decision_id": "review-" + digest(key.encode()),
                            "reviewer": {
                                "actor_type": "human",
                                "actor_id": reviewer,
                                "display_name": None,
                                "model": None,
                            },
                            "decision": decision,
                            "explanation": explanation,
                            "independence_basis": independence_basis,
                            "conflicts_disclosed": list(conflicts),
                            "decided_at": stamp,
                        }
                    ],
                },
                "updated_at": stamp,
            }
            self.workflow.validate(workflow)
            review = put(
                db,
                key,
                "review",
                proposal.project,
                {"request": request, "workflow": workflow},
                (proposal.id,),
            )
            if decision == "approve" and revision is not None:
                metadata = object_value(revision["revision"])
                boundary = {
                    field: value
                    for field, value in revision.items()
                    if field not in {"provenance", "thesis_created_at", "revision_created_at"}
                }
                boundary["revision"] = {
                    field: value for field, value in metadata.items() if field != "content_hash"
                }
                metadata["content_hash"] = "sha256:" + digest(encode(boundary))
                revision["revision_created_at"] = stamp
                if base is None:
                    revision["thesis_created_at"] = stamp
                self.revision.validate(revision)
                put(db, str(revision_id), "revision", proposal.project, revision, (review.id,))
            return review
