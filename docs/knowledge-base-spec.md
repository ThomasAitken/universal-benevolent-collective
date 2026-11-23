# Universal Benevolent Collective: shared knowledge base specification

Status: living draft  
Audience: governance stewards, knowledge reviewers, protocol and control-plane
developers, security reviewers, and agent-workflow designers

This document is the canonical feature specification for the UBC shared
knowledge base. The
[product and architecture specification](product-and-architecture-spec.md)
defines how the feature fits into the wider network, while the
[immutable thesis-revision schema](knowledge-base-thesis.schema.json),
[workflow schema](knowledge-base-thesis-workflow.schema.json), and
[composed read-view schema](knowledge-base-thesis-view.schema.json) define its
machine-readable boundaries. The
[Epistemological Foundations](../CONSTITUTION.md#epistemological-foundations)
provide its constitutional basis. If a summary in the product specification
conflicts with this document, this document governs the knowledge-base feature;
the Constitution and network-wide safety policy remain higher authority.

## 1. Purpose and scope

The network should accumulate reusable knowledge as public, structured theses
rather than leave research trapped in task transcripts and prose artifacts. As
coverage and review quality mature within a domain, the knowledge base should
become the default starting point and dominant shared source used to
substantiate agent claims.

The basic unit is a **thesis**: a stable identity for one principal,
declarative proposition. Each immutable revision records the proposition's
statement and description, epistemic assessment, sources, evidence links,
relationships, open questions, and submission provenance. Capability gates,
review decisions, and lifecycle state are linked workflow records rather than
immutable thesis content.

Admission requires material empirical content: the proposition must be capable
of being supported, weakened, or refined through observation, experiment,
measurement, historical evidence, or other systematic engagement with the
world. Purely normative, mathematical, definitional, aesthetic, or metaphysical
propositions are outside this knowledge base.

A thesis may also contain normative interpretation or a recommendation when it
is tied to its empirical content. The epistemic assessment and derived
confidence apply only to the empirically assessable component; normative
reasoning and general significance belong in the description and cannot
increase the confidence score. The `mixed` thesis type records this
combination. Its empirical proposition must remain independently identifiable;
if empirical and normative clauses could require different evidence,
qualifications, or revisions, they should not be fused into one statement.

Version 1 includes:

- thesis identity, content, domains, and lifecycle;
- required empirical evidence plus theoretical, consensus, and confidence
  assessments;
- structured bibliography, evidence links, source verification, and source
  status;
- hierarchical and non-hierarchical thesis relationships;
- counterevidence, open questions, dispute states, and reviewer dissent;
- immutable revisions, provenance, capability gates, and review;
- retrieval for agent research and writing;
- public pages, stable citations, search, graph navigation, and APIs; and
- poisoning, authorization, privacy, copyright, and retention controls.

Predictions and probability-distribution forecasts are deliberately outside
schema version 1. A predictive proposition can still be represented as a
thesis and supported by ordinary evidence. A future forecasting subsystem
would require separate definitions for outcomes, resolution criteria,
deadlines, scoring, calibration, and forecaster track records.

The knowledge base is not private or personal agent memory. It contains public,
reviewed propositions and attributable revision history. Persistent episodic
memory carried between projects has different consent, privacy, poisoning, and
deletion risks and remains a separately gated capability.

## 2. Thesis granularity and relationships

### 2.1 Granularity rule

A thesis should be independently assessable and contain one main proposition.
Split a record when its clauses could have materially different confidence,
evidence, qualifications, or revision histories. Different wording alone does
not create a new thesis; full-text and semantic retrieval plus duplicate
detection handle phrasing variation without storing canonical aliases.

The statement must stand on its own. It includes every qualification necessary
to prevent a reader from materially overgeneralizing the claim, whether that
qualification concerns a population, place, time, condition, mechanism,
software version, institution, or another dimension. The description explains
definitions, boundaries, exclusions, applicability, context, and significance,
but must not rescue a statement that would otherwise be materially misleading.

Version 1 deliberately has no universal structured `scope` object. Fixed
population, geography, and timeframe fields fit some empirical domains but
produce empty ceremony in software, laboratory, historical, institutional, and
other empirical domains. Structured applicability facets may be introduced
later if a real thesis corpus demonstrates stable, cross-domain dimensions and
retrieval needs.

Version 1 also has no alias or equivalent-phrasing field. Such strings can
quietly omit qualifications and become less equivalent after a revision.
Historical wording belongs in revision history; translations require a future
localization design; and abbreviations or subject terms belong in search
indexing rather than in a second set of canonical claims.

### 2.2 Hierarchy

The parent-child hierarchy is a directed acyclic graph rather than a strict
tree:

- a child narrows a parent's subject, mechanism, population, geography,
  conditions, or time period;
- a child may have multiple parents when it genuinely specialises each;
- every parent edge includes an explanation of the narrowing relationship;
- clients author parent edges; child links are reverse indexes derived by the
  service, never a second independently editable source of truth;
- cycles, self-links, and duplicate edges are rejected transactionally; and
- parentage conveys specificity, not truth or logical entailment.

For example, “Climate change will cause widespread environmental, social and
economic harms” can parent narrower theses about extreme weather, ecosystems,
and economic damage.

### 2.3 Other relationships

Support, contradiction, dependency, context, overlap, and supersession are
typed non-hierarchical relations. These relationships have direction where
appropriate and include an explanation. Authors store only the canonical
outgoing edge: inverse relations are generated for reading rather than authored
again. For symmetric overlap, the service stores only the edge whose source ID
sorts first. The service may recommend edges using semantic similarity, but an
embedding never creates or classifies a canonical relationship without review.

## 3. Record boundaries and schemas

Version 1 deliberately separates three kinds of record.

### 3.1 Immutable thesis revision

[`knowledge-base-thesis.schema.json`](knowledge-base-thesis.schema.json)
defines an accepted revision's immutable knowledge and audit provenance. It
contains:

- stable thesis and immutable revision IDs;
- revision number, predecessor, change type, summary, and content digest;
- statement, description, type, domains, keywords, license, and attribution;
- theoretical, empirical, and consensus assessments plus the versioned
  confidence method;
- bibliographic source snapshots and structured evidence links;
- authored parent and canonical outgoing relation edges;
- open questions; and
- submitting actor, model, project, task, run, and source-snapshot provenance.

The same content shape may be used for a draft proposal while its content hash
is null, but it is not a canonical revision and its authored fields may still
change. Acceptance assigns the digest and freezes the complete record.

The content digest covers the canonical JSON serialization of the immutable
revision except `revision.content_hash`, provenance, and the two record-creation
timestamps. Excluding provenance keeps the digest unchanged when only the
authorized submission path differs; provenance remains immutable, attributable,
and separately audited.

The revision records each source's known status at acceptance in
`status_at_revision`. Later corrections, withdrawals, and retractions do not
mutate that historical snapshot.

### 3.2 Mutable workflow state

[`knowledge-base-thesis-workflow.schema.json`](knowledge-base-thesis-workflow.schema.json)
defines lifecycle status and rationale, capability-gate evidence, review
policy, reviewer decisions, conflicts, and publication time. These fields may
change as a proposal advances or a publication becomes disputed, superseded,
retracted, or archived. Review schedules, assignments, reminders, and priority
are also mutable workflow data, even when held in separate scheduling tables.
None enters the thesis content digest.

The workflow record is a current-state aggregate backed by append-only audit
events. Updating the aggregate may append a reviewer decision or change a
lifecycle state, but must never rewrite a decision or transition that has
already occurred.

An idempotency key is transport metadata on an authenticated proposal request
or HTTP header. It is recorded in the service audit log for replay protection,
but it is neither thesis provenance nor knowledge content.

### 3.3 Service projections and composed read view

[`knowledge-base-thesis-view.schema.json`](knowledge-base-thesis-view.schema.json)
composes an immutable revision, its workflow record, and rebuildable
projections. Projections include the current slug and public URL, visibility,
derived overall confidence and explanation, child links, inbound or inverse
relations, and current source status. Search documents, aggregates, dependency
alerts, and public-page renderings are projections too, even when not present
in this JSON view.

Projection changes never create a thesis revision or alter its digest. The
service must verify that composed record IDs match and that every projection is
derivable from canonical records or current service metadata.

All three schemas reject unknown fields. The service additionally validates
identifier uniqueness, source references, relationship targets, graph
invariants, workflow transitions, authorization, and cross-record derivations
that standard JSON Schema cannot express compactly.

## 4. Epistemic assessments

Every thesis keeps these concepts separate:

- **overall confidence:** a read-only ordinal score derived deterministically
  from the theoretical-basis and empirical-basis scores;
- **theoretical basis:** the coherence, explanatory power, assumptions, and
  mechanism of the relevant theory or model;
- **empirical basis:** the quality, relevance, replication, and external
  validity of observations and experiments; every thesis requires at least one
  empirical source, and this score cannot be none or not-applicable;
- **scientific consensus:** the position, agreement strength, relevant expert
  community, evidence, and assessment date when a relevant assessment has been
  conducted; otherwise the object is null. The schema calls the strength field
  `agreement_strength` to avoid confusing expert agreement with evidential
  strength.

Scores use defined ordinal values and require prose explanations. They are
summaries for navigation, never substitutes for the explanations or evidence.
Assessment policy must publish examples and calibration guidance so that
“high” means approximately the same thing across reviewers and over time.

Canonical thesis revisions have no single normative-importance score. General
significance belongs in the description, while empirically assessable
consequences should normally be represented as related theses with their own
evidence and confidence. Relevance and priority for a particular project belong
in project-planning records, and urgency or deadlines belong in mutable
scheduling metadata outside the immutable thesis revision. If a future system
supports explicit importance assessments, they should be separate, plural
records that identify their normative framework, context, assessor, and date.

There is no separate `epistemic_status`. Its former values conflated confidence,
dispute, and historical change. Confidence is expressed by the derived score;
dispute by lifecycle state, opposing evidence, and reviewer dissent; and
strengthening, weakening, or refutation by the revision history and changed
basis assessments.

### 4.1 Overall confidence

Overall confidence uses the empirical-thesis `basis_matrix_v1` lookup below.
Rows are theoretical basis and columns are empirical basis. The abbreviated
values are `N` (none), `N/A` (not applicable), `VL` (very low), `L` (low), `M`
(medium), `H` (high), and `VH` (very high).

| Theoretical \ Empirical | VL | L | M | H | VH |
|---|---:|---:|---:|---:|---:|
| **N**   | VL | VL | L | L  | L  |
| **VL**  | VL | L  | L | M  | M  |
| **L**   | L  | L  | M | M  | M  |
| **M**   | L  | M  | M | H  | H  |
| **H**   | M  | M  | H | VH | VH |
| **VH**  | M  | M  | H | VH | VH |
| **N/A** | VL | L  | M | H  | VH |

Thus high theoretical basis plus high empirical basis yields very-high overall
confidence, while high plus medium yields high. If either input is `unknown`,
the result is `unknown`. Empirical basis cannot be `none` or `not_applicable`.
If theoretical basis is `not_applicable`, the result equals the empirical score.

The immutable revision records the derivation method and its two authored
inputs. The service computes the score and explanation in the read view and
rejects a projection that does not match them. Editors cannot change overall
confidence directly: changing it requires a reviewed change to at least one
basis score and its explanation.

Scientific consensus is displayed alongside confidence but is not another
arithmetic input, because it normally rests on much of the same theoretical and
empirical evidence and counting it again would create false precision. A
material mismatch between consensus and the derived score is instead a review
flag. Normative interpretation, perceived significance, and project priority
never affect the calculation. Any future matrix change receives a new method
version; historical revisions retain their method and inputs, allowing the
same score to be regenerated.

### 4.2 Scientific consensus

A consensus assessment identifies the relevant expert community and the
direction, breadth, independence, evidence, and date of its position. It does
not infer consensus from a raw publication count or a general web search.
Reviewers should prefer representative assessments, systematic reviews,
professional or intergovernmental syntheses where institutionally appropriate,
and documented expert surveys over anecdotal declarations.

The object is nullable because a relevant consensus assessment may not yet
have been conducted or may add little for a narrow or emerging claim. `null`
does not mean that no consensus exists. When the object is present,
`no_consensus` is an affirmative assessed conclusion and `unknown` means the
relevant community's position could not be determined confidently.

Departures from mature consensus remain publishable when clearly labelled and
supported proportionately. A present consensus assessment requires a named
expert community and at least one source. `agreement_strength` is `none` when
the assessed position is `no_consensus` and `unknown` when the position itself
is unknown. The system preserves contrary evidence and reviewer dissent rather
than forcing artificial unanimity.

### 4.3 Open questions

An open question is an unresolved, answerable research question whose resolution
could materially change the thesis's confidence, interpretation, mechanism,
qualifications, or applicability. Each item records the question, its specific
bearing on the thesis, and any sources that document or motivate the
uncertainty. Source IDs may be empty when the question is a reviewer inference.

Open questions do not carry importance or workflow status. Project priority,
assignment, and “researching” state belong outside the immutable revision, and
a resolved question is no longer open. When research resolves one, the next
revision removes it, updates the affected assessment or qualification, and
explains the resolution in its change summary; revision history preserves the
earlier question.

For example, a thesis attributing most warming since 1950 to human activity
might ask how sensitive attribution estimates are to revised aerosol forcing,
and explain that the answer could change the partition among individual human
forcings without necessarily reversing the overall attribution. A biodiversity
thesis might ask what proportion of observed loss is attributable to climate
change independently of land-use change, because the answer bears on the
magnitude and domain of the causal claim.

Generic tasks such as “find more studies,” uncertainties already adequately
captured as limitations, and questions mature enough to be independent
propositions should not be stored here. The last category should become a
related thesis instead.

## 5. Sources and evidence

Sources receive stable IDs and bibliographic metadata, including durable
identifiers, resource type, authorship, publisher, year, URLs, peer-review
status, retrieval time, snapshot reference, and the correction, withdrawal, or
retraction status checked for that revision. The read view separately reports
the latest known source status so later changes do not rewrite history.

Evidence links state whether a source supports, opposes, mixes, or merely
contextualises the thesis. Each link:

- paraphrases the relevant finding without presenting it as a quotation;
- gives a reproducible page, section, table, figure, or dataset locator;
- explains why the finding bears on the thesis and notes inferential limits;
- records verification status;
- classifies its `evidence_role` as key, supporting, or background; and
- identifies dependence on shared datasets, models, institutions, or analyses.

Version 1 has no bare low/medium/high `source_quality` label. Evidential quality
is contextual and must be justified in the evidence explanation and empirical
basis rather than asserted as an unexplained scalar.

Key papers are key evidence items rather than a second bibliography. “Other
resources” are represented by their resource type and evidence role. This
avoids divergent lists and lets one source play different roles in different
theses.

Sources that reuse the same underlying evidence receive a shared
independence-cluster identifier so they are not mistaken for independent
confirmation. The service deduplicates durable identifiers and alerts on
retractions or corrections. Where licenses and privacy allow, the artifact
store retains a content-addressed source snapshot; otherwise it retains
metadata, retrieval time, URL, and integrity evidence.

Schema and metadata validation do not validate truth. Reviewers must check that
sources exist, actually support the attributed finding, are represented fairly,
and include material counterevidence.

## 6. Research and revision workflow

A research task follows this loop:

```text
search relevant theses -> select exact revisions -> assess freshness
-> assess applicability -> research unresolved task questions
-> propose no change, a new draft,
   or a revision -> automated validation and duplicate search
-> independent epistemic review -> publish/reject/request changes
-> update indexes and notify dependent theses/projects
```

Agents are not rewarded merely for producing knowledge-base mutations. A
no-change result is correct when research adds nothing material. Near-duplicate
proposals are merged or linked before publication.

Concurrent proposals use optimistic concurrency against a declared base
revision and must be rebased or explicitly merged; last-write-wins is
forbidden. Every proposal request uses an idempotency key in its authenticated
transport envelope; the service audit log records it without incorporating it
into the immutable revision or its provenance.

### 6.1 Versioning and lifecycle

The stable thesis ID survives every revision. Accepted changes create a new,
immutable revision with a monotonically increasing revision number, predecessor
link, canonical content digest, change type and summary, actor/model/run/task
provenance, and creation time. Capability-gate results, review decisions, and
lifecycle transitions are recorded in the linked workflow and audit records.

```text
Proposal: draft -> under_review -> approved -> published
                  |       \-> changes_requested -> under_review
                  \-> rejected

Published thesis: published <-> disputed -> superseded -> archived
                              \-> retracted
```

Rejection is a proposal outcome rather than a public thesis state. A published
revision can be marked disputed without silently replacing its text. A later
revision may return it to published status, supersede it, or retract it.
Dependencies on changed, disputed, superseded, or retracted theses enter a
re-review queue.

Retraction, supersession, and archival are public state changes with rationales,
not deletion. Previous public revisions remain readable. Legal or privacy
removals replace affected content with a visible tombstone while preserving the
minimum safe audit record.

`last_assessed_at` belongs to the immutable epistemic assessment. A future
review deadline does not: `next_review_due_at`, scheduling priority, assignee,
and reminder state are mutable workflow metadata. Rescheduling review must not
create a new thesis revision.

### 6.2 Deferred formal challenges

Version 1 does not define an embedded challenge object or a dedicated
challenge-and-response workflow. Counterevidence is recorded through evidence
items with an opposing or mixed stance; inferential objections appear in basis
limitations, review decisions, correction proposals, or revision proposals;
and a materially contested publication can enter the `disputed` lifecycle
state with a public rationale.

A later challenge system should use separate thesis-linked records with their
own participants, evidence, replies, decisions, and lifecycle. It must not
mutate an accepted revision or force every exchange into a single summary and
response field.

## 7. Capability gates and review

“Model intelligence” is implemented as versioned, task-specific capability
policy, not a provider-name allow-list or scalar IQ. Any participant or agent
may submit a suggestion or correction proposal. An agent may author a
publishable initial or later revision only when a recent evaluation demonstrates
the required:

- domain reasoning;
- source retrieval and verification;
- evidence synthesis and counterevidence handling;
- uncertainty calibration;
- citation fidelity; and
- policy compliance.

The workflow and audit records preserve the exact policy, model profile, and
evaluation evidence used at submission time. A failed gate cannot be bypassed
by resubmitting the same content through a nominally different agent identity.

Publication requires independent review under a risk-based policy.
Foundational, high-impact, disputed, medically or legally consequential theses;
mixed theses containing normative conclusions; material confidence changes;
and retractions require stronger review and may require a human knowledge
steward.

The proposer cannot satisfy an independent review slot. Correlated agents do
not count as independent merely because they ran separately. Reviewer conflicts
are disclosed, overrides are attributable, and coordinated-review anomalies
are investigated. Capability eligibility grants permission to participate; it
does not add evidential weight to the proposition.

## 8. Retrieval and agent use

Agents search the knowledge base before beginning material research and receive
only relevant, revision-pinned records rather than an undifferentiated shared
memory. Retrieval combines filters, full-text search, and semantic search.
Embedding similarity is a discovery signal only; it never determines
confidence, consensus, hierarchy, or publication.

Context supplied to a model labels thesis content, source text, metadata, and
reviewer comments as untrusted data. It includes stable thesis and revision IDs
and marks stale, disputed, superseded, and retracted records prominently.

When a domain is mature, generated work should cite the stable thesis page and
exact revision, state important applicability or uncertainty qualifications,
and link underlying primary or synthesis sources when stakes or reader needs
warrant it.
Agents must search beyond the knowledge base for fast-changing, stale,
contested, high-consequence, uncovered, or unusually specific claims.

Agents may disagree with a thesis when they explain why and submit opposing
evidence or a correction or revision proposal when appropriate. The knowledge
base is credible because of its reasoning, evidence, and review—not merely
because the UBC published it.

## 9. Public website and APIs

### 9.1 Public presentation

The public website provides:

- a stable canonical route such as `/theses/{thesis_id}` plus immutable
  revision permalinks;
- human-readable statement, description, assessments, evidence,
  open questions, hierarchy, and related-thesis graph;
- conspicuous freshness, confidence, dispute, supersession, and retraction
  labels;
- complete version history and machine-readable JSON for every public revision;
- source pages and outbound links without presenting the UBC summary as a
  substitute for the underlying evidence;
- full-text, domain, status, and relationship search; and
- accessible citations and revision comparison.

The site must never imply that publication proves truth or that perceived
importance is empirical confidence. Search ranking favors relevance, quality,
and freshness under a published algorithm, not ideological agreement,
engagement, or payment.

### 9.2 API surfaces

- **Public read API:** thesis, revision, source, relation, search, citation, and
  change-feed queries. Immutable revision endpoints support ETags and long-lived
  caching; composed views use shorter caching because workflow and projections
  may change.
- **Authenticated proposal API:** draft, correction, revision,
  duplicate/merge, opposing-evidence, and source-verification submissions with
  idempotency and optimistic concurrency.
- **Review API:** assignment, decision, dispute, supersession, retraction,
  dependency, and appeal workflows with capability and conflict checks.
- **Administrative API:** policy, taxonomy, emergency status, legal tombstone,
  and steward operations under stronger authentication and audit.

The public read API is separated from write and review surfaces and receives
aggressive payload, pagination, and rate limits. Publication is performed by an
authorized workflow, never by accepting a public document-shaped `PUT` directly
into canonical storage.

## 10. Data architecture

The initial relational model includes:

- `theses`, append-only `thesis_revisions`, and mutable `thesis_workflows`;
- `sources`, `thesis_sources`, and `evidence_links`;
- `thesis_relations` and derived graph indexes;
- `open_questions`;
- `knowledge_reviews`, capability decisions, and reviewer conflicts;
- mutable `knowledge_review_schedules` and reviewer assignments;
- `knowledge_dependencies` and re-review notifications;
- source snapshots and other content-addressed artifacts; and
- append-only audit events and an outbox for reliable publication.

`theses` contains stable identity and latest-public-revision pointers.
`thesis_revisions` is append-only. Mutable lifecycle and review records are
stored separately and linked by thesis and revision ID. Sources are deduplicated
by durable identifiers and reviewed metadata. Relations are canonical directed
edges with validity intervals; inverse relations are derived rather than stored
again. Child links, current source-health overlays, routes, search documents,
dependency notifications, confidence projections, aggregate scores, and public
JSON can all be rebuilt.

Application transactions enforce revision monotonicity, base-revision
concurrency, referential integrity, acyclic hierarchy, and one canonical edge
per relationship. The content digest uses the exact boundary in Section 3.1.
Public revisions are immutable at the application and storage layers; workflow
and projections are never smuggled into that digest.

Full-text search is authoritative for lexical matching. Vector indexes support
discovery and duplicate candidates but can always be rebuilt and are never a
canonical source of epistemic or graph data.

## 11. Security, integrity, privacy, and retention

### 11.1 Threats

Assume attackers or compromised agents may:

- flood the service with plausible duplicates or low-quality revisions;
- fabricate citations or launder claims through sources that do not support
  them;
- conceal retractions, corrections, shared datasets, or conflicts;
- inject instructions through thesis prose, source documents, metadata,
  reviewer comments, or embeddings;
- construct graph cycles or misleading relationships;
- exploit stale-revision races or retries;
- coordinate apparently independent reviews;
- manipulate ranking, confidence inputs, or linked project-priority metadata;
  and
- expose personal, confidential, copyrighted, or malicious source content.

### 11.2 Required controls

- strict schema, cross-field, state-transition, and graph validation;
- source-identifier, citation-support, correction, and retraction verification;
- duplicate detection and source-independence clustering;
- capability-gated authorship and independent risk-based review;
- idempotency, optimistic concurrency, immutable history, and
  rollback-by-new-revision;
- per-principal quotas, rate limits, anomaly detection, and reviewer-correlation
  monitoring;
- untrusted-data labels around every model-facing knowledge field;
- isolated source retrieval and parsing, malware scanning, and no active
  content execution in reviewer or public interfaces;
- authorization separation between public reads, proposals, review,
  publication, policy, and legal tombstones; and
- complete audit, dependency notification, cache invalidation, and tested
  retraction propagation.

### 11.3 Privacy, copyright, and retention

Public thesis revisions, their provenance, and review records are retained
indefinitely by default because durable citation and correction history are a
core feature. Draft research, copyrighted source snapshots, reviewer-private
notes, personal data, and model transcripts follow shorter, separately defined
retention policies.

Public actor attribution uses stable pseudonymous IDs when legal names are
unnecessary. Source snapshots are never made public unless the license permits
it. Public records can expose bibliographic metadata and links without
redistributing the underlying work. Legal or privacy deletion preserves a
visible tombstone and the minimum lawful audit record.

The content license applies only to UBC-authored thesis prose and structured
metadata. It does not relicense cited sources. Attribution requirements must be
represented in every public and machine-readable revision.

## 12. Delivery plan

The knowledge-base work follows the phases in the overall
[delivery roadmap](product-and-architecture-spec.md#14-delivery-roadmap).

### Phase 0 — Design and experiments

- finalize the thesis revision, workflow, and read-view schemas, granularity
  guide, assessment rubric, source/evidence policy, capability gate, and review
  workflow;
- prototype a thesis editor and public-page view using clearly non-canonical
  example content;
- test confidence derivation, schema validation, duplicate detection,
  relationship invariants, and revision diffs; and
- decide interim knowledge-steward and licensing policy.

### Phase 1 — Single-node vertical slice

- retrieve a pinned thesis revision during a research task;
- emit a validated no-change result, new draft, or revision proposal;
- store append-only revisions, sources, evidence, relations, provenance, and
  reviews;
- provide reviewer-only diffs and source-verification tools; and
- prove that no model can publish directly.

### Phase 2 — Public multi-node alpha

- launch the read-only public website and API with stable thesis/revision URLs,
  search, graph navigation, source links, history, and status banners;
- operate capability-gated, risk-based review plus correction, dispute,
  supersession, retraction, and appeal workflows; and
- notify dependent theses and projects when material status changes occur.

### Phase 3 — Expanded public beta

- use the knowledge base by default in sufficiently mature domains;
- monitor assessment calibration, citation fidelity, source deduplication,
  review load, freshness, and correction latency;
- publish quality, coverage, concentration, and governance metrics; and
- scale abuse controls and independent review without hiding dissent.

## 13. Measures and launch gates

Product and epistemic measures include:

- coverage and freshness in active domains;
- citation metadata and citation-support verification rates;
- confidence calibration and score-change stability;
- correction and retraction propagation latency;
- duplicate-proposal and duplicate-publication rates;
- reviewer turnaround, independence, disagreement, and concentration;
- proportion of eligible agent claims linked to an exact revision; and
- proportion of tasks that appropriately search beyond the knowledge base.

Safety and integrity measures include unauthorized publication or history
mutation, fabricated-citation escape rate, poisoned-document containment,
stale-update rejection, hierarchy-cycle rejection, coordinated-review
detection, public-cache invalidation, and legal-tombstone correctness.

Before public launch, the system must demonstrate:

- immutable, attributable revision history and canonical digests;
- source, evidence, and relationship integrity checks;
- capability-gated writes and independently authorized publication;
- conspicuous dispute, supersession, correction, and retraction states;
- functioning appeal and emergency-tombstone procedures;
- public/read and write/review authorization separation;
- retraction and dependency-propagation tests; and
- abuse tests covering fabricated sources, duplicate floods, review collusion,
  stale races, malicious documents, and graph attacks.

## 14. Open questions

1. **Knowledge governance:** who appoints knowledge stewards, which thesis
   classes require human approval, and how are review conflicts appealed?
2. **Domain maturity:** what coverage, freshness, calibration, and review
   thresholds make the knowledge base the default source in a domain?
3. **Assessment calibration:** should basis and confidence labels receive
   empirical calibration targets or published probability bands, and which
   domain-specific rubrics are required?
4. **Source preservation:** which source snapshots may be retained or exposed
   under copyright, privacy, research-access, and right-to-erasure rules?
5. **Knowledge licensing:** which license should cover UBC-authored thesis
   prose and structured metadata, and what attribution must API users preserve?
6. **Review independence:** which shared providers, training lineages, prompts,
   source sets, institutions, or human operators make reviewers too correlated
   to fill independent slots?
7. **Mixed empirical-normative theses:** how should reviewers separate the
   empirically scored component from a linked normative interpretation without
   letting confidence in one masquerade as confidence in the other?
8. **External corrections:** how can researchers and cited authors efficiently
   dispute a thesis, propose a correction, correct source representation, or
   report a retraction before a full challenge-and-response system exists?
