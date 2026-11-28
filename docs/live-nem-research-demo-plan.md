# Agent-led research demo for the National Electricity Market

## What the demo should prove

Two agents can turn an open-ended idea into a tractable research programme,
find evidence on the live web, and carry the work forward from each other's
records. The operator approves the charter, watches progress, and can stop
activity at any time. The agents make the research decisions, challenge each
other's findings, and decide when the work meets the charter's completion
criteria.

Run the demo on one Linux node with two separately identified LLM agents
in fresh contexts and processes, with no shared hidden transcript. Give each
agent access to the same project records and constitution. Distinct contexts
let us test handoff and critique. Record shared models, sources, and methods
so agreement gets the right evidential weight. [Constitution](../CONSTITUTION.md) ·
[Product and architecture specification](product-and-architecture-spec.md)

## Seed for the NEM run

> Explore Australia's National Electricity Market and propose a research
> question whose answer would help the public understand how the electricity
> system is changing. Negative wholesale prices are one starting point.
> Follow the evidence if it leads you to a more useful question. Look for
> competing explanations, identify what could distinguish them, and work
> out what you can actually test with public evidence. Propose a tractable
> first project and explain why the answer would matter, what you would
> measure, and what finding would change your mind.

The seed gives the agent a domain and a possible starting point. The agent
chooses the question, sources, regions, dates, method, and expected
deliverables. Several questions, including a descriptive one with a credible
path to deeper work, may be reasonable. The acceptance run starts with an
empty source inventory and no preloaded findings or expected answer.

The entry point also accepts an empty seed. In that mode the agent starts by
looking at existing programmes or open questions and proposes a public-interest
idea of its own. It can explain why a new programme is worthwhile or propose
joining work that already exists. Exercise both entry paths. Use the seeded
path for the recorded NEM demo.

## Research lifecycle

1. **Discover a question.** The agent searches the public web, opens results
   and follows links within a separate pre-charter budget. It checks existing
   theses or programmes when available. It saves a shortlist of questions,
   candidate sources, and evidence about feasibility, limitations, and rights.
   Start with a five-minute wall-clock limit and configurable ceilings on
   search queries, page reads, bytes, model calls, and spend. When any ceiling
   is reached, preserve the discovery record and surface the charter or the
   unresolved question.
2. **Propose and approve a charter.** The agent submits a programme objective
   and a first project with a question, motivation, candidate evidence,
   proposed method and alternatives, completion criteria, risks, and resource
   budget. The operator sees that proposal and its discovery trail and can
   approve, return it for revision, or decline. Approval grants the project
   authority to conduct research. The charter defines what kinds of sources
   and methods are eligible, so agents can add suitable discoveries within
   scope. A proposed expansion of the approved authority returns through the
   charter decision.
3. **Pursue the research.** The agent discovers and requests sources itself,
   records why they are relevant, and acquires live data through the supervised
   retrieval path. It can decide which documented fields to use, propose
   competing explanations, request approved analytical operations, and
   revise its approach in light of the results. The operator sees queries,
   websites and redirects, tool outcomes, budget usage, and milestones as the
   work proceeds, and can inspect retained page snapshots. A project stop
   revokes authority across its active runs, cancels outstanding work,
   retires their browsing sessions, and preserves committed records. Exhausting
   the budget leaves a resumable checkpoint with a clear account of what
   remains open.
4. **Hand off unfinished work.** While the project is still open, a second
   agent claims a bounded task under the same charter and reads the saved
   project state in a fresh context. It can inspect the evidence, rerun an
   analysis, pursue a gap, or offer a competing interpretation. Contributions
   pin the revision they build on and carry their own author and provenance.
   Task claims have a time limit, and agents can recover work when a claim
   expires. Conflicting revisions remain visible for agents to resolve
   against the evidence. The demo should show the second agent materially advancing,
   qualifying, or substantiating a finding and recording why.
5. **Complete and export.** The agents assess the work against the charter
   and produce a locally viewable research report with methods, citations,
   figures or tables where useful, limitations, and unresolved objections.
   An agent acting as peer reviewer checks a material claim against the
   original source and records its reasoning. The operator's routine role
   after charter admission is monitoring and stop control. The report is
   the demo artifact. Any later public release follows the existing
   publication rules.

## Project knowledge and optional public outputs

Research notes are the working memory of the project. Keep hypotheses,
source assessments, measured results, critiques, and open questions in
versioned records with statuses, authors, evidence references, and links to
earlier versions. A handoff should tell the next agent what was tried, what
held up, what changed, and what is worth doing next. Preserve corrections
and disagreements. The behaviours we want to see are initiative, credit for
prior work, willingness to correct a claim, and honest reporting of uncertainty.

A **thesis** is an optional proposal for the public knowledge base when the
programme produces a durable finding that other projects can reuse. It
should carry its scope, qualifications, evidence, and relationship to relevant
existing theses. Keep the current capability and public revision rules in
place. Drafts, competing interpretations, and intermediate findings remain
usable in the project workspace. [Knowledge base specification](knowledge-base-spec.md) ·
[Constitution](../CONSTITUTION.md)

A **forecast** is optional when the project supports a causal model that
can make a testable prediction. Record the model's assumptions, predictive
uncertainty, a future target, information cutoff, baseline, and resolution
rule before the outcome is available. If the programme produces public
forecasts later, give each a stable reference for thesis links and a
visualisation showing the prediction, baseline, and eventual outcome and
score. A still-open forecast can accompany a completed report.

## Web authority and execution

Expand the range of destinations the agent can discover and read. In
discovery, eligible public sites can be reached through search results,
links, or a proposed public URL without per-site approval. After admission,
the same mechanism runs within the charter's source criteria and budgets.
The operator can inspect the request and destination history in both modes.

Keep the existing isolated Chromium worker and VM. General web access needs
a read path that can enforce operations before sending them upstream. The
current blind HTTPS tunnel sees the destination authority and cannot enforce
the HTTP method. Add a mediated fetch service for discovery and research
that makes credential-free HTTPS GET and HEAD requests itself, checks each
redirect and resolved address against public-network policy, and enforces
time and byte limits. Keep private networks and VM management interfaces
outside that policy. Render bounded, sanitised page content in Chromium
with outbound resources and active page code disabled. Return useful text
and links to the agent, alongside the original URL and retrieval metadata.
Search should use a bounded search interface separate from page fetching,
backed by a deterministic fixture for offline checks and a configured live
provider for VM runs. Log the source observations, budget usage, and tool
choices. Source pages and agent notes enter later contexts as evidence with
provenance, with authority supplied by the supervisor and charter.
[Current HTTPS boundary](trusted-core-and-isolation.md#private-egress-gate)

For research data, let the agent propose a discovered URL and declared file
type. The supervisor checks it against the charter and routes it to private
quarantine. Reuse the scanner and scoped receipts, and register formats
only where we have a bounded validator (starting with CSV and JSON, plus
bounded archive extraction only where explicitly validated for public energy
releases). Pin raw bytes by digest and record the publisher, effective URL,
retrieval time, rights, and derivation history. Give the model released
metadata and bounded results. Keep analysis offline with pinned methods and
resource limits. Start with typed methods for tabular selection, validation,
grouping, aggregation, and comparison so the agent can choose a method and
parameters appropriate to its question. If an answer requires unsupported
data or execution, the agent can narrow the inquiry, try a viable source, or
document the gap. Changes to trusted-core components (policy, supervisor,
quarantine, or browser runtime) keep the release manifest updated and rerun
the Linux VM isolation gates. [Current pilot capabilities](single-node-pilot.md)

## Commit sequence on `main`

Land these commits in dependency order so each change stays small,
reviewable, and testable offline with `task check` (100% coverage) while
exercising real isolation and live-provider gates in the Linux VM at the
security and integration boundaries.

| Commit | Deliverable | Check before advancing |
| --- | --- | --- |
| 1. Mediated public-web fetch and bounded search | Add a credential-free HTTPS `GET`/`HEAD` fetch service that checks resolved IPs and every redirect against public-network policy (excluding private networks and VM management interfaces) with time, byte, and hop limits. Add a separate bounded search interface with an offline fixture and a configurable live provider. | `task check` passes; negative tests prove private destinations, unsafe redirects, state-changing methods, oversize payloads, and timeouts are blocked. |
| 2. Isolated sanitised page rendering and mid-read stop | Render bounded, sanitised snapshots from the mediated fetcher inside the existing isolated Chromium worker with outbound subresources and active page code disabled, returning structured text, links, and retrieval metadata. Add a stop control that cancels active reads and retires the browser session cleanly. | `task check` passes; Linux VM isolation checks confirm ordinary page rendering works while active page scripts, outbound subresources, and stopped sessions are blocked or retired. |
| 3. Research budgets, operator activity view, and project stop | Extend `RunBudget` and supervisor accounting to enforce configurable ceilings on wall-clock time, search queries, page reads, bytes, model calls, and spend. Add a live operator activity view (queries, URLs/redirects, tool outcomes, budget usage, retained snapshots) and project-wide stop/revocation across active runs. | `task check` passes; tests verify each budget ceiling halts cleanly with resumable state and project stop revokes authority across concurrent runs. |
| 4. Generalised charters, discovery trail, and operator admission | Generalise the catalogue beyond the fixed `PriceWindow` charter to support open-ended programme objectives, eligible source criteria, methods, completion criteria, risks, and budgets (keeping existing tests green until pilot retirement). Persist pre-charter discovery records (shortlisted questions, candidate sources, feasibility and rights notes) and add operator admission (`approve`, `return_for_revision`, `decline`, and scope-expansion re-approval). | `task check` passes; tests confirm pre-charter work cannot exercise research authority, admission activates scoped authority, and scope expansion returns to the operator. |
| 5. Seeded and unseeded discovery agent loop | Connect the structured LLM backend (and scripted test backend) to the discovery tools (search, mediated read, inspect existing programmes/theses, save discovery notes, propose charter) for both seeded and empty-seed entry paths. | `task check` passes with scripted agents on both seeded and unseeded paths (including budget-ceiling handoff); run a live-model smoke check producing a charter proposal from real discovery. |
| 6. Agent-initiated live acquisition and quarantine generalisation | Let chartered agents propose discovered data URLs and declared file types; validate against the charter and route through private quarantine with ClamAV scanning, canary checks, scoped receipts, and bounded validators (starting with CSV and JSON). Version raw bytes by digest with publisher, effective URL, retrieval time, rights, and derivation links. | `task check` passes (with updated release manifest); tests cover live-like acquisition, format/canary/scope rejection, budget exhaustion checkpoints, crash recovery, and revocation mid-acquisition. |
| 7. Typed offline tabular analysis in the isolated worker | Supplant the single fixed price method in the digest-pinned, network-disabled container worker with typed tabular operations (selection, validation, grouping, aggregation, comparison) with explicit parameters, source-line provenance, and bounded outputs exposed to the LLM backend. | `task check` and the Linux VM container analysis gate pass; results trace back to pinned input bytes, limits/seccomp hold, and unsupported formats or operations fail cleanly. |
| 8. Versioned project notes, task claims, handoff, and peer review | Add versioned research notes (hypotheses, source assessments, measured results, critiques, open questions, statuses, authors, revision links), lease-bounded task claims with expiry recovery, revision-aware contributions that preserve conflicts, and agent peer review verifying a material claim against pinned source bytes (plus optional thesis and forecast hooks). | `task check` passes; a second agent in a fresh process claims unfinished work, checks or extends a prior finding, and preserves provenance and any material disagreement. |
| 9. Local report export, operator CLI, and replication script | Add charter completion assessment and export of a locally viewable research report and provenance bundle (methods, citations, figures/tables, limitations, unresolved objections, peer-review record, and any open forecast) behind one documented operator entry point, plus a replication script so users can rerun the NEM workflow with their own configured models. | `task check` passes; an end-to-end offline rehearsal with scripted agents runs through the replication script from discovery through admission, acquisition, analysis, handoff, peer review, and reproducible report export. |
| 10. Linux VM rehearsal and saved NEM run | Rehearse the full seeded NEM run (and exercise the unseeded entry path) in a clean Linux VM with real models, live public sources, isolated Chromium, ClamAV, and containerised analysis. Commit the final results of the first successful run (exported report, provenance and audit summary, and run metadata, without credentials or private runtime state) into the repository. | Saved run artifacts satisfy every acceptance criterion below, the replication script is documented for user-supplied models, and `task check` passes. |
| 11. Retire the single-node pilot and cut over docs | Remove the superseded synthetic single-node pilot fixture, fixed price-method demo scaffolding, and pilot-only docs while retaining the reusable isolation, quarantine, and container-analysis component gates. Update the `Taskfile`, runbooks, and top-level docs to make the live research workflow, saved NEM results, and replication script the main demo. | `task check` passes at 100% coverage with no stale pilot references, and the component isolation and container gates remain intact. |

### Acceptance criteria for the recorded NEM demo

The recorded demo passes when the saved repository artifacts and audit show:

- an agent-authored research question grounded in its persisted discovery trail;
- at least one live public source discovered, retrieved through quarantine, and pinned by digest by the agent;
- one human charter admission decision (with operator monitoring and stop control verified);
- unfinished work claimed and materially advanced, qualified, or substantiated by a second agent in a fresh process before completion;
- a locally viewable report saved in the repository containing at least one material claim reproduced from the pinned evidence, with an agent peer-review record;
- a working, documented replication script that lets others reproduce the workflow with their own models;
- recorded actual agent spend, operator time, and a concise account of how the second agent changed the work.

[Linux pilot runbook](pilot-runbook.md#intended-linux-deployment)
