"""Tests for constitution-bound trusted-core release metadata."""

import json
from pathlib import Path
from typing import cast

import pytest

from ubc_node.contracts import JsonValue
from ubc_node.release_manifest import (
    ISOLATION_CONTRACT_VERSION,
    PRIMARY_ISOLATION_BACKEND,
    ReleaseManifestError,
    _resolve_input,  # pyright: ignore[reportPrivateUsage]
    create_release_manifest,
    load_release_manifest,
    verify_release_manifest,
    write_release_manifest,
)

_ROOT = Path(__file__).parents[1]
_DIGEST = f"sha256:{'a' * 64}"


def _manifest() -> dict[str, JsonValue]:
    return create_release_manifest(
        _ROOT,
        release_id="0.1.0-c01",
        constitution_version="0.1-draft",
        browser_image_reference="localhost/ubc-browser",
        browser_image_digest=_DIGEST,
    )


def test_manifest_binds_every_required_input_and_verifies() -> None:
    manifest = _manifest()
    verify_release_manifest(_ROOT, manifest)

    assert manifest["schema_version"] == 1
    assert cast(dict[str, object], manifest["constitution"])["path"] == "CONSTITUTION.md"
    components = cast(dict[str, object], manifest["trusted_core"])
    assert set(components) == {
        "supervisor",
        "executable_policy",
        "audit_writer",
        "node_key_interface",
        "download_quarantine",
    }
    isolation = cast(dict[str, object], manifest["isolation"])
    assert isolation["backend"] == PRIMARY_ISOLATION_BACKEND
    serialized = json.dumps(manifest)
    assert "sha256:" in serialized


def test_manifest_write_and_load_are_canonical(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "manifest.json"
    manifest = _manifest()
    write_release_manifest(path, manifest)

    assert load_release_manifest(path) == manifest
    assert path.read_text().endswith("\n")


@pytest.mark.parametrize(
    "overrides",
    [
        {"release_id": ""},
        {"constitution_version": ""},
        {"browser_image_reference": ""},
    ],
)
def test_manifest_requires_nonempty_identity_fields(overrides: dict[str, str]) -> None:
    values = {
        "release_id": "release",
        "constitution_version": "draft",
        "browser_image_reference": "localhost/browser",
        "browser_image_digest": _DIGEST,
        **overrides,
    }
    with pytest.raises(ReleaseManifestError, match="must not be empty"):
        create_release_manifest(_ROOT, **values)


@pytest.mark.parametrize(
    "digest",
    [
        "short",
        f"sha256:{'z' * 64}",
        f"sha256:{'A' * 64}",
        f"sha256:0x{'a' * 62}",
        f"sha256:{'a' * 31}_{'a' * 32}",
    ],
)
def test_manifest_rejects_invalid_image_digests(digest: str) -> None:
    with pytest.raises(ReleaseManifestError, match="OCI sha256"):
        create_release_manifest(
            _ROOT,
            release_id="release",
            constitution_version="draft",
            browser_image_reference="localhost/browser",
            browser_image_digest=digest,
        )


def test_manifest_rejects_embedded_image_digests() -> None:
    with pytest.raises(ReleaseManifestError, match="must not include"):
        create_release_manifest(
            _ROOT,
            release_id="release",
            constitution_version="draft",
            browser_image_reference=f"localhost/browser@{_DIGEST}",
            browser_image_digest=_DIGEST,
        )


def test_manifest_loader_rejects_unreadable_or_non_object_json(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ReleaseManifestError, match="readable JSON"):
        load_release_manifest(missing)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(ReleaseManifestError, match="readable JSON"):
        load_release_manifest(invalid)
    invalid.write_text("[]")
    with pytest.raises(ReleaseManifestError, match="JSON object"):
        load_release_manifest(invalid)


def test_manifest_verifier_rejects_structural_and_content_changes() -> None:
    cases: list[tuple[dict[str, JsonValue], str]] = []
    missing = _manifest()
    del missing["release_id"]
    cases.append((missing, "missing release_id"))
    wrong_schema = _manifest()
    wrong_schema["schema_version"] = 2
    cases.append((wrong_schema, "schema_version"))
    bool_schema = _manifest()
    bool_schema["schema_version"] = True
    cases.append((bool_schema, "schema_version"))
    wrong_lock = _manifest()
    cast(dict[str, JsonValue], wrong_lock["dependency_lock"])["path"] = "other.lock"
    cases.append((wrong_lock, "dependency lock"))
    wrong_isolation = _manifest()
    cast(dict[str, JsonValue], wrong_isolation["isolation"])["contract_version"] = 1
    cases.append((wrong_isolation, "isolation contract"))
    bool_isolation = _manifest()
    cast(dict[str, JsonValue], bool_isolation["isolation"])["contract_version"] = True
    cases.append((bool_isolation, "isolation contract"))
    wrong_components = _manifest()
    del cast(dict[str, JsonValue], wrong_components["trusted_core"])["supervisor"]
    cases.append((wrong_components, "component roles"))
    wrong_digest = _manifest()
    constitution = cast(dict[str, JsonValue], wrong_digest["constitution"])
    constitution["sha256"] = _DIGEST
    cases.append((wrong_digest, "bound local inputs"))

    for manifest, message in cases:
        with pytest.raises(ReleaseManifestError, match=message):
            verify_release_manifest(_ROOT, manifest)


def test_manifest_verifier_rejects_wrong_value_types() -> None:
    manifest = _manifest()
    manifest["constitution"] = "not-an-object"
    with pytest.raises(ReleaseManifestError, match="constitution must be an object"):
        verify_release_manifest(_ROOT, manifest)

    manifest = _manifest()
    manifest["release_id"] = 3
    with pytest.raises(ReleaseManifestError, match="release_id must be a non-empty string"):
        verify_release_manifest(_ROOT, manifest)

    manifest = _manifest()
    cast(dict[str, JsonValue], manifest["browser_image"])["reference"] = ""
    with pytest.raises(ReleaseManifestError, match="reference must be a non-empty string"):
        verify_release_manifest(_ROOT, manifest)


def test_manifest_input_resolver_rejects_escape_missing_and_symlink(tmp_path: Path) -> None:
    with pytest.raises(ReleaseManifestError, match="repository-relative"):
        _resolve_input(tmp_path, "../outside")
    with pytest.raises(ReleaseManifestError, match="regular file"):
        _resolve_input(tmp_path, "missing")
    target = tmp_path / "target"
    target.write_text("target")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ReleaseManifestError, match="regular file"):
        _resolve_input(tmp_path, "link")


def test_manifest_schema_declares_closed_required_contract() -> None:
    schema = json.loads((_ROOT / "docs/release-manifest.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == 1
    assert (
        schema["properties"]["isolation"]["properties"]["contract_version"]["const"]
        == ISOLATION_CONTRACT_VERSION
    )
    assert set(schema["properties"]["trusted_core"]["required"]) == {
        "supervisor",
        "executable_policy",
        "audit_writer",
        "node_key_interface",
        "download_quarantine",
    }


def test_browser_context_digest_tracks_nested_src_files_and_ignores_caches(
    tmp_path: Path,
) -> None:
    for relative_path in (
        "CONSTITUTION.md",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "browser-runtime/Dockerfile",
        "browser-runtime/Dockerfile.dockerignore",
        "src/ubc_node/supervisor.py",
        "src/ubc_node/policy.py",
        "src/ubc_node/event_log.py",
        "src/ubc_node/node_keys.py",
        "src/ubc_node/quarantine.py",
        "src/ubc_node/py.typed",
    ):
        file_path = tmp_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(relative_path)

    baseline = create_release_manifest(
        tmp_path,
        release_id="0.1.0-test",
        constitution_version="0.1-draft",
        browser_image_reference="localhost/ubc-browser",
        browser_image_digest=_DIGEST,
    )

    cache_file = tmp_path / "src/ubc_node/__pycache__/supervisor.cpython-313.pyc"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"bytecode")
    (tmp_path / "src/ubc_node/.DS_Store").write_bytes(b"finder")
    with_ignored = create_release_manifest(
        tmp_path,
        release_id="0.1.0-test",
        constitution_version="0.1-draft",
        browser_image_reference="localhost/ubc-browser",
        browser_image_digest=_DIGEST,
    )
    assert with_ignored == baseline

    nested = tmp_path / "src/ubc_node/subpkg/worker.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("VALUE = 1\n")
    with_nested = create_release_manifest(
        tmp_path,
        release_id="0.1.0-test",
        constitution_version="0.1-draft",
        browser_image_reference="localhost/ubc-browser",
        browser_image_digest=_DIGEST,
    )
    assert (
        cast(dict[str, object], with_nested["browser_image"])["build_context_sha256"]
        != cast(dict[str, object], baseline["browser_image"])["build_context_sha256"]
    )


def test_checked_manifest_matches_bound_inputs() -> None:
    manifest = load_release_manifest(_ROOT / "release/trusted-core-manifest.json")
    verify_release_manifest(_ROOT, manifest)
