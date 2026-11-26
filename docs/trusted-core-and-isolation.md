# Trusted core and isolated-browser foundation

## Decision and scope

C01 defines contracts and build inputs; it does not claim that the current
prototype is isolated. C02 must replace the inherited host process with the
backend below before an isolated runtime is available.

The primary pilot backend is a root-managed Podman service on Linux, with the
browser container owned by a dedicated `ubc-browser` service account. The
ordinary node account has neither the Podman socket nor permission to enter the
browser account's runtime directories. The service launches the pinned image,
drops all Linux capabilities, applies cgroup limits, uses a read-only root
filesystem, and provisions only the named runtime, profile, and quarantine
writable areas. C02 will implement and test those launch properties.

Native macOS and Windows, Docker Desktop, a rootless container owned by the
ordinary node user, and the existing child-process runtime are development
fixtures only. They are unsupported for the pilot security gate: each leaves a
same-user or desktop-VM administrator able to inspect or interfere with the
browser. There is no automatic fallback from the supported backend to one of
these weaker modes.

This choice is deliberately narrower than “any OCI runtime.” The trust claim
depends on who controls the runtime socket and service account, not just on
container namespaces. A lightweight VM remains a valid future backend, but it
must implement the same contracts and pass the same gate before use.

## Trusted core

The trusted core consists of:

- the supervisor entry point;
- the executable policy evaluator;
- the core audit writer;
- the signing-only node-key interface;
- the constitution version and content digest;
- the dependency lock; and
- the release manifest that binds those inputs to an immutable browser image
  digest and isolation-contract version.

The [release-manifest schema](release-manifest.schema.json) is intentionally
unsigned in C01. A manifest is a deterministic statement of what is expected,
not evidence that an untrusted host is honest. Release signing, provenance,
SBOMs, and platform attestation are later release-engineering gates.

The node-key interface can sign but cannot export private key material. The
browser runtime receives no node-key object and no core audit sink. Browser
boundary events are untrusted facts consumed and wrapped by the trusted audit
writer; they are not direct writes to the core journal.

## Browser build and filesystem contract

Build from the repository root:

```shell
docker build --file browser-runtime/Dockerfile --tag ubc-browser:local .
```

After publishing or loading the image, create a manifest from its immutable
OCI digest (never from a mutable tag):

```shell
uv run ubc release-manifest \
  --release-id 0.1.0-c01 \
  --constitution-version 0.1-draft \
  --browser-image-reference localhost/ubc-browser \
  --browser-image-digest sha256:<64 lowercase hex characters> \
  --output release/trusted-core-manifest.json
```

The build pins the Python and `uv` base images by multi-platform OCI digest,
uses a dated Debian snapshot, pins Chromium, installs Python dependencies from
`uv.lock`, and runs as UID/GID 65532. The image exposes no port and contains no
host path. C02 must launch it with no container socket and with these
boundary-local writable areas only:

| Area | In-boundary location | Purpose |
|---|---|---|
| `runtime` | `/run/ubc` | authenticated channel material and ephemeral state |
| `profile` | `/var/lib/ubc/profile` | isolated Chromium profile |
| `quarantine` | `/var/lib/ubc/quarantine` | untrusted downloads awaiting C04 |

The browser must not mount node source, executable policy, constitution,
release metadata, node-key storage, core audit storage, task workspaces, or a
container-engine socket. Released task artifacts will cross the boundary by
content digest only after C04 scanning.

## Channel and fixture topology

`IsolatedBrowserLaunchSpec` contains an immutable image digest, normalized
origins, resource limits, named writable areas, and opaque control/egress
channel capabilities. It contains no host path or endpoint. The raw Chromium
debug endpoint remains entirely inside the browser security boundary.

The machine-readable [fixture topology](../browser-runtime/fixture-topology.json)
defines three isolated networks. The browser shares `control` only with the
trusted node boundary and `egress` only with the gate. Fixtures are reachable
only from the gate. No browser port is published to the host. C03 will make
that topology executable and prove bypass resistance.

Human takeover is disabled in the isolated launch contract until C06 provides
an authenticated same-session surface.
