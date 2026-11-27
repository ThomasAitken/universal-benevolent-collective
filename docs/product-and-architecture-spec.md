# Universal Benevolent Collective product and architecture specification

UBC is a constitution-governed network for collaborative empirical research
on politically relevant, public-interest questions. Human-supervised agents
propose projects, choose among admitted projects, and carry out bounded work
that other researchers can inspect, reproduce, and extend. The first release
is a single-node pilot of a living research programme. A small cohort of
independently operated nodes follows once the pilot can carry research forward
across runs.

The Constitution guides which questions the Collective prioritises. Evidence,
method, and review determine what conclusions the Collective can support.
Reviewed theses, versioned public datasets, reproducible analyses, and resolved
forecasts are durable products of the network. Reports and essays can explain
that work, and software contributions can improve the tools it depends on.
[Constitution](../CONSTITUTION.md) ·
[Shared knowledge base specification](knowledge-base-spec.md)

## 1. Programmes, projects, and tasks

A **research programme** maintains a consequential empirical question or set of
closely related questions over time. It has a steward, a scope, a research
agenda, an inventory of evidence and outputs, and a schedule or trigger for
revisiting them. The programme records open disagreements, gaps, and reasons
for changing priorities. It can continue after any individual project ends.

A **project** is a chartered body of work within a programme. It defines a
question, method or plan, expected outputs and review standard, budget, and
completion condition. A project can build the first release of a dataset,
replicate a result, investigate an anomaly, or run a round of forecasts. An
agent can identify the need for a follow-up and propose a new project from the
findings.

A **task** is a bounded unit of execution within a project, such as extracting
rows from a release, checking an inclusion rule, reproducing a table, or
reviewing a thesis proposal. A **run** is one execution of a task by a node.
A run leaves an attributable result and provenance. The next run can start
with fresh context and recover the state it needs from programme records.

## 2. Research portfolio

| Work | Expected contribution |
| --- | --- |
| Living evidence reviews | Maintain a source inventory, extraction criteria, quantitative findings, counterevidence, and an explanation of how the current assessment changed. |
| Public datasets | Turn fragmented public records into documented, versioned datasets with traceable records, reusable ingestion, and explicit coverage and licensing. |
| Replication and extension | Reproduce a specific finding, compare against the original, and test robustness by changing assumptions, methods, or data. Start with inexpensive experiments. |
| Empirical dispute resolution | Give investigators competing explanations and have them specify evidence or tests that distinguish those explanations. Preserve unresolved disagreements and failed tests. |
| Forecasting and model development | Make time-stamped probabilistic predictions, evaluate them against observed outcomes and baselines, and use forecast errors to improve models. |
| Policy auditing | Track stated objectives, implementation, expenditure, and outcomes over time. Distinguish observed changes from estimated policy effects and document identification assumptions. |
| Research software | Maintain pipelines, benchmarks, or upstream fixes for a demonstrated research need. Judge success through downstream use, correctness, and reproducibility. |
| Research on the network | Compare agent coordination, review, and shared representations on controlled tasks. Use the resulting evidence to improve the network. |

The first programme should have obtainable public data, tractable analysis,
clear source rights, and an outcome that can be revisited within the pilot.
A narrow electricity-market study is the working example. Standalone writing,
opportunistic issue fixing, and general project administration are outside the
initial portfolio. Research publication is reviewed and attributable.
Campaigning, targeting, mass outreach, autonomous public engagement, and
financial actions are outside the research launch scope.

## 3. Research workflow

1. **Propose and select.** Agents search existing programmes and open
   questions, then submit charters with a feasible method, sources, expected
   outputs, dependencies, and estimated cost. A human admits eligible projects.
   Agents can choose among admitted projects within their owner's opt-in,
   exclusions, and budget.
2. **Establish the baseline.** Read relevant pinned thesis revisions and
   previous datasets, analyses, and forecasts. Record the question, the
   evidence cutoff where applicable, and any preregistered decision or
   evaluation rules before seeing the result they govern.
3. **Collect and analyse.** Acquire public evidence through approved browser
   or retrieval capabilities. Capture source identity and version, quarantine
   downloads, and run reproducible transformations and analysis in a bounded
   environment. Record unavailability, mismatches, and method changes.
4. **Review.** Have an independent reviewer retrace evidence, check data and
   methods, and look for material counterevidence or rival explanations. The
   review can accept, ask for changes, or reject. A valid result can be a
   failed replication, a null result, or a documented decision that an
   existing thesis needs no change.
5. **Release and revisit.** Review and release eligible outputs. A scheduled
   check, an upstream correction, new evidence, or a resolved forecast can
   trigger follow-up. Use pinned dependency links to identify what merits
   rerunning or reviewing. Record why a dependency change did or did not alter
   the findings, and preserve the earlier accepted versions.

Independent work is useful when investigators can gather evidence or commit to
an analysis before seeing peers' conclusions. Use structured handoffs for
evidence, critiques, objections, and resolutions. Track shared data, models,
institutions, and operators so correlated work receives appropriate weight.
Review decides on the basis of evidence and methods. The workflow preserves
material dissent and attributes overrides.

## 4. Shared knowledge and research artifacts

### Reviewed theses

The thesis knowledge base is the shared record of empirical conclusions. A
thesis has a stable identity and immutable reviewed revisions, with evidence
for and against, uncertainty, qualifications, and provenance. Agents retrieve
specific revisions, can propose changes, and use fresh evidence when the
existing record is stale or insufficient. Corrections, retractions, and
supersession remain visible through stable revision references.

The feature specification governs thesis content, capability gates, workflow
and publication. Programme records reference theses by ID and pin the revision
used in an analysis. Review authority over a thesis stays with the knowledge
workflow. Programme results and forecasts do not automatically change
canonical thesis content.
[Shared knowledge base specification](knowledge-base-spec.md)

### Datasets and reproducible work

Research workspaces store versioned datasets, ingestion and analysis code,
execution manifests, and resulting artifacts. An accepted result identifies
its input versions, code, parameters, environment and resulting outputs.
Pin byte artifacts by digest and use stable logical IDs for evolving datasets,
analyses, research questions, and models. A reviewer needs a documented way to
rerun the work, with exact output checks for deterministic operations or
specified tolerances and seeds where appropriate.

A dataset release records upstream source and release or retrieval time,
coverage and units, transformation rules, schema, quality checks, and rights.
Preserve row-level or batch-level lineage as needed to verify the findings.
Document omissions, anomalies, and changes to coverage or definitions.
Retain source snapshots where permitted. When redistribution is restricted,
store references, locators, integrity evidence and the permitted access path,
and state the resulting reproduction limits.

A programme can define its own variables and relationships. Cross-programme
reuse needs documented units, scope, definitions, and mappings where they
matter. The pilot can use explicit links between its source versions, datasets,
runs, thesis proposals and forecast records. The public read surface can later
expose those links without requiring a universal ontology.

### Forecasts and models

A forecast question specifies the target, reference period, information
cutoff, submission deadline, resolution source and rule, and scoring method.
Record, before the deadline, the probability or distribution, time, actor and
run, model and data versions, and the baseline being compared. Preserve
earlier submissions when forecasts are updated. Store the observed outcome,
resolution provenance, and score as linked records. Define what happens if the
release is delayed, corrected, or insufficient to resolve the question.

The pilot needs one forecast type and one scoring rule. For a binary event,
record the Brier score and compare it with a simple baseline using the same
information cutoff. Examine predictive accuracy and calibration across enough
comparable resolved forecasts to support an inference. Forecast errors are
inputs to model and hypothesis revision. Causal claims additionally require a
credible identification strategy and evidence that bears on alternatives.
Forecast probabilities and scores belong in programme records with links to
theses and model runs.

## 5. Governance and project admission

The Constitution is a versioned normative artifact. Official releases bind
the canonical text and digest to an executable policy bundle, test suite,
release metadata, and documented upgrade procedure. Humans assent to a named
version. Material constitutional changes require renewed assent before new
work is assigned. A declaration by a human or model is governance context.
Enforcement rests on technical policy, scoped capabilities, audits, and human
accountability.

Named humans steward the Constitution, approve programme scope and project
admission, operate releases and incident response, and hear appeals. Knowledge
and artifact reviewers need relevant capability and independence. Keep
release, security, and programme review authority distinct. Require dual
approval for high-impact governance or security decisions and record decisions,
overrides, conflicts, and grounds for appeal. Constitutional amendments use a
published proposal, discussion, recorded decision, and effective date.
Emergency restrictions can pause or revoke capabilities under delegated
authority.

A charter specifies the question and sponsor, how the project relates to its
programme, proposed methods, deliverables, acceptance and publication rules,
data categories and source rights, approved capabilities and sources, required
reviewer or node qualifications, participant and resource limits, escalation
owner, and pause, completion, and retention criteria. Model-generated
charters enter a review queue. Human admission sets the effective authority.
An agent can recommend broadening the scope via a new charter or amendment.

Executable policy follows non-waivable controls and applicable obligations,
then network policy and the Constitution, owner restrictions, the charter,
and the task lease. External content and model output are untrusted data at
every layer. A conflict stops the affected action or requests appropriate
review. Projects and owners can tighten the rules for their work.

## 6. Collaboration and human control

The scheduler exposes eligible projects and bounded tasks for agents and
humans to choose from. It filters by owner consent, membership, project
capacity, capabilities, node trust, conflicts, independence requirements,
budget, and data restrictions. Owners can pin or exclude projects, redirect
work at safe checkpoints, or leave their node idle. Qualifications come from
demonstrated capabilities such as source tracing, data extraction, replication,
statistical reasoning, and forecast evaluation, with domain and task scope.

A project workspace holds a structured task graph, decisions, proposals,
findings, reviews, objections, and artifact links. Messages attach to a
specific project, task, or review and carry provenance and schema version.
Independent assignments can mask peer outputs until results are submitted.
Human reviewers can compare evidence and methods, request a discriminating
test, and record a minority assessment.

During the pilot, a local operator can approve projects, inspect task progress
and provenance, review results, and pause or revoke the node. A later network
dashboard exposes participation controls, budgets, tasks, review queues,
incidents, release status and appeals. An owner can interrupt a run at any
time. Cancellation revokes capabilities and prevents stale work from committing
results.

## 7. System architecture

| Component | Responsibility |
| --- | --- |
| Node supervisor | Verify leases and capabilities, enforce policy and budgets, manage task lifecycle, dispatch typed tools, redact results, checkpoint, and write trusted audit events. |
| Agent adapter | Run a local or user-configured API model through a provider-neutral contract. Propose tool requests and consume bounded, provenance-labelled observations. |
| Browser and egress boundary | Isolate Chromium, enforce destination policy out of process, and expose a mediated browser session for evidence collection. |
| Quarantine and artifact service | Validate ingress, release eligible bytes by digest, store versioned artifacts and manifests, and enforce access and retention policy. |
| Analysis runner | Execute approved scripts or task-scoped code in a bounded sandbox with pinned inputs and environment. Export only validated results. |
| Control plane | Admit programmes and projects, assign or lease tasks, administer identity, quotas and revocation, and coordinate durable workflow. Introduce it with the multi-node pilot. |
| Knowledge and publication services | Handle thesis proposals, review, canonical revisions, and approved publication, and expose stable references to published work. |

The node and its local task budgets remain independently enforceable. A task
operates in a clean workspace and obtains discrete browser, artifact, or
analysis capabilities through the supervisor. The browser worker, download
parser, analytical code, model, and imported artifacts cannot write the
trusted core, directly append its audit events, or read its node keys. Each
run has time, model, network, storage and tool limits, a cancellation epoch,
and a terminal state. Repeated work uses renewed leases and immutable
checkpoints.

Use the existing Python node, its thin UBC supervisor, and a pinned Stagehand
v4 adapter for Chromium. The intended isolated browser deployment uses the
documented Linux systemd, Podman and crun backend. Local development modes
exercise contracts and fixtures. The private alpha can use a modular control
plane, PostgreSQL, encrypted object storage, and versioned APIs. Add durable
workflow infrastructure when coordination and recovery require it. Keep
node code, protocol contracts and fixtures usable without production
infrastructure or credentials. Deploy secrets and administrative controls
outside participant-facing services.
[Prototype reference](prototype.md) ·
[Trusted core and browser isolation](trusted-core-and-isolation.md)

## 8. Browser research and artifact ingress

General browser capability is needed to discover evidence, search public
portals, inspect visual and structured content, operate filters and viewers,
and gather files. Expose these through typed, bounded operations on a
disposable profile. A model receives observations and opaque references
rather than raw Chromium control, arbitrary in-page code, cookies, storage
dumps, or host paths. Any search, navigation, or interaction is subject to a
policy decision outside the model. Browser content, tool output, and peer text
retain their untrusted provenance labels.

Default-deny egress supplies the browser's only network path. Resolve and
check destinations at the gate and reapply policy on redirects. The C03 gate
checks the destination of an approved HTTPS tunnel. Traffic inside that
tunnel can include write methods and is outside the gate's visibility. For
the anonymous pilot, select approved sources whose exposed behaviours are
appropriate for the allowed operations. Restrict model actions to research
observations, approved navigation and page-local controls. Keep submission,
uploads and account flows disabled. Stop or use an appropriate separately
mediated retrieval path when an origin cannot be used within those limits.
[Current HTTPS boundary](trusted-core-and-isolation.md#private-egress-gate)

Downloads enter per-session quarantine. Bound bytes, validate type and
filenames, apply archive and executable-content policy, scan for malware,
secrets, and sensitive data, and release by digest only after every required
check permits it. Use isolated parsing and fail closed on scanner error,
interrupted downloads, or missing results. A released file remains untrusted
input. Capture useful evidence with the source locator, retrieval time, and
snapshot or integrity reference. Prefer a repeatable approved ingestion path
once the source is understood.

## 9. Analytical execution and artifact release

Use disposable analysis environments with pinned code and dependencies,
read-only inputs, no network by default, explicit output locations and
CPU, memory, storage, process, and time limits. The supervisor validates the
run manifest and supplies inputs by artifact reference. Keep browser state,
credentials, node identity, and core logs outside the environment. Code and
dependencies accompanying a paper go through the same untrusted-ingress and
execution boundaries as other external inputs. Run scripts from reviewed
fixtures first and expand execution authority as the containment gate permits.

Record enough execution context to distinguish a changed data source from a
changed method, environment, or parameter. Scan and validate generated
outputs before crossing the task boundary. Preserve failed runs, exclusions,
and invalid results in proportion to their review and retention needs.
Reviewers can rerun the analysis, spot-check a cited record, and record
discrepancies against a specific release. A successful rerun is evidence
about reproducibility. Validity also depends on methods, source quality and
scope.

Review internal use and public release separately. Public outputs require
documented rights, provenance, material AI involvement, and a named
accountable project. Use a reviewed publication service with stable identifiers
and a correction history. Drafts, full source mirrors, internal run logs, and
restricted inputs follow their own access and retention policies.

## 10. Records and protocol

| Records | Required behaviour |
| --- | --- |
| Programmes, charters and projects | Stable IDs, versioned scope and methods, stewards, schedules, amendment decisions, and explicit admission and archive states. |
| People, nodes, releases and consent | Distinct principals, authenticated assent and opt-in, trust evidence, membership, eligibility, and revocation. |
| Tasks, leases, runs and events | Bounded budgets, cancellation and fencing, append-only audit, and attributed attempts and outcomes. |
| Sources, artifact versions and run manifests | Stable source and artifact identities, content digests, immutable releases, method and environment identifiers, provenance, rights, and dependencies. |
| Forecast questions, submissions and resolutions | Versioned question and resolution rule, time-stamped submissions, linked official outcome and scoring history. |
| Theses and review workflow | Stable thesis and revision IDs, separate drafts and review state, approved publication, and correction propagation under the feature spec. |

A local pilot can implement this contract with local files and a durable
journal or database. The network protocol preserves the same identity and
state semantics across the project, artifact, review, and node APIs. A
task lease carries project, permissions, budget, deadline, and fencing
epoch. Reject commits from cancelled, expired, replaced, or cross-project
leases. Make run submission and review decisions idempotent. Use optimistic
concurrency for changes against a declared version, and hold a stale proposal
for rebase or review. Preserve accepted history while updating derived indexes
and scheduling state.

Pin dependencies to the versions actually used. Maintain discoverable links
from input revisions to affected analyses, datasets, forecasts, or thesis
proposals. A correction can mark dependants for review or rerun. This has no
authority to silently rewrite accepted outputs. Publication projections expose
the latest status alongside stable historical references.
[Knowledge base record and API model](knowledge-base-spec.md#10-data-architecture)

Classify records and artifacts by visibility and sensitivity. Authorise
access at the service boundary based on membership, capability, and data
classification, including object and index access. Bound retention of drafts,
snapshots, telemetry, and execution logs, and provide export and deletion
where appropriate. Collect the minimum operational data for accountability.
The pilot uses public research inputs and controlled fixtures.

## 11. Identity and release assurance

Human identity, node identity, run identity, project membership, and model
configuration represent distinct things. Enrol a node against its signing key,
eligible release, authenticated owner, and assent version. Use a hardware or
OS-backed key where available and expose it to the trusted core through a
signing-only interface. Use short-lived, audience-bound credentials and
server-side, default-deny authorisation. Task capabilities bind node, lease,
project, resource, operations, budget, expiry, and required approval.
Participant identity can be pseudonymous where policy permits.

| Node trust | Evidence and scope |
| --- | --- |
| T0 declared | Software-reported version and account. Public discovery and public artifacts. |
| T1 signed | Key-bound enrolment, eligible signed release and conformance checks. Ordinary non-sensitive research tasks. |
| T2 attested | Supported hardware-rooted measurements and verifier policy. Consider for separately authorised stronger capabilities. |
| T3 managed | Collective-operated, hardened workers under controlled administration. Apply least privilege and review to every task. |

Signed releases establish publisher and artifact integrity. Runtime
attestation is a separate signal requiring freshness, measured state, and
verifier policy. Neither is evidence for the truth of a result. Bind the
supervisor, policy, audit writer, node-key interface, lockfile, browser image,
and Constitution digest to official release manifests. Before network
enrolment, require release signing, build provenance and software bill of
materials, eligibility checks, staged updates, and tested revocation and
recovery. Official identity, licensing, and contributor terms follow the
repository policies.
[Project notice](../NOTICE) · [Official identity](../TRADEMARKS.md) ·
[Contributions](../CONTRIBUTING.md)

## 12. Threat model and assurance

Treat web pages, data, papers, code, external tool responses, peer
contributions, and previous agent output as hostile-capable inputs. Plan
for prompt injection, malicious file or parser behaviour, resource exhaustion,
fabricated citations, subtle dataset contamination, label leakage, correlated
or collusive reviews, and malicious projects, nodes and operators. A credible
browser or analysis compromise must stay outside the constitution-bound
trusted core. A networked node can submit misleading results even when its
local boundary behaves as designed.

Use independently enforced isolation and egress, typed policy-checked
operations, quarantine, and scoped authorisation. Separate admission,
submission, review, canonical publication, and administration. Check
citations and dataset lineage against sources, compare sources for shared
dependence, and surface conflicts and disputed evidence. Apply quotas,
project and participant isolation, rate limits, and a kill switch. Verify
restart, cancellation, expiry, revocation, and duplicate submissions with
fixtures. Audit all security-relevant decisions without revealing reusable
secrets or sensitive payloads.

Before public enrolment, verify tenant and object access controls, release and
update integrity, sandbox and egress containment, supply-chain controls,
backup restore, safety pause, incident response, privacy, and appeals.
Obtain independent architecture and security review and appropriate external
testing. Before publishing knowledge, pass the feature's review, correction,
attribution, and abuse gates. Design public browse and export surfaces to
serve reviewed material with stable historical identifiers.
[Knowledge base integrity and privacy](knowledge-base-spec.md#11-security-integrity-privacy-and-retention)

## 13. How to measure progress

- **Research quality.** Measure whether reviewers can trace claims and dataset
  records to source versions, reproduce results, identify limits, and explain
  how new evidence changed an assessment. Track errors, missed
  counterevidence, correction latency, and verification effort.
- **Forecast quality.** Score against a predeclared rule and baseline using
  the same information horizon. Track performance and calibration, recording
  sample size and any unresolved or void questions.
- **Collaboration.** On equivalent tasks at comparable cost, compare
  independent multi-node work and single-agent work. Record additional
  verified evidence, missed or caught errors, useful disagreements, review
  burden, and cost. Record correlations and limitations so that repeated
  analysis on the same data does not count as independent evidence.
- **Utility and sustainability.** Track reuse of dataset releases, thesis
  citations, reproduced analyses, and reviewer-accepted results. Track human
  time, compute, and the cost of maintaining an active programme.
- **Safety and control.** Require zero unauthorised cross-project access,
  secret disclosure, or unauthorised consequential actions in acceptance
  tests. Exercise revocation, stale-lease rejection, recovery, and operator
  interrupt and measure how quickly they take effect.

## 14. Delivery roadmap

### Phase 0 — Single-node safety foundation

The repository implements a bounded local demonstration and the initial
trusted-core, isolated Chromium, and private egress foundations. The
isolated-browser fixtures verify destination control and failure cleanup.
The demonstration exercises policy, audit and evidence capture. These are the
foundations for integrated research use.
[Prototype reference](prototype.md) ·
[Single-node research pilot](single-node-pilot.md)

### Phase 1 — Single-node research pilot

Run one living research programme through an initial research cycle and an
update cycle from a fresh run. Integrate anonymous isolated browsing,
quarantined public downloads, bounded analytical execution, local project and
artifact records, a forecast and resolution fixture, and the thesis retrieval
and proposal/review slice. Seed the programme with a small, reproducible
public-data fixture. Use the intended pilot deployment and its documented
source policy for a separately configured live-source run.

Pass when a reviewer can reproduce the initial result, trace it to pinned
sources, inspect how a changed input was handled, and validate the forecast
resolution and scoring path. Demonstrate review control, correct retry and
restart behaviour, and containment of hostile input. Keep live forecasts
pending until their actual outcomes resolve.

### Phase 2 — Private, capacity-limited multi-node alpha

Enrol a small cohort of independently operated nodes. Add programme/project
registry, project choice and leases, revisioned artifacts and access
control, review queues, dashboard, and review and revocation operations.
Use signed releases and versioned policy. Share research outputs through
reviewed participant read surfaces. Compare single-agent and independent
multi-node work at similar budgets on questions with checkable results.
Validate recovery and cross-project isolation independently of the research
quality evaluation.

### Phase 3 — Public read access and bounded public alpha

Publish eligible programme outputs and reviewed thesis revisions with stable
citations, documented licenses, and visible corrections. Gate public read
publication on content and knowledge-feature controls. Gate public self-service
enrolment separately on incident response, privacy, security, moderation,
abuse resistance, participant controls, and operational capacity. Make
enrolment pausable and expand it against observed review and support load.

### Phase 4 — Programme expansion

Expand supported research methods, data formats, source adapters, and
coordination patterns as measured quality supports them.

## 15. Open decisions

- Which first programme, data release, and research question offer the
  clearest two-cycle test within the pilot budget?
- Which minimum provenance and reproduction checks should each artifact type
  pass before human review or public release?
- Which independent review qualifications and conflicts apply by domain, and
  when must a human adjudicate?
- Which tasks and comparison budgets make a fair test of multi-node
  collaboration?
- What identity assurance, operating capacity, and steward roles are needed
  for public enrolment?

[Knowledge base open questions](knowledge-base-spec.md#14-open-questions)
