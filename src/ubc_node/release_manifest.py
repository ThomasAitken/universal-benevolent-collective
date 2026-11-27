"""Constitution-bound trusted-core release manifest generation and verification."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import cast

from ubc_node.contracts import JsonValue

MANIFEST_SCHEMA_VERSION = 1
ISOLATION_CONTRACT_VERSION = 2
PRIMARY_ISOLATION_BACKEND = "linux-rootful-podman-service"
_HEX_DIGITS = frozenset("0123456789abcdef")

_CORE_COMPONENTS: Mapping[str, tuple[str, str]] = {
    "supervisor": ("src/ubc_node/supervisor.py", "ubc_node.supervisor:RunSupervisor"),
    "executable_policy": (
        "src/ubc_node/policy.py",
        "ubc_node.policy:PrototypePolicyEvaluator",
    ),
    "audit_writer": ("src/ubc_node/event_log.py", "ubc_node.event_log:JsonlEventLog"),
    "node_key_interface": ("src/ubc_node/node_keys.py", "ubc_node.node_keys:NodeKey"),
    "download_quarantine": (
        "src/ubc_node/quarantine.py",
        "ubc_node.quarantine:DownloadQuarantine",
    ),
}
_BROWSER_STATIC_INPUTS = (
    "browser-runtime/Dockerfile",
    "browser-runtime/Dockerfile.dockerignore",
    "README.md",
    "pyproject.toml",
    "uv.lock",
)


class ReleaseManifestError(ValueError):
    """Raised when release metadata is malformed or does not match local artifacts."""


def create_release_manifest(
    root: Path,
    *,
    release_id: str,
    constitution_version: str,
    browser_image_reference: str,
    browser_image_digest: str,
) -> dict[str, JsonValue]:
    """Create deterministic release metadata for the current trusted-core inputs."""
    root = root.resolve()
    for name, value in (
        ("release_id", release_id),
        ("constitution_version", constitution_version),
        ("browser_image_reference", browser_image_reference),
    ):
        if not value.strip():
            raise ReleaseManifestError(f"{name} must not be empty")
    _validate_digest(browser_image_digest, "browser_image_digest")
    if "@" in browser_image_reference:
        raise ReleaseManifestError("browser_image_reference must not include a digest")

    components: dict[str, JsonValue] = {}
    for role, (relative_path, entrypoint) in _CORE_COMPONENTS.items():
        components[role] = {
            "path": relative_path,
            "entrypoint": entrypoint,
            "sha256": _digest_file(_resolve_input(root, relative_path)),
        }

    constitution_path = "CONSTITUTION.md"
    dependency_path = "uv.lock"
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_id": release_id,
        "constitution": {
            "version": constitution_version,
            "path": constitution_path,
            "sha256": _digest_file(_resolve_input(root, constitution_path)),
        },
        "trusted_core": components,
        "dependency_lock": {
            "path": dependency_path,
            "sha256": _digest_file(_resolve_input(root, dependency_path)),
        },
        "browser_image": {
            "reference": browser_image_reference,
            "digest": browser_image_digest,
            "build_context_sha256": _browser_context_digest(root),
        },
        "isolation": {
            "backend": PRIMARY_ISOLATION_BACKEND,
            "contract_version": ISOLATION_CONTRACT_VERSION,
        },
    }


def write_release_manifest(path: Path, manifest: Mapping[str, JsonValue]) -> None:
    """Write canonical manifest JSON without embedding generation-time state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(manifest, allow_nan=False, indent=2, sort_keys=True)
    path.write_text(f"{body}\n", encoding="utf-8")


def load_release_manifest(path: Path) -> dict[str, JsonValue]:
    """Load a manifest as a JSON object."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseManifestError("release manifest is not readable JSON") from error
    if not isinstance(value, dict):
        raise ReleaseManifestError("release manifest must be a JSON object")
    return cast(dict[str, JsonValue], value)


def verify_release_manifest(root: Path, manifest: Mapping[str, JsonValue]) -> None:
    """Fail if schema-critical fields or any bound local input differ."""
    try:
        schema_version = manifest["schema_version"]
        release_id = _string(manifest, "release_id")
        constitution = _mapping(manifest, "constitution")
        browser = _mapping(manifest, "browser_image")
        isolation = _mapping(manifest, "isolation")
        dependency = _mapping(manifest, "dependency_lock")
        components = _mapping(manifest, "trusted_core")
    except KeyError as error:
        raise ReleaseManifestError(f"release manifest is missing {error.args[0]}") from error
    if type(schema_version) is not int or schema_version != MANIFEST_SCHEMA_VERSION:
        raise ReleaseManifestError("unsupported release manifest schema_version")
    if dependency.get("path") != "uv.lock":
        raise ReleaseManifestError("dependency lock path is not the required input")
    if type(isolation.get("contract_version")) is not int or isolation != {
        "backend": PRIMARY_ISOLATION_BACKEND,
        "contract_version": ISOLATION_CONTRACT_VERSION,
    }:
        raise ReleaseManifestError("isolation contract does not match this node release")
    if set(components) != set(_CORE_COMPONENTS):
        raise ReleaseManifestError("trusted_core component roles are incomplete or unknown")

    expected = create_release_manifest(
        root,
        release_id=release_id,
        constitution_version=_string(constitution, "version"),
        browser_image_reference=_string(browser, "reference"),
        browser_image_digest=_string(browser, "digest"),
    )
    if dict(manifest) != expected:
        raise ReleaseManifestError("release manifest does not match the bound local inputs")


def _browser_context_digest(root: Path) -> str:
    relative_paths = [*_BROWSER_STATIC_INPUTS]
    relative_paths.extend(
        path.relative_to(root).as_posix()
        for path in sorted((root / "src").rglob("*"))
        if (path.is_symlink() or not path.is_dir()) and not _is_ignored_context_file(path)
    )
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        path_digest = _digest_file(_resolve_input(root, relative_path))
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(path_digest.encode())
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _is_ignored_context_file(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == ".DS_Store"


def _resolve_input(root: Path, relative_path: str) -> Path:
    value = PurePosixPath(relative_path)
    if value.is_absolute() or ".." in value.parts:
        raise ReleaseManifestError("manifest input paths must be repository-relative")
    path = root.joinpath(*value.parts)
    if path.is_symlink() or not path.is_file():
        raise ReleaseManifestError(f"manifest input is not a regular file: {relative_path}")
    return path


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _mapping(value: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    result = value[key]
    if not isinstance(result, dict):
        raise ReleaseManifestError(f"{key} must be an object")
    return result


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    result = value[key]
    if not isinstance(result, str) or not result.strip():
        raise ReleaseManifestError(f"{key} must be a non-empty string")
    return result


def _validate_digest(value: str, name: str) -> None:
    digest = value.removeprefix("sha256:")
    if (
        not value.startswith("sha256:")
        or len(digest) != 64
        or any(character not in _HEX_DIGITS for character in digest)
    ):
        raise ReleaseManifestError(f"{name} must be an OCI sha256 digest")
