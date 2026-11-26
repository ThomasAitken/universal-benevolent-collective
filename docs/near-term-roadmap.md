# Near-term roadmap: hardened local pilot

This roadmap turns the implemented
[single-node prototype](prototype.md) into a local pilot suitable for one
dedicated, low-value project account. It covers the next five calendar weeks
for one part-time maintainer. The order is intentional: no real account is
introduced until browser isolation, artifact handling, session control, and
action recovery have passed their gates.

The immediate next task is the isolated browser runtime. It closes the largest
current gap: the supervised worker and proxy constrain normal model-driven
behavior, but they do not yet withstand a compromised browser or another
malicious process running as the same host user.

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

- Keep each milestone runnable through one local command and local fixtures.
- Extend the existing contracts instead of exposing a parallel privileged API.
- Add failure behavior and adversarial tests before convenience retries.
- Preserve 100% deterministic statement and branch coverage.
- Keep real accounts, internet destinations, and persistent credentials out of
  tests.
- Stop at a milestone gate when the boundary is weaker than its documentation.

## Week 1 — OS-enforced browser and network isolation

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
  explicitly disabled until Week 3.

## Week 2 — Artifact quarantine and release

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

## Week 3 — Session registry, encrypted profiles, and takeover UI

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

## Week 4 — Consequential action ledger and reconciliation

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

## Week 5 — Dedicated low-value account pilot

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

At the end of Week 5, run the complete deterministic, real-browser,
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
