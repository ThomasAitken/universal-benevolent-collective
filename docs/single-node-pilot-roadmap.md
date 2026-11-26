# Single-node pilot roadmap

**Document status:** Active living work tracker  
**Delivery model:** Direct, ordered commits to `main`  
**Last updated:** 2026-11-26  
**Current focus:** C03 — authenticated proxy-only HTTP/HTTPS egress  
**Pilot gate:** One dedicated, low-value project account after C01–C08 pass

This roadmap turns the implemented
[single-node prototype](prototype.md) into a local pilot suitable for one
dedicated, low-value project account. Progress is governed by dependency and
evidence gates. No real account is introduced until browser isolation,
artifact handling, session control, and action recovery have passed their
gates.

C02 implements the isolated browser runtime and its acceptance gate. The
prototype's supervised worker and proxy remain available for development. The
isolated runtime is offline until C03 adds the separate egress boundary.

This boundary also protects node integrity. Web content and Chromium are
high-exposure inputs; compromising them must not let an attacker replace the
supervisor, weaken executable policy, forge core audit events, read node
identity keys, or change the constitution version and digest bound to the node
release. Isolation keeps the browser outside that trusted core.

Isolation is containment, not proof that the host is running an official
build. Signed reproducible releases, release manifests, node identity, and
later platform attestation provide progressively stronger evidence about the
code actually running. Trust tiers and server-side authorization must remain
safe when that evidence is weak or unavailable.

## Working rules

- Update commit and milestone status here as work starts, lands, blocks, or
  changes scope. This document is the operational source of truth for the
  single-node pilot.
- Keep each milestone runnable through one local command and local fixtures.
- Extend the existing contracts instead of exposing a parallel privileged API.
- Add failure behavior and adversarial tests before convenience retries.
- Preserve 100% deterministic statement and branch coverage.
- Keep real accounts, internet destinations, and persistent credentials out of
  tests.
- Stop at a milestone gate when the boundary is weaker than its documentation.

## Work tracker

Status values are `queued`, `in_progress`, `blocked`, and `done`. A unit is
`done` only when its commit is on `main`, its gate passes, and this document
reflects the resulting behavior and any residual limitation.

| Unit | Commit | Status | Depends on | Result |
|---|---|---|---|---|
| SNP-01 | C01 | done | Prototype baseline | Verified trusted-core manifest, pinned non-root browser image, isolation contracts, and fixture topology |
| SNP-02 | C02 | done | C01 | Offline Chromium isolation implemented and verified via Linux guest acceptance |
| SNP-03 | C03 | queued | C02 | Authenticated proxy-only HTTP/HTTPS egress with controlled DNS and bypass resistance |
| SNP-04 | C04 | queued | C01 | Downloads remain quarantined until deterministic scanning and promotion |
| SNP-05 | C05 | queued | C02 | Encrypted, registered, expiring, and revocable project browser profiles |
| SNP-06 | C06 | queued | C05 | Authenticated same-session takeover and active-session UI |
| SNP-07 | C07 | queued | C01 | Durable R2 attempt ledger, idempotency, fencing, and reconciliation |
| SNP-08 | C08 | queued | C03, C04, C06, C07 | One integrated hardened local workflow and operator runbook |
| SNP-09 | C09 | queued | C08 | Dedicated low-value account pilot, adversarial suite, and go/no-go report |

## Commit plan

These commits are intentionally large integration units. Each one may contain
contracts, implementation, fixtures, migrations, tests, documentation, and
operator tooling. The goal is not minimal diffs; it is a sequence of coherent,
reversible states where `main` remains runnable and accurately documented.

### C01 — `feat(security): define the trusted core and isolated browser foundation`

- define the constitution-bound trusted core and release-manifest schema;
- bind supervisor, executable policy, constitution digest, dependency lock,
  audit writer, node-key interface, and browser image digest;
- select and document the primary isolation backend and unsupported platforms;
- add browser image build, pinned dependencies, fixture topology, and
  isolation/runtime contracts;
- add conformance tests proving browser-facing interfaces contain no host path,
  node key, raw debug endpoint, or core audit authority.

This commit is the common contract point for the three parallel implementation
lanes that follow.

### C02 — `feat(browser): move Chromium into the enforced security boundary`

- run the browser worker and Chromium as non-root with a read-only base,
  isolated writable paths, resource limits, and no host container socket;
- keep CDP, browser profile, and worker internals private to the boundary;
- replace the current inherited-process assumptions with an authenticated,
  bounded control transport;
- make parent, worker, runtime, and control-channel loss terminal and
  restart-safe;
- retain the typed `BrowserSession` API and run the approved-page and denied-redirect
  scenario in isolated Chromium.

C02 runs only the bundled fixture in a disconnected network namespace. Its
control connection uses a root-owned Linux Unix socket and a dedicated
container connection with no published ports. C03 adds the external network
path. Artifact export, model-assisted browser calls, persistent profiles, and
takeover stay disabled until their prerequisites are available.

The [installation and acceptance procedure](trusted-core-and-isolation.md#c02-acceptance)
passed in a disposable Linux rehearsal under Docker Desktop as both the node
user and the root test operator. `task check` passed at 100% statement and
branch coverage. A Linux guest running the intended systemd, Podman, crun, and
Chromium stack is sufficient for C02 acceptance.

### C03 — `feat(egress): enforce authenticated proxy-only HTTP and HTTPS`

- make the egress gate the browser boundary's only network route;
- authenticate the runtime to the gate without putting credentials in prompts,
  events, artifacts, or command lines;
- resolve DNS at the gate and allow HTTP or HTTPS only for normalized approved
  authorities and methods;
- support HTTPS `CONNECT` without content interception;
- disable or deny direct sockets, alternate DNS, QUIC, WebRTC, extension
  traffic, and other bypass paths;
- prove denied targets receive zero requests under normal, redirect, renderer,
  and worker-compromise fixtures.

### C04 — `feat(artifacts): quarantine, scan, and promote browser downloads`

- implement the artifact state machine and per-session quarantine;
- stream size and digest checks before a file is complete;
- add filename, type, archive, executable, malware, secret, and sensitive-data
  checks;
- promote clean content by digest only after every scanner allows it;
- make scanner failure, cancellation, crash, rejection, expiry, and cleanup
  fail closed and auditable.

This commit can be implemented alongside C02 and C03 after C01 fixes the
artifact and filesystem boundaries.

### C05 — `feat(sessions): add encrypted project profiles and session registry`

- add the local active/recent session registry and lifecycle records;
- encrypt project profiles with OS-keystore-wrapped per-project keys;
- expose decrypted state only inside the isolated browser boundary;
- enforce profile locking, origin binding, idle/absolute expiry, revocation,
  rotation, crash cleanup, and non-restoration of expired authority;
- add operator-visible metadata without exposing cookies or credentials.

This can proceed in parallel with C03, C04, and C07 once C02 establishes the
runtime and storage boundary.

### C06 — `feat(takeover): add authenticated same-session operator control`

- add a short-lived, single-use handoff capability and authenticated loopback
  UI;
- provide same-session view/input, visible capture-paused state, explicit
  return/decline, expiry, and revoke;
- isolate operator frames, input, credentials, and verification codes from
  model context and ordinary telemetry;
- survive UI refresh and fail closed on UI, worker, or authorization loss.

### C07 — `feat(actions): add durable consequential-action reconciliation`

- persist intent, policy, approval, dispatch, acknowledgement, observed effect,
  and reconciliation separately;
- bind idempotency key and fencing token to task, action, capability, account,
  resource, payload, approval, and expiry;
- implement terminal and ambiguous states including `unknown_outcome`;
- prevent blind replay after crash, timeout, cancellation, partition, or stale
  worker activity;
- add a provider-fixture contract for idempotent execution and read-after-write
  reconciliation.

This lane depends only on C01 and can proceed alongside the browser, artifact,
and session work.

### C08 — `feat(pilot): integrate the hardened single-node workflow`

- connect isolated browsing, gated egress, quarantine, encrypted sessions,
  takeover, policy, approval, action recovery, evidence, and reporting;
- add one-command operator setup, diagnostics, run, pause, revoke, resume, and
  cleanup;
- add research, software, and long-form writing fixtures through the same
  boundaries;
- run crash/restart, secret-canary, prompt-injection, malformed-input, and
  incident-revocation scenarios as one acceptance suite;
- produce the operator runbook and residual-risk register.

### C09 — `test(pilot): complete the dedicated low-value account gate`

- add the project-account record and manually approved service configuration;
- run the read-only and reversible-draft pilot sequence;
- prove expiry, revoke, restart, artifact, audit, and no-secret guarantees
  against the selected service;
- inspect all prompts, events, artifacts, profiles at rest, quarantine records,
  logs, and crash output;
- record the go/no-go decision before any second account or external-write
  capability.

## Parallel execution

Work can advance concurrently, but commits land on `main` in dependency-safe
order. Parallel work should avoid changing a shared contract after a dependent
lane has started; when that is unavoidable, update C01's contract first and
realign the affected unit before integration.

```text
prototype baseline
       |
      C01  trusted core and contracts
     / | \
    /  |  \
 C02  C04  C07
  |    |    |       browser       artifacts      action reliability
  +-> C03   |       egress
  |
  +-> C05 -> C06   sessions and takeover
    \    |    /
     \   |   /
       C08          integrated hardened workflow
        |
       C09          low-value account pilot and closeout
```

| Lane | Units | May run alongside | Convergence requirement |
|---|---|---|---|
| Trusted core and containment | C01 → C02 → C03 | C04 and C07 after C01 | C03 gate passes before C08 |
| Artifact ingress | C04 | C02, C03, C07 | Released artifacts use C01 filesystem contract |
| Sessions and operator control | C05 → C06 | C03, C04, C07 | Requires C02 runtime boundary; C06 passes before C08 |
| Consequential action reliability | C07 | C02–C06 | Uses C01 identity/audit contracts; passes before C08 |
| Assurance and documentation | Every commit | All lanes | Tests and docs land in the same commit as behavior |

## Direct-to-main commit discipline

- Land a commit only when every dependency in the tracker is already on `main`.
  Parallel lanes may prepare work concurrently and land at their earliest safe
  point; numerical IDs identify the planned units rather than requiring a
  strictly serial landing order.
- Every commit includes its relevant tests, fixtures, documentation, migration
  behavior, and cleanup path. Do not leave a knowingly permissive temporary
  state for a later commit.
- Incomplete capabilities remain absent or deny by default. A commit may land a
  disabled capability only when the disabled state is explicit, tested, and
  accurately documented.
- Run focused tests while developing, then `task check` before every commit.
  Commits touching Chromium, isolation, or egress also run the applicable
  real-browser and adversarial acceptance tasks.
- Treat each commit as a rollback unit. Avoid combining unrelated formatting,
  dependency churn, or speculative refactors with the named outcome.
- Update the tracker status and current focus in the same commit. If scope or
  order changes, update this plan before implementing against the new
  assumption.
- Never weaken a landed gate merely to keep delivery moving. Record the unit as
  `blocked`, keep the previous safe behavior, and redirect effort to the
  blocking boundary.

## Milestone A — OS-enforced browser and network isolation (C01–C03)

### Outcome

Run the browser worker and Chromium in a rootless container, lightweight VM, or
separate OS security context whose network can reach only the egress gate. The
model-facing process must not be able to reach Chrome's debugging endpoint,
profile, or network namespace. Conversely, the browser boundary must not be
able to modify or impersonate the constitution-bound node core: supervisor,
policy evaluator, audit writer, release metadata, or node identity keys.

### Deliverables

- reproducible browser-worker image with read-only base, non-root user, dropped
  capabilities, process/memory/time limits, and no host container socket;
- explicit trusted-core manifest covering the supervisor, executable policy,
  constitution version/digest, audit component, node-key interface, browser
  image digest, and dependency lock;
- read-only code and policy mounts, isolated writable paths, and no browser
  access to node keys or core audit storage;
- private authenticated control channel between the node and worker;
- debugging endpoint reachable only inside the browser security boundary;
- proxy-only network path enforced outside Chromium;
- controlled DNS resolution at the egress gate;
- HTTPS `CONNECT` allow-listing by normalized destination authority without
  intercepting application content;
- explicit denial of QUIC, WebRTC bypass, extension egress, and unsupported
  schemes;
- equivalent local-fixture behavior on the primary development platform, with
  other platforms marked unsupported rather than silently weakened.

### Gate

Run the integrated gate on the intended pilot deployment before using a real
account. A Linux VM is suitable when its host and management plane are under
trusted administration. Verify that the ordinary node and model-facing
processes lack engine, host management, and browser runtime access beyond their
assigned capabilities.

- approved HTTP and HTTPS fixtures work through the gate;
- denied redirect, direct socket, alternate DNS, QUIC, and proxy-bypass tests
  produce zero contact at denied servers;
- a same-user process cannot use the control channel, debugging endpoint, or
  proxy merely by discovering an address or port;
- a compromised fixture page, renderer, or browser worker cannot alter node
  code or policy, change the bound constitution digest, read node identity
  keys, or append a forged event to the core audit journal;
- the release manifest and browser image digest are recorded with the run,
  without claiming that self-reported values prove an untrusted host is honest;
- parent, worker, gate, and container failure all revoke the session without
  action replay;
- the human-takeover path either works through the isolated session or remains
  explicitly disabled until C06 passes.

## Milestone B — Artifact quarantine and release (C04)

### Outcome

Treat every browser download as untrusted ingress. A downloaded file must not
enter a task workspace or model context until a deterministic quarantine
workflow releases it.

### Deliverables

- artifact states `receiving`, `quarantined`, `scanning`, `released`,
  `rejected`, and `expired`;
- per-session quarantine directory outside task workspaces;
- streaming size limit and SHA-256 while receiving;
- filename normalization, regular-file checks, media-type detection, archive
  depth/count/expanded-size limits, and executable-content policy;
- pluggable malware scanner with fail-closed unavailable/error behavior;
- secret-canary and sensitive-data scan before release;
- content-addressed promotion into the task workspace after all checks pass;
- audit events for receipt, scan decisions, promotion, rejection, and cleanup,
  without storing file contents in the journal.

### Gate

- clean fixture files are released with stable provenance;
- malware fixture, archive bomb, oversized file, traversal name, symlink,
  scanner failure, and secret canary are rejected;
- cancellation and crashes cannot turn a partial or unscanned file into a
  released artifact;
- rejected and expired quarantine data is recoverably cleaned.

## Milestone C — Session registry, encrypted profiles, and takeover UI (C05–C06)

### Outcome

Make authenticated browser sessions visible, expiring, revocable, and usable
through a local authenticated operator surface without exposing credentials to
the model or ordinary telemetry.

### Deliverables

- local registry of active and recently ended browser sessions with project,
  allowed origins, creation, last use, idle/absolute expiry, state, and
  revocation reason;
- per-project profile encryption using an OS-keystore-wrapped key, with
  decrypted state available only inside the isolated browser boundary while
  active;
- authenticated loopback takeover UI using a short-lived, single-use handoff
  capability;
- same-session view and input, explicit return/decline, visible capture-paused
  state, and independent revoke control;
- crash-safe profile lock, expiry, cleanup, revocation, and rotation behavior;
- no password, verification code, cookie, storage value, keystroke, or takeover
  frame in prompts or ordinary audit events.

### Gate

- an unauthenticated local process cannot list, view, control, or revoke
  sessions;
- takeover survives UI refresh and returns only the minimum redacted result;
- expiry and revoke terminate control, egress, and profile use promptly;
- restart never silently restores an expired or revoked capability;
- secret-canary tests cover prompts, logs, screenshots, profiles exported at
  rest, crash reports, and task artifacts.

## Milestone D — Consequential action ledger and reconciliation (C07)

### Outcome

Give approved R2 actions durable identities and explicit recovery semantics so
a crash cannot cause blind replay or falsely report success.

### Deliverables

- durable action records separating intent, policy, approval, dispatch,
  provider acknowledgement, reconciliation, and observed effect;
- idempotency key and attempt/fencing token bound to task, action, capability,
  resource, approval, and expiry;
- states for `prepared`, `dispatched`, `confirmed`, `denied`, `failed`,
  `unknown_outcome`, and `reconciled`;
- provider adapter contract for idempotent execution or read-after-write
  reconciliation;
- restart behavior that never retries `dispatched` or `unknown_outcome`
  actions without provider evidence or renewed human direction;
- audit rendering that preserves accountability without persisting secrets or
  sensitive request bodies.

### Gate

- crash-before-dispatch, crash-after-dispatch, lost response, duplicate reply,
  timeout, cancellation, expired approval, and zombie-worker tests pass;
- a confirmed effect is not duplicated across restart;
- an ambiguous effect stops in `unknown_outcome` and is visible to the operator;
- approval cannot be reused for a changed payload, resource, account, or
  attempt.

## Milestone E — Integration and dedicated low-value account pilot (C08–C09)

### Outcome

Exercise the complete local boundary with one service account created and
owned by the project, not an ordinary personal account.

### Preparation

- choose a service whose terms permit the intended human-supervised automation;
- record owner, purpose, allowed origins/tasks, recovery human, recovery
  channel, authentication mode, expiry, and revocation procedure;
- create the account manually, enable the least privilege available, and avoid
  personal mail, contacts, payment methods, private history, and reused
  credentials;
- define an immediate manual kill switch and account deletion procedure.

### Pilot sequence

1. Human signs in through takeover and explicitly permits profile persistence.
2. Agent performs an R0 read-only task and produces cited evidence.
3. Agent prepares one reversible R1 draft without external publication.
4. Operator revokes the session and confirms further access fails.
5. Supervisor restarts and proves revoked authority is not restored.
6. Review all prompts, events, artifacts, quarantine records, and crash output
   for canaries or account data outside the intended boundary.

### Gate

- no reusable credential, cookie, token, recovery value, or private takeover
  content appears outside the encrypted isolated profile;
- destination, capability, approval, artifact, expiry, and revocation controls
  behave as specified against the real service;
- no public post, message, account change, upload, payment, or recovery action
  occurs;
- the pilot has an operator-reviewed report, residual-risk list, and go/no-go
  decision before any second account or external-write capability is added.

## Closeout and next decision

After C08 and C09 pass, run the complete deterministic, real-browser,
container-escape/egress, secret-canary, prompt-injection, crash/restart, and
incident-revocation suites. Obtain a focused independent review of the local
browser, profile, artifact, and action boundaries.

If the gates pass, the next work is the capacity-limited multi-node alpha:
identity, enrollment, project/task assignment, leases and fencing, encrypted
artifact storage, dashboard, capability broker, and a small private participant
cohort. If a gate does not pass, keep the system local and fixture-only while
that boundary is corrected.

## Explicitly outside this roadmap

- public self-service enrollment;
- ordinary personal or high-value accounts;
- autonomous public posting or messaging;
- money movement;
- confidential or regulated projects;
- multiple external integrations;
- private cross-project memory;
- claims of general browser or internet support beyond the tested destination
  and protocol set.
