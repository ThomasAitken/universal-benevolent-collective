# Single-node research pilot roadmap

- **Current focus** · C04 download quarantine and release
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
| C04 | queued | C03 | Quarantined, scanned, content-addressed research downloads |
| C05 | queued | C03, C04 | Bounded model-assisted evidence collection through the isolated browser |
| C06 | queued | C01, C04 | Local research records, reproducible analysis, thesis review, and forecast resolution |
| C07 | queued | C05, C06 | Integrated two-cycle research pilot and operator acceptance |

C04 is the next landing target. Public-page observations for C05 and analysis
with preloaded fixtures for C06 can be developed in parallel with quarantine.

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

- Receive into per-session quarantine outside task workspaces. Enforce a
  streaming byte limit and SHA-256 digest. Track `receiving`, `quarantined`,
  `scanning`, `released`, `rejected`, and `expired` states.
- Validate normalised names, regular-file status, actual type, archive depth,
  count and expanded size, and executable-content policy. Scan for malware,
  secret canaries, and sensitive data, with a deny by default for scanner
  failure or an unavailable required scanner.
- Promote only clean, complete content by digest. Bind the result to the
  project, task, session, source, and scan decisions. Audit receipt, decision,
  release, and cleanup without copying file contents into the journal.
- Demonstrate clean-file release and rejection for oversize, traversal,
  symlink, malware, archive bomb, secret canary, and scanner failure. Show
  that cancellation, crash, and repeated cleanup never publish partial or
  unscanned bytes. Keep rejected and expired files inaccessible and clean
  them up on restart where needed.

The gate includes the file types selected for the pilot research question.
Exercise document parsing inside a bounded process where the programme
requires it. Treat scanner-approved files as untrusted analysis inputs.

## C05 — Isolated browser research

- Connect the supervisor's typed, policy-checked browser operations to the
  isolated worker. Support the navigation, structured observation and
  page-local controls needed to find and verify the pilot sources. Bound
  output sizes, tabs, time, tool use, and origin changes.
- Attach origin, locator, retrieval time and source references to evidence
  observations. Keep page text, screenshots, and extracted content labelled
  as untrusted. Model-visible data contains no authority to widen policy or
  invoke raw browser control.
- Keep sessions disposable. Deny public submission, uploads, authentication,
  raw CDP, arbitrary script execution, and access to browser storage and host
  paths. Require C04 for artifact export through quarantine.
- Exercise approved tasks in Chromium and verify that forbidden operations,
  redirects, prompt injection, expiry, cancellation, and channel failure
  remain contained. Prove that old handles and browser state cannot survive
  session loss. Confirm denied destinations receive zero contact.

Start with operator-approved origins and a small source inventory. Expand
operations and approved origins against observed research needs and acceptance
evidence.

## C06 — Research records, analysis, and review

- Create a minimal local programme and project record, with human admission,
  owner opt-in, a bounded project choice, and a queue for follow-up proposals.
  Store immutable versions for source artifacts, dataset releases, run
  manifests, and forecast submissions with explicit dependency references.
- Run transforms and analysis in a disposable, network-disabled environment
  with pinned tooling, read-only inputs, explicit outputs, and process,
  memory, storage, and time limits. Start with reviewed fixture analysis
  code. Gate execution of external research code on containment for untrusted
  programs. Verify the runner has no host credentials, browser authority,
  node keys, or core audit access.
- Emit versioned artifacts with schema, source lineage, method, digest,
  licensing and quality checks. Confirm that reruns can reproduce expected
  values and that malformed input, injected content, excessive output and
  execution failure do not yield accepted artifacts.
- Retrieve a pinned thesis revision and submit a validated draft, revision
  proposal, or reasoned no-change result under the v1 contracts. Provide
  human review, a diff, and evidence locators. Record review and catalogue
  changes durably and prevent model-only publication.
- Store a forecast question, pre-outcome submission and baseline, resolution
  evidence, and score. Test temporal separation, duplicate resolution,
  unresolved outcomes, and upstream corrections. Preserve the original
  submission and rule. Test that a fresh run can identify dependants from a
  changed input and that stale or duplicate commits cannot corrupt accepted
  state.

The local implementation can use a small catalogue, immutable files, and a
journal. Use task-scoped state and the existing supervisor and audit contracts.
[Thesis vertical slice](knowledge-base-spec.md#phase-1--single-node-vertical-slice)

## C07 — Integrated pilot and acceptance

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
