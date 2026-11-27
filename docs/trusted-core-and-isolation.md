# Trusted core and isolated-browser foundation

## Decision and scope

The isolated browser requires Linux with cgroup v2, systemd 254 or later,
local Podman 5.4 or later, and crun. A root-owned systemd socket starts one
broker, one browser container, and one egress gate container per connection. Only the broker uses
rootful Podman. The worker and Chromium run as UID/GID 65532 (`ubc-browser`)
inside a private user namespace mapped to subordinate host IDs. This uses the
backend recorded in the C01 manifest while keeping runtime storage and engine
authority outside the ordinary node account.

The `ubc-node` group can open the launcher socket. Keep model processes outside
that group and keep the node account outside any container-engine or
administrative group. Processes sharing the node UID can ask for a new
container. Each accepted connection is distinct and cannot attach to an
existing container by supplying its session ID or discovering the socket path.
This isolation model assumes a trusted node process, host root, and kernel.
Host-level inspection or control of the trusted node process is outside this
boundary.

Native macOS and Windows, Docker Desktop, and the existing local in-process
and supervised browser demos remain development fixtures. A disposable Linux
guest running the specified stack can supply local acceptance evidence.
Repeat the integrated browser and egress gates on the intended Linux pilot
deployment before live retrieval. Linux launch failures are terminal.
[Pilot acceptance](single-node-pilot-roadmap.md#c07--integrated-pilot-and-acceptance)

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

## Build and install the runtime

Use a disposable Linux test host. With a Docker build host and rootful Podman
available on the test host, build and load the same named OCI image:

```shell
docker build --provenance=false --file browser-runtime/Dockerfile --tag localhost/ubc-browser:c03 .
image=$(docker image inspect localhost/ubc-browser:c03 --format '{{index .RepoDigests 0}}')
docker save localhost/ubc-browser:c03 | sudo podman load
```

Verify the loaded image resolves by digest on the Linux host. Use that
digest in both the manifest and systemd release configuration. For an architecture-specific local build, generate a
local manifest from the current source tree:

```shell
mkdir -p .ubc
uv run ubc release-manifest \
  --release-id 0.1.0-c03 \
  --constitution-version 0.1-draft \
  --browser-image-reference localhost/ubc-browser \
  --browser-image-digest "${image##*@}" \
  --output .ubc/c03-manifest.json
```

Reserve non-overlapping subordinate ranges in `/etc/subuid` and `/etc/subgid`
for Podman's rootful `containers` allocator. The socket permits four concurrent
connections and each uses two separate 65,536-slot UID/GID ranges. Confirm the local image
resolves by digest with `sudo podman image inspect localhost/ubc-browser@sha256:…`.
[Podman run reference](https://docs.podman.io/en/v5.4.2/markdown/podman-run.1.html)

Create the `ubc-node` group and add the ordinary node account. Start a fresh
login after changing group membership. Install the launcher and units as root:

```shell
sudo install -D -m 0644 browser-runtime/broker.py /usr/libexec/ubc-browser/broker.py
sudo install -m 0644 browser-runtime/ubc-browser.socket /etc/systemd/system/
sudo install -m 0644 browser-runtime/ubc-browser@.service /etc/systemd/system/
sudo install -d -m 0755 /etc/ubc-browser
```

Set these values in root-owned `/etc/ubc-browser/release.env` using the values
from the loaded image and manifest:

```ini
UBC_BROWSER_IMAGE=localhost/ubc-browser@sha256:<64 lowercase hex characters>
UBC_BROWSER_RELEASE=0.1.0-c03
UBC_EGRESS_NETWORK=ubc-egress-project
```

Configure a dedicated Podman bridge with a trusted DNS resolver and the
required upstream routes. Give the bridge the name in `UBC_EGRESS_NETWORK`,
which must start with `ubc-egress-`. The browser container never joins it.
Install a root-owned `/etc/ubc-browser/egress.json` with an exact DNS origin and
address ranges for each approval. An empty list permits globally routable
unicast answers. An explicit list is useful for a dedicated address range or
an internal fixture.

```json
{
  "https://project.example.com": []
}
```

Keep the launcher, policy, unit files, and release configuration writable only
by root. Use standard Podman seccomp and keep the Podman API socket disabled.
Upgrade the launcher, image, release manifest, and configuration together.
Isolation contract version 2 requires the private egress gate at startup.
Activate the launcher with `sudo systemctl daemon-reload` followed by
`sudo systemctl enable --now ubc-browser.socket`.
[systemd socket reference](https://www.freedesktop.org/software/systemd/man/latest/systemd.socket.html)

## Runtime contract

The image uses pinned base images, a dated Debian snapshot, pinned Chromium,
and Python dependencies from `uv.lock`. The launcher supplies a read-only
root filesystem, a private network and PID namespace, dropped capabilities,
and `no-new-privileges`. Cgroups enforce a 1 GiB memory limit with swap
disabled, one CPU, and 256 processes. Podman and systemd enforce a 30-minute
lifetime. The worker uses three private tmpfs mounts:

| Area | In-boundary location | Size |
|---|---|---:|
| Runtime | `/run/ubc` | 128 MiB |
| Profile | `/var/lib/ubc/profile` | 256 MiB |
| Quarantine | `/var/lib/ubc/quarantine` | 64 MiB |

The browser receives no node source mount, node keys, task workspace, core
audit storage, or container-engine socket. Chromium's debugger listens only
within the disconnected network namespace. C02 disables Chromium's internal
sandbox to run with the OCI restrictions and treats Chromium and the worker
as one trust domain. The containment claim rests on Linux and the container
configuration.

The node verifies its local release manifest and the socket's root peer
credentials. The broker uses the digest configured by the administrator and
does not interpret client messages as runtime arguments. A connected
descriptor passed with Podman and crun carries the existing versioned,
length-prefixed browser protocol. There is no browser port or reattach
endpoint. Commands are sequential and bounded to 1 MiB per message. Startup
has a 60-second deadline and a command has a 30-second deadline. Loss of the
parent connection, worker, or broker retires the container and its tmpfs
state. A failed command is terminal and the client never reconnects or
replays it. Lifecycle events include the release and image digest.

### Private egress gate

`PodmanBrowserRuntime.launch` returns the typed `BrowserSession`. It asks for
a subset of the operator's configured origins. The broker starts an
unprivileged gate container from the same pinned image and waits for the gate
to accept the policy before starting the worker. The gate has its own
network, PID, and user namespaces. It receives the operator policy over a
private descriptor. The browser gets a read-only mount containing one
Unix socket. Only the broker and the matching container can traverse a path
to that endpoint. The worker bridges Chromium's loopback proxy port to the
socket. The filesystem capability authenticates the runtime without a
reusable proxy credential or host port.

The gate accepts ASCII DNS names and exact scheme/authority pairs with
optional non-default ports. It resolves only approved destinations using the
gate's resolver, checks every returned address, and opens a socket to a
checked numeric address. Loopback, link-local, multicast, unspecified, and
common IPv6-embedded IPv4 addresses are denied even with explicit ranges.
The gate accepts one GET or HEAD per cleartext HTTP connection. It rejects
request bodies, proxy upgrades, unsupported methods and malformed framing.
HTTPS uses a blind CONNECT tunnel to an approved authority. The tunnel
cannot enforce the TLS virtual host, encrypted HTTP methods or payloads.
An approved HTTPS page can issue writes through its tunnel.
Approve dedicated HTTPS endpoints whose service and address do not relay
traffic or share authority with denied services.

Chromium uses a fixed proxy with QUIC and non-proxied WebRTC UDP disabled.
It starts with a fresh profile and the bundled Stagehand extension. All
external extension traffic follows the same network boundary and gate
policy. The browser IP namespace has only loopback, and the worker cannot
open an IP connection to the gate, a DNS server, or a destination. Loss of
the gate closes the control connection, revokes the session, and retires
both containers. Model-assisted operations, artifact export, persistent
profiles, and takeover remain disabled.

## C03 acceptance

Use a disposable Linux test host. Build and load the base image as above.
Run the fixture setup as root from the repository root:

```shell
image=$(sudo sh tests/setup_egress_fixture.sh localhost/ubc-browser:c03)
```

This creates an internal fixture network with approved HTTP and TLS endpoints
and a denied TCP/UDP contact counter. It installs the fixture egress policy
and derives a test-only image that trusts the generated TLS fixture key. Use
the returned image reference and digest with `UBC_EGRESS_NETWORK=ubc-egress-fixture`
and `UBC_BROWSER_RELEASE=0.1.0-c03-fixture`. Generate a local manifest using
the same release ID and image digest. Restart the launcher socket after
installing the configuration if it is already active. The ordinary base image
uses Chromium's standard TLS verification configuration.

Run this as the ordinary node user and again as root with
`UBC_RUN_ISOLATION_ADMIN=1`. Run with no other isolated browser sessions.

```shell
UBC_ISOLATION_MANIFEST=.ubc/c03-fixture-manifest.json task test-browser-isolation
```

The ordinary run covers real Chromium HTTP/HTTPS navigation, the renderer
fixture, redirects, session separation, and proxy endpoint permissions. The
operator run also checks the denied counter, exercises direct TCP and UDP
attempts and disallowed proxy requests from the browser namespace, and
terminates the parent, worker, runtime, broker, and gate separately. Both
containers must be removed after termination. Discard the fixture host, or
stop the launcher and remove the two fixture containers, fixture network,
configuration and derived image before reusing it.
