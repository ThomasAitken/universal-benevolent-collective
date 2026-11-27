# Single-node research pilot roadmap

- **Current focus** · C04, C06, and C07 deployment acceptance
- **Delivery model** · Dependency-ordered commits to `main`
- **Updated** · 2026-11-27

The pilot passes when a human reviewer can reproduce a result from one living
research programme and inspect how a fresh run handled a changed source. The
programme produces a versioned dataset, a runnable analysis, a thesis proposal
or reasoned no-change decision, and a forecast that can be resolved and scored.
The pilot uses public, account-free research inputs and local fixtures. A
controlled live-source run follows fixture acceptance on the intended Linux
deployment. [Product roadmap](product-and-architecture-spec.md#14-delivery-roadmap)

## Pilot programme

Use a bounded electricity-market question as the working example. In one
Australian market region, measure the frequency of negative wholesale price
intervals, describe the circumstances in which they occur, and forecast an
observable outcome in a subsequent release. Freeze the region, interval and
price definitions, observation window, handling of missing or duplicate
intervals, data vintage, and publication rights in the charter. The descriptive
analysis can motivate follow-up work on causes. A causal claim needs its own
design and review.

Use two documented versions of a small approved public dataset or deterministic
fixtures modelled on them. Include a source change that exposes a plausible
follow-up question and a release that can resolve a binary forecast. The first
run receives only the information available at its cutoff. A live forecast
remains pending until its named outcome is available.

### Cycle 1 — Establish a baseline

1. Seed a programme and a small set of eligible project charters. Let a run
   choose a project within the human's admission, opt-in, and budget rules.
2. Retrieve a pinned thesis revision, collect evidence via the isolated
   browser, and acquire an approved dataset via quarantine. Record source
   locator, version or retrieval time, digest, rights, and the acquisition
   decision.
3. Produce a dataset release and a reproducible analysis. Save a manifest
   for source and input versions, transformation and analysis code,
   environment, parameters, output digests, and diagnostics.
4. Submit a thesis draft, revision proposal, or reasoned no-change decision.
   Give a human reviewer evidence locators and a diff against existing
   knowledge. Record the review decision, objections, and any corrections.
5. Freeze a binary forecast question, deadline, data cutoff, resolution
   source and rule, and Brier scoring method. Record a forecast and a simple
   baseline with the same cutoff before revealing the fixture outcome.

### Cycle 2 — Update from fresh context

1. Start a fresh run from programme records. Present a new source revision,
   correction, or release and use pinned dependencies to identify the affected
   dataset, analysis, forecast, and thesis proposal or revision.
2. Rerun the affected work with the updated input. Record what changed,
   whether prior findings still hold, and why any output was left alone.
   Retain the previous versions and their inputs.
3. Resolve and score the fixture forecast and baseline against the specified
   outcome. Record the forecast error and any proposed change to the model or
   next question. Keep predictive performance distinct from evidence of cause.
4. Make the update reviewable. A second run can trace evidence or look for
   counterexamples before seeing the author's conclusions. The human reviewer
   records whether to accept the update, request changes, or leave the prior
   assessment current.
5. Have the agent propose a follow-up project if it identifies a material
   gap. The proposal belongs in the programme queue for human admission.

## Work tracker

A unit is `done` when its commit is on `main`, its gate passes, and the
tracker records the resulting behaviour and residual limits.

| Unit | Status | Depends on | Result |
| --- | --- | --- | --- |
| C01 | done | Prototype baseline | Constitution-bound trusted-core manifest, pinned non-root browser image, isolation contracts, and fixture topology |
| C02 | done | C01 | Offline Chromium in the Linux isolation boundary with terminal session failure |
| C03 | done | C02 | Private per-session egress gate with HTTP/HTTPS Chromium and zero-contact fixtures |
| C04 | implemented, deployment gate pending | C03 | Operator-imported, quarantined, scanned, content-addressed pilot CSVs |
| C05 | done | C03 | Inventory-scoped Chromium evidence with provenance and a supervisor audit |
| C06 | implemented, deployment gate pending | C01, C04 | Local research catalogue, isolated price analysis, thesis review, and forecast resolution |
| C07 | fixture implemented, deployment and live acceptance pending | C05, C06 | Restartable two-cycle fixture, operator reviews, provenance checks, and forecast scoring |

Public-page evidence and operator CSV acquisition can be accepted separately.
Any future browser artifact handoff must use the C04 quarantine gate.

## C01–C03 — Trusted core, isolation, and egress

The Linux acceptance runs verified the trusted-core and browser image
contracts, offline Chromium, and the separate egress gate. C02 and C03 passed
in a disposable Linux guest with the intended systemd, Podman, crun, and
Chromium stack. C03 passed as both ordinary node user and root operator.
The denied fixture recorded zero TCP or UDP contacts, and loss of the parent,
worker, runtime, broker, or gate ended the session and retired both containers.
`task check` passed at 100% statement and branch coverage.
[Linux setup and acceptance](trusted-core-and-isolation.md#c03-acceptance)

Repeat the applicable acceptance on the intended pilot deployment before
connecting to live research sources. Verify that browser-facing processes
lack host engine and management access, node keys, and audit authority, and
that the browser has only its private control and proxy paths. The browser
runs in a disconnected IP namespace. The operator sets the maximum origin
policy, and the node requests a subset for each session. The manifest binds
expected code, policy, browser image, and Constitution digest. Release signing
and runtime assurance have separate checks for independently operated nodes.

The egress gate permits cleartext GET and HEAD at approved authorities and
scopes blind HTTPS `CONNECT` to approved destinations. HTTPS virtual hosts,
methods, and payloads inside the tunnel are outside its visibility. Approve
only pilot destinations whose service behaviour is suitable for the allowed
work. Document required redirects and dependencies and avoid destinations
that relay traffic or share authority with denied services. If a source
requires stronger method enforcement, use a separately mediated retrieval path
or leave it out of the pilot.
[HTTPS boundary](trusted-core-and-isolation.md#private-egress-gate)

## C04 — Quarantine, scan, and release downloads

The first pilot accepts only the documented electricity-price CSV format.
The operator imports a completed file with an expected SHA-256 from the
approved source record. The node streams it into private per-session
quarantine, validates its name, type and digest, checks configured secret
canaries, and requires a clean verdict from a private ClamAV daemon.
The fixed schema excludes free-text and formula fields. Archives and
executables are rejected before parsing. Treat every release as untrusted
analysis input.

The store records receipt, quarantine, scanning, release or rejection,
expiry, and cleanup. Release commits content, provenance, and its audit
event together. Each import retains its own project, task, session and
source receipt, even when its bytes already exist by digest.

Fixture acceptance covers clean scoped release, oversize, traversal,
symlinks, non-regular files, a truncated CSV prefix, a disguised archive,
executable and formula content, a secret canary, malware and scanner failure
verdicts, cancellation, process death during scanning, and idempotent recovery.
A real ClamAV daemon in a disposable, network-disabled Linux container
released the clean CSV, flagged a benign fixture signature, and caused
import rejection after shutdown. Its scanner account could not traverse the
node store. Run the deployment gate with the configured scanner and repeat
applicable C03 acceptance before pilot use. Browser artifact export stays
disabled until an explicit handoff can use the quarantine gate.
[Operator procedure and deployment gate](trusted-core-and-isolation.md#c04-download-quarantine)

## C05 — Isolated browser research

The model selects a locator from a small operator-approved inventory. The
supervisor authorizes one combined navigation and structured page snapshot at
a time through the existing typed Linux worker. The pilot needs one active
tab and no page mutations. Additional page controls need a specific source
requirement and containment evidence. The session allows only inventory
origins, and the launcher checks them against the operator's egress policy.

Accept evidence only when navigation returns a successful HTTP response, the
reported locator matches the inventory, and the same tab and locator are
present after capture. Journal the source and evidence identifiers, session,
origin, locator, UTC retrieval time, HTTP status, snapshot text and its digest.
The model receives the observation as untrusted data and can cite its evidence
identifier in draft findings. Bound model calls, browser actions, wall time,
snapshot characters and tab count. Retire the disposable session on every run
exit.

The research surface exposes URL selection and completion. Submission, upload,
authentication, screenshots, raw CDP, arbitrary script, browser storage and
artifact export have no model-accessible operation. Browser downloads stay
disabled. Operators acquire approved CSVs through C04.

`task check` passed with 100% statement and branch coverage. The installed
launcher gate passed in a disposable Linux environment with systemd, Podman,
crun, and Chromium. Six ordinary-node and ten root-operator cases passed.
The runs exercised evidence capture, an injected proposal, denied redirects,
fresh tab handles and storage, and session cleanup after channel loss.
The denied fixture registered zero TCP or UDP contacts. Repeat the gate on
the intended pilot deployment before connecting to live sources.
[Operator command and acceptance gate](trusted-core-and-isolation.md#c05-browser-research)

## C06 — Research records, analysis, and review

The pilot uses a private local SQLite catalogue so releases, dependency links,
and supervisor-format audit events can commit together. The operator admits
charters and records owner opt-in for up to eight choices. A choice yields
one task with a fixed project and budget. Follow-up suggestions remain in the
programme queue until admitted. A task revocation blocks new accepted results,
including an analysis that finishes after revocation.

The analysis boundary accepts a project- and task-scoped C04 receipt and the
charter's region and observation window. One reviewed five-minute price method
runs in a fresh Linux container with a digest-pinned Python image, no network,
no host mounts, a read-only root, and explicit resource limits. Its dataset
retains exact CSV line lineage. A manifest pins source, code, tooling,
parameters, output digest, rights through the dataset release, and quality
checks. External research code is disabled. Use a fixed method for the pilot
so reviewers can reproduce the result and focus on evidence and interpretation.

The thesis workflow handles a pinned local revision, a new draft, a revision
proposal, or a reasoned no-change result. Proposals use the v1 schema with
pinned evidence locators. A human records an independent decision after
reviewing the diff. Local approval creates an immutable revision with its
content digest and keeps earlier revisions available. Model callers have
proposal authority and public publication remains separately gated.

A binary forecast pins the question, cutoff, deadline, named source version,
resolution rule and Brier method. The submission records a probability and a
constant 0.5 baseline at the same cutoff. A later named dataset can resolve it.
A direct source correction appends a score while preserving the original
question, submission, and resolution. A fresh process can traverse the
old source's dependants and retrieve the exact prior artifacts.

The component fixtures cover reruns, malformed and injected input, excess
output, worker failure, stale thesis reviews, revoked work, known or premature
forecast outcomes, duplicate resolution, and corrections. `task check`
passed with 100% statement and branch coverage. `task test-analysis-container`
passed against a local Linux ARM64 Docker engine with cgroup v2. Repeat the
analysis gate and the pending C04 gate on the intended pilot deployment.
The C07 fixture drives these records through the two-cycle workflow.
[Operator API and analysis gate](c06-research-records.md)

## C07 — Integrated pilot and acceptance

The deterministic workflow is available as `task pilot`. Two invocations
share a private catalogue and pause for human review. A fresh process
discovers the baseline dependencies after approval, reruns the corrected
dataset, scores the frozen forecast, and queues a follow-up charter.
Process-exit tests exercise retries after quarantine release, analysis,
proposal submission, and scoring. The local fixture uses a synthetic
browser session and ClamAV protocol responder by default, with the pinned
analysis worker in the local container engine.
[Operator runbook](c07-pilot-runbook.md)

The local Docker run reproduced the baseline dataset and traced a sample to
CSV line 3. The correction changed the negative count from one to two. The
forecast Brier score was 0.0625 and the baseline score was 0.25. The local
smoke test used synthetic review decisions. Human review, integrated
Chromium and ClamAV on the intended deployment, and live-source evidence
remain pending.

Provide one local command and an operator runbook for the two-cycle fixture.
Document setup, source configuration, run, pause, revoke, retry, diagnostics,
and cleanup. Integrate policy, evidence capture, quarantine, analysis, review,
and forecast scoring through the same boundaries used by the component gates.

The fixture passes when all of the following hold.

- A reviewer can trace a material claim and a sampled dataset record to the
  exact source version and rerun the initial analysis against pinned inputs.
- An agent chooses an eligible project within the owner's restrictions and
  submits a follow-up charter for human admission. Selection and proposal
  submission preserve the approved project scope and budget.
- A fresh run finds the relevant records, reruns affected work, and explains
  what changed. Earlier artifacts, revisions, and forecasts remain intact.
- A human reviews the thesis proposal or no-change result. Model authority
  remains confined to proposal submission. The forecast resolution and
  baseline score are reproducible from the pre-outcome records.
- Crash and restart at artifact release, analysis, proposal submission or
  forecast resolution leave a consistent state. Repeated steps do not
  duplicate releases, reviews or scores. Cancellation and revocation reject
  stale results and remove active authority.
- Prompt injection, malformed downloads, secret canaries, resource limits,
  and loss of privileged components behave as specified. Logs and review
  artifacts contain the required provenance and exclude reusable secrets.
- The intended deployment passes the integrated browser and egress gates.
  The operator documents the anonymous source policy, residual HTTPS risk,
  and an independent review of the exercised boundaries before allowing
  separately configured live retrieval.

Use a reviewed public source with permission for the intended access and
appropriate rights for retained and published artifacts. Run the workflow
against that source on the intended deployment. Record reproducibility,
observed source and tool limitations, and time spent by the reviewer. Record
a go/no-go decision for a private multi-node trial. Keep public enrolment and
publication subject to their separate gates.
[Multi-node delivery plan](product-and-architecture-spec.md#14-delivery-roadmap)

## Working rules

- Land dependency-safe, reversible commits on `main`. Update tracker and
  acceptance evidence with the implementation. Incomplete capabilities stay
  disabled or deny by default.
- Keep milestone verification runnable locally with fixtures and no real
  accounts, persistent credentials, or Internet destinations in tests.
  Live-source runs use separately reviewed configuration.
- Reuse typed contracts and the trusted audit path. Record failure behaviour
  and adversarial fixture results alongside successful runs.
- Run `task check` before landing. Preserve 100% deterministic statement and
  branch coverage. Run applicable Chromium, isolation and egress acceptance
  for boundary changes.
- Keep a failed boundary gated. Record the blocker, preserve the previous
  safe behaviour, and continue work that can be verified independently.
