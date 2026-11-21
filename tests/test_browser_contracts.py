"""Tests for the policy-shaped browser capability contracts."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from ubc_node.browser_contracts import (
    BrowserContractError,
    BrowserProfileMode,
    BrowserSessionConfig,
    ExtractionField,
    ExtractionResult,
    ExtractionSchema,
    ExtractionType,
    PersistencePolicyDecision,
    normalize_origin,
    origin_for_url,
)
from ubc_node.contracts import JsonValue


def test_origins_are_normalized_without_paths_or_credentials() -> None:
    assert normalize_origin("HTTPS://EXAMPLE.COM:443/") == "https://example.com"
    assert normalize_origin("http://example.com:8080") == "http://example.com:8080"
    assert origin_for_url("https://EXAMPLE.com:443/path?q=1#fragment") == "https://example.com"


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.com",
        "https:///missing-host",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://example.com#fragment",
    ],
)
def test_invalid_allowed_origins_are_rejected(origin: str) -> None:
    with pytest.raises(BrowserContractError):
        normalize_origin(origin)


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/page", "https:///missing-host", "https://user:secret@example.com"],
)
def test_invalid_navigation_urls_are_rejected(url: str) -> None:
    with pytest.raises(BrowserContractError):
        origin_for_url(url)


def test_temporary_profile_is_default_and_normalizes_configuration(tmp_path: Path) -> None:
    config = BrowserSessionConfig(
        workspace=tmp_path / "workspace/.." / "workspace",
        allowed_origins=frozenset({"https://EXAMPLE.com:443"}),
    )

    assert config.workspace == (tmp_path / "workspace").resolve()
    assert config.allowed_origins == {"https://example.com"}
    assert config.profile_path is None


def test_persistent_profile_requires_matching_policy_evidence(tmp_path: Path) -> None:
    decision = PersistencePolicyDecision("project_1", True, "Approved for project authentication")
    config = BrowserSessionConfig(
        workspace=tmp_path / "task",
        allowed_origins=frozenset({"https://example.com"}),
        profile_mode=BrowserProfileMode.PERSISTENT,
        project_id="project_1",
        persistence_policy=decision,
        persistent_profile_root=tmp_path / "profiles",
    )

    assert config.profile_path == (tmp_path / "profiles" / "project_1").resolve()


@pytest.mark.parametrize(
    ("project_id", "reason"),
    [("", "reason"), ("project", "")],
)
def test_persistence_policy_requires_identity_and_reason(project_id: str, reason: str) -> None:
    with pytest.raises(BrowserContractError):
        PersistencePolicyDecision(project_id, True, reason)


def test_session_config_rejects_missing_origins_and_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(BrowserContractError, match="origin"):
        BrowserSessionConfig(tmp_path, frozenset())

    valid = BrowserSessionConfig(tmp_path, frozenset({"https://example.com"}))
    invalid = (
        ("max_inspection_chars", lambda: replace(valid, max_inspection_chars=0)),
        ("max_fill_chars", lambda: replace(valid, max_fill_chars=0)),
        ("max_download_bytes", lambda: replace(valid, max_download_bytes=0)),
    )
    for name, build in invalid:
        with pytest.raises(BrowserContractError, match=name):
            build()


def test_temporary_profile_rejects_persistence_configuration(tmp_path: Path) -> None:
    with pytest.raises(BrowserContractError, match="temporary"):
        BrowserSessionConfig(
            tmp_path,
            frozenset({"https://example.com"}),
            project_id="project",
        )


@pytest.mark.parametrize(
    ("project_id", "policy", "root", "message"),
    [
        (None, None, None, "project_id"),
        ("../escape", None, None, "unsafe"),
        ("project", None, None, "profile root"),
        ("project", None, Path("profiles"), "policy decision"),
        (
            "project",
            PersistencePolicyDecision("other", True, "allowed"),
            Path("profiles"),
            "does not match",
        ),
        (
            "project",
            PersistencePolicyDecision("project", False, "denied"),
            Path("profiles"),
            "policy decision",
        ),
    ],
)
def test_persistent_profile_rejects_incomplete_or_mismatched_policy(
    tmp_path: Path,
    project_id: str | None,
    policy: PersistencePolicyDecision | None,
    root: Path | None,
    message: str,
) -> None:
    with pytest.raises(BrowserContractError, match=message):
        BrowserSessionConfig(
            tmp_path,
            frozenset({"https://example.com"}),
            BrowserProfileMode.PERSISTENT,
            project_id,
            policy,
            root,
        )


def test_extraction_schema_requires_safe_unique_fields() -> None:
    fields = (
        ExtractionField("title", ExtractionType.STRING),
        ExtractionField("count", ExtractionType.INTEGER, required=False),
    )
    assert ExtractionSchema(fields).fields == fields

    with pytest.raises(BrowserContractError, match="safe"):
        ExtractionField("not safe", ExtractionType.STRING)
    with pytest.raises(BrowserContractError, match="at least"):
        ExtractionSchema(())
    with pytest.raises(BrowserContractError, match="unique"):
        ExtractionSchema((fields[0], fields[0]))


def test_extraction_result_copies_and_freezes_data() -> None:
    assert ExtractionResult().data == {}
    source: dict[str, JsonValue] = {"title": "Example"}
    result = ExtractionResult(source)
    source["title"] = "Changed"
    assert result.data == {"title": "Example"}

    with pytest.raises(TypeError):
        cast(dict[str, JsonValue], result.data)["title"] = "Nope"
