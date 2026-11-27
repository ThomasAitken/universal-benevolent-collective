"""Freeze and score a binary threshold forecast against named source versions."""

from datetime import datetime
from decimal import Decimal

from ubc_node.pilot.catalogue import (
    Record,
    ResearchCatalogue,
    active_project,
    find,
    id_value,
    note,
    now,
    object_value,
    put,
    read,
    require,
    utc,
)

_BASELINE_BASIS_POINTS = 5000


def freeze_question(
    catalogue: ResearchCatalogue,
    task: str,
    *,
    question_id: str,
    rationale: str,
    outcome_version: str,
    threshold: int,
    cutoff: datetime,
    deadline: datetime,
) -> Record:
    """Freeze one rule: at least threshold negative intervals in the charter window."""
    with catalogue.transaction() as db:
        project = active_project(db, task)
        charter = read(db, project)
        intervals = (
            datetime.fromisoformat(str(charter.data["end"]))
            - datetime.fromisoformat(str(charter.data["start"]))
        ).total_seconds() // 300
        key = f"question:{id_value(question_id)}"
        target = f"source:{project}:{id_value(outcome_version)}"
        if (
            type(threshold) is not int
            or not 1 <= threshold <= intervals
            or utc(cutoff) >= utc(deadline)
        ):
            raise ValueError("invalid threshold or cutoff")
        if find(db, key) is None and (utc(now()) >= utc(deadline) or find(db, target) is not None):
            raise ValueError("forecast question must precede deadline and outcome")
        return put(
            db,
            key,
            "question",
            project,
            {
                "question": (
                    f"Will {outcome_version} show at least {threshold} negative-price intervals "
                    f"in {charter.data['region']} from {charter.data['start']} (inclusive) "
                    f"to {charter.data['end']} (exclusive)?"
                ),
                "rationale": note(rationale),
                "target": target,
                "threshold": threshold,
                "cutoff": utc(cutoff),
                "deadline": utc(deadline),
                "rule": "negative_intervals_at_least",
                "scoring": "binary_brier_v1",
                "baseline_method": "constant_0.5",
                "resolution_locator": charter.data["source_locator"],
            },
            (project, task),
            task=task,
        )


def submit_forecast(
    catalogue: ResearchCatalogue, task: str, question_id: str, analysis_id: str, basis_points: int
) -> Record:
    """Store one probability and a constant 0.5 baseline at the same cutoff."""
    if type(basis_points) is not int or not 0 <= basis_points <= 10_000:
        raise ValueError("probability must be an integer in basis points")
    with catalogue.transaction() as db:
        project = active_project(db, task)
        question = require(db, question_id, "question", project)
        analysis = require(db, analysis_id, "analysis", project)
        key = f"forecast:{question.id}"
        data = question.data
        if find(db, key) is None and (
            not str(data["cutoff"]) <= utc(now()) < str(data["deadline"])
            or analysis.created_at > str(data["cutoff"])
            or find(db, str(data["target"])) is not None
        ):
            raise ValueError("submission or evidence is outside the pre-outcome cutoff")
        return put(
            db,
            key,
            "forecast",
            project,
            {
                "forecast_basis_points": basis_points,
                "baseline_basis_points": _BASELINE_BASIS_POINTS,
            },
            (task, question_id, analysis_id),
            task=task,
        )


def resolve_forecast(
    catalogue: ResearchCatalogue,
    forecast_id: str,
    analysis_id: str,
    *,
    supersedes: str | None = None,
) -> Record:
    """Append a score for the named release, or re-score from its direct correction."""
    with catalogue.transaction() as db:
        forecast = read(db, forecast_id)
        if forecast.kind != "forecast":
            raise ValueError("expected a forecast submission")
        project = forecast.project
        question = read(db, next(key for key in forecast.inputs if key.startswith("question:")))
        analysis = require(db, analysis_id, "analysis", project)
        source = read(db, str(analysis.data["source"]))
        key = f"score:{forecast_id}:{source.id}"
        original = f"score:{forecast_id}:{question.data['target']}"
        if supersedes is None:
            if key != original:
                raise ValueError("outcome is pending the named source version")
            inputs = (forecast_id, analysis_id)
        else:
            previous = require(db, supersedes, "score", project)
            if (
                forecast_id not in previous.inputs
                or previous.data["source"] != source.data["replaces"]
            ):
                raise ValueError("expected a direct correction to this forecast's resolution")
            inputs = (forecast_id, analysis_id, supersedes)
        if str(source.data["released_at"]) <= str(question.data["deadline"]) or utc(now()) <= str(
            question.data["deadline"]
        ):
            raise ValueError("outcome was available before deadline or is still pending")
        diagnostics = object_value(analysis.data["diagnostics"])
        outcome = int(int(str(diagnostics["negative"])) >= int(str(question.data["threshold"])))
        forecast_error = Decimal(str(forecast.data["forecast_basis_points"])) / 10_000 - outcome
        baseline_error = Decimal(str(forecast.data["baseline_basis_points"])) / 10_000 - outcome
        return put(
            db,
            key,
            "score",
            project,
            {
                "source": source.id,
                "outcome": outcome,
                "supersedes": supersedes,
                "forecast_brier": format(forecast_error**2, ".8f"),
                "baseline_brier": format(baseline_error**2, ".8f"),
                "forecast_error": format(forecast_error, ".4f"),
            },
            inputs,
        )
