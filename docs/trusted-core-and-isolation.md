# Trusted core and isolated-browser foundation

## Decision and scope

The isolated browser requires Linux with cgroup v2, systemd 254 or later,
local Podman 5.4 or later, and crun. A root-owned systemd socket starts one
broker and one browser container per connection. Sessions with approved
navigation origins also receive a separate egress gate container. Only the
broker uses rootful Podman. The worker and Chromium run as UID/GID 65532 (`ubc-browser`)
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
[Pilot deployment procedure](pilot-runbook.md#intended-linux-deployment)

## Trusted core

The trusted core consists of:

- the run supervisor and mediated research supervisor;
- the executable policy evaluator;
- the core audit writer;
- the pilot quarantine and artifact release service;
- the mediated public-web fetch and search transport and the page reader;
- the root-owned browser launcher;
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

On an Apple Silicon Mac, the pilot runbook covers preparing a Debian guest
for this procedure. [Provision a Linux VM](pilot-runbook.md#provision-a-linux-vm-on-macos)

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
connections. Each browser and egress gate uses a separate 65,536-slot UID/GID
range. Confirm the local image
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
fixture, redirects, session separation, proxy endpoint permissions, and
offline snapshot rendering. The operator run also verifies that the offline
worker has no egress mount or gate, checks that snapshot subresources leave
the denied contact counter untouched, and confirms that stop removes a
worker with a render request in flight. The navigation gate exercises
direct TCP and UDP attempts and disallowed proxy requests from the browser
namespace, then terminates the parent, worker, runtime, broker, and gate
separately. Both navigation containers must be removed after termination.
Discard the fixture host, or
stop the launcher and remove the two fixture containers, fixture network,
configuration and derived image before reusing it.

## Mediated public reads and search

`ubc_node.research.public_web.PublicWebFetcher` is a supervisor-side
component for anonymous `GET` and `HEAD` requests. It accepts HTTPS DNS
names on port 443. Each hop requires all resolved addresses to pass the
public-unicast policy before TCP connects to a checked numeric address.
TLS verifies the original hostname. Loopback, private and link-local
addresses, multicast, and IPv6 translation or transition ranges are excluded
by the address policy. Supply operator-controlled `excluded_networks` CIDRs
for any additional management or internally routed ranges, including
globally numbered addresses. The Lima host and guest management addresses
fall within the default exclusions.

The deadline spans DNS, connection setup, redirects and the final body.
The defaults are 15 seconds, five redirects and a 2 MiB response body.
The fetcher closes redirect responses without consuming their bodies and
accepts `identity`, `gzip` and `deflate` encodings, bounding both wire and
decompressed bytes to the body limit and rejecting unsupported or corrupt
encodings. Successful reads return the normalized request and effective URLs,
redirect history, HTTP status, content type, UTC retrieval time and bounded
bytes. The bytes are untrusted input to snapshot rendering and quarantine.

The separate `SearchProvider` interface has an exact-query `FixtureSearch`
for offline runs alongside live `BraveSearch` and `SerperSearch` adapters.
Construct a live adapter with an operator-supplied key such as
`BraveSearch(os.environ["BRAVE_SEARCH_API_KEY"])` or
`SerperSearch(os.environ["SERPER_API_KEY"])`. Each adapter sends its key only
to its fixed HTTPS search endpoint (`api.search.brave.com` or
`google.serper.dev`) and refuses redirects. Each call fetches one batch within
ten seconds and 512 KiB and returns up to 20 bounded hits. Hit URLs are
candidates and must pass the fetcher's address checks when opened.
[Brave Search API reference](https://api-dashboard.search.brave.com/api-reference/web/search/get) ·
[Serper API](https://serper.dev/)

## Offline page snapshots

The trusted node calls `PageReader(fetcher, runtime).read(url)` to acquire and
render a public page. The reader accepts HTML, XHTML, and plain text up to
512 KiB. Successful page responses get a fresh Chromium container with no
IP network access or egress socket. The launcher selects this mode when the
startup frame has an empty origin list. The worker accepts one snapshot
command and rebuilds the document from a static HTML allowlist. The generated
document's Content Security Policy blocks scripts and subresources. Chromium provides the title,
visible text and inert link labels, with a 32,000-character text limit and a
48-link limit. Links resolve against the mediator's final URL and are
candidates for a separate checked fetch. Pages that depend on site scripts
may yield sparse snapshots. Use `read(url, link_query="202610")` to match a
case-insensitive substring in link text or the original target before
applying the 48-link limit. The snapshot records the query, and
`links_truncated` refers to the matching links.

A non-2xx status or Cloudflare's `cf-mitigated: challenge` header produces a
`PageHTTPError` with retrieval metadata before Chromium starts. The error
indicates whether the challenge header was present. Discovery can record the
access failure and look for a documented public data endpoint or another
source. Other interstitials can still arrive as successful HTML responses and
should be assessed as part of source review.
[Cloudflare challenge response detection](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)

A live, interactive Chromium session can sometimes complete a challenge
because it can make follow-up requests, execute verification scripts, store
a clearance cookie, and accept human input when prompted. The snapshot worker
receives only the fetcher's response. If that response is a challenge, the
requested page content never reaches the renderer. A live-browser fallback
would be a separate capability with authority to run publisher code, retain
browser state, and make requests beyond the mediator's anonymous GET and
HEAD. An automated browser could still be challenged. For the NEM demo,
AEMO publishes the relevant price and demand data separately on NEMWeb.
[Cloudflare interstitial challenges](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/) ·
[AEMO price and demand data](https://www.aemo.com.au/energy-systems/electricity/national-electricity-market-nem/data-nem/aggregated-data)

The result includes requested and effective URLs, redirect history, status,
content type, retrieval time, and the byte count and SHA-256 of the
mediator's decompressed response body. Keep the returned text and links
tagged as untrusted evidence. The trusted owner may call `await reader.stop()`
from another coroutine to cancel a fetch or render and permanently stop
that reader. Closing the browser control connection triggers the launcher's
container cleanup. To deploy this mode, install the matching broker and
browser image from the same release.

## C04 download quarantine

The operator selects the source, obtains a completed local file and records
the SHA-256 of the complete approved version before import. Use a publisher
checksum or a digest from a verified completed acquisition. The store checks
the expected digest even when a truncated file is still valid CSV. The input
file and its parent directories should be under operator control. The
browser has no quarantine export operation in this release.

Use a dedicated root on a local filesystem outside task workspaces, owned by
the importing node account with mode `0700`. Its parent must also be under
operator control. The gate accepts one format up to 8 MiB, with a portable
`.csv` basename and ASCII LF-delimited records:

```csv
interval_utc,region,price
2026-01-01T00:00:00Z,NSW1,-12.30
```

Each row uses a UTC calendar timestamp at second precision, a National
Electricity Market region code, and a signed decimal price with up to six
integer and six fractional digits. The fixed fields exclude names, email
addresses, free text and spreadsheet formulas. An operator provides 1 to 32
exact canary values of 1 to 256 bytes each in an operator-owned, regular
mode-`0600` file, one per line. This policy screens the admitted format and
known canaries. Source classification, publication rights, and statistical
validation belong to the programme's source and analysis records.

Use an operator-managed ClamAV daemon with maintained signatures, a separate
unprivileged service account, and a filesystem socket whose directory and
permissions restrict access to the node and scanner. Give the daemon
private temporary storage and keep the node store, keys, and audit storage
outside its access. Use service-level CPU, memory and process limits.
The client submits only bytes, caps the reply, and applies a 60-second
deadline. Configure at least the following scan limits and set a service
memory budget sufficient for the installed signature database.
[ClamAV daemon protocol](https://docs.clamav.net/manual/Usage/ClamdProtocol.html)

```text
StreamMaxLength 16M
MaxFileSize 16M
MaxScanSize 16M
AlertExceedsMax yes
ScanArchive no
```

```shell
uv run ubc quarantine --store /path/to/private-store import /path/to/completed.csv \
  --filename prices.csv --project electricity --task baseline --session baseline-1 \
  --source https://data.example/prices.csv --expected-sha256 "$approved_digest" \
  --scanner-socket /run/clamav/clamd.ctl --canaries-file /path/to/private-canaries
uv run ubc quarantine --store /path/to/private-store recover
```

Import prints a receipt ID, SHA-256, and byte count. Trusted node code can use
`DownloadQuarantine.read_release(project, receipt)` to fetch the released
bytes and `DownloadQuarantine.inspect_acquisition(project, receipt)` to read
typed provenance and ordered audit events without querying SQLite directly.
The operator-owned SQLite journal records scope, source, timestamps, digest,
byte count, scan policy and decisions without putting file contents or canary
values into event rows; keeping artifacts and events in one database commits
release content, provenance, and the release audit event atomically. Recovery
runs explicitly or before the next import, expires interrupted attempts and
removes their staging directories. The operator retains responsibility for the
original local input file.

Run `uv run pytest tests/test_quarantine.py --no-cov` for the deterministic
gate. On the pilot deployment, certify the scanner using a disposable
signature database and a benign CSV fixture signature with the opt-in gate:

```shell
UBC_CLAMD_SOCKET=/run/clamav/clamd.ctl \
  UBC_CLAMD_FLAGGED_CSV=/path/to/flagged-fixture.csv \
  task test-quarantine-scanner
```

Verify the node releases a clean fixture, rejects the matching fixture, and
rejects imports with the daemon stopped. Verify that the scanner identity
cannot traverse the store, read node keys or write node audit files. Repeat
the C03 acceptance on the intended launcher before live-source work.

## C05 browser research

Run from the source tree on a Linux node with the installed browser launcher
and a matching manifest. Review the anonymous sources and their rights for
retaining page text and sharing it with the configured model provider. Use
`OPENAI_API_KEY` in the trusted node environment. The browser does not
receive provider credentials. Supply the exact canonical HTTP(S) locators
with explicit paths, including `/` for a root page. User info, queries and
fragments are excluded. Use the final locator for a source that redirects.
Replace the example host with an approved source from the launcher's egress
policy.

```shell
uv run ubc research \
  --manifest .ubc/pilot-manifest.json \
  --journal .ubc/research/prices-run-1.jsonl \
  --task prices-source-review \
  --objective 'Find the market interval and published price definition.' \
  --source glossary https://market.example/glossary \
  --source release-notes https://market.example/release-notes
```

A run uses a fresh profile and one active tab. The command accepts up to 16
inventory entries and allows up to eight model calls, seven navigations and
120 seconds including session startup. Press Ctrl-C to cancel. Use a new
journal path to retry. The command creates it with owner-only access. Inspect
`browser_action_completed` events for evidence and `run_finished` for the
draft or failure. Each observation retains up to 8,000 snapshot characters
and a SHA-256 of that retained text. Check model citations and findings
against the journal before reuse.

The inventory limits the model's requested top-level destination and the
locators accepted as evidence. Chromium can still load resources or follow
redirects at origins admitted to the session by the egress gate. The same
egress restrictions apply to page-initiated HTTPS traffic. C04 provides the
operator path for releasing CSVs into analysis.
[HTTPS boundary](#private-egress-gate)

### C05 acceptance

Build the current image and repeat the C03 gate with the local fixture and
matching manifest. [C03 acceptance](#c03-acceptance)

Run the ordinary node and operator gates before using the intended
deployment with public sources. The C05 cases use
a deterministic structured model provider and no API key. They verify the
supervisor through the isolated worker, evidence labels in the model prompt,
retirement after normal completion and policy failure, an injected instruction
that causes a denied proposal, and a server redirect that yields no evidence.
The operator gate confirms zero denied TCP and UDP contacts. It also retains
the C03 channel failure and container cleanup checks.
