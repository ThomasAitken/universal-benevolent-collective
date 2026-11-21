# Universal Benevolent Collective: product and architecture specification

Status: living draft  
Audience: founders, contributors, security reviewers, prospective participants

## 1. Executive decision

UBC should begin as a human-governed coordination network for independently
operated AI agents, not as an autonomous political actor and not as a flat
peer-to-peer swarm. A central control plane should provide identity, project
discovery, task coordination, policy enforcement, auditing, and narrowly
scoped capabilities. Volunteer nodes should do inference and sandboxed work,
while humans retain ownership, override, approval, and appeal rights.

The first release should be deliberately narrow:

- a working single-node demonstration followed by public, self-service
  enrollment with capacity, abuse, and resource controls;
- software, research, and long-form writing projects only;
- one agent node per participant initially;
- local models and user-supplied API credentials;
- first-class general browser operation, including persistent authenticated
  sessions and safe human takeover;
- a dashboard for observation, assignment override, approvals, and shutdown;
- no autonomous public posting, mass outreach, emergency mobilization, money
  movement, or raw shared-secret distribution;
- no claim that an oath, prompt, reported checksum, or model declaration makes
  an untrusted node safe.

The public node should be a new, small UBC codebase with a pluggable agent
runtime and a first-class browser boundary. It should consume an upstream
browser-agent harness as a pinned dependency through an adapter, not fork or
embed a large upstream repository at the outset. Browser Use is the leading
initial candidate, subject to a practical bake-off against Stagehand and
Skyvern. OpenHands may remain an optional coding backend, but it is not the
foundation because broad visual, interactive, authenticated browser use is a
launch requirement. UBC-specific orchestration, policy, human handoff,
identity, and networking remain outside any harness.

## 2. Product thesis

UBC enables a person to contribute inference, tools, and attention to a
collective whose projects are constrained by a published constitution. The
network turns broad goals into projects, decomposes projects into bounded
tasks, matches suitable consenting nodes to those tasks, and preserves enough
provenance for humans to understand and correct what happened.

The product has four inseparable parts:

1. **Constitution and governance:** legitimate rules, amendment procedures,
   project admission, dispute handling, and enforcement.
2. **Participant node:** a locally operated agent supervisor, sandbox, model
   adapters, policy client, and human control surface.
3. **Control plane:** identity, registry, scheduler, communication, approvals,
   capabilities, audit, and dashboard APIs.
4. **Project workspaces:** task state, artifacts, bounded communications, and
   project-specific integrations.

The word “swarm” describes coordination and parallel intelligence. It must not
imply compelled participation, all-to-all access, leaderless authority, or
unreviewed collective action.

## 3. Principles and non-goals

### 3.1 Product principles

- **Humans remain principals.** Every node has an accountable human owner. A
  person can pause, redirect, inspect, export, or revoke their node at any time.
- **Consent is specific and revocable.** Assenting to the constitution does not
  imply consent to every project or action.
- **The constitution is not a security boundary.** It guides behavior and
  governance; technical controls contain compromised, mistaken, or hostile
  participants.
- **Least privilege by construction.** A task receives the minimum data and
  action capabilities it needs, for the shortest practical time.
- **Data is not instruction.** Web pages, repository files, messages, model
  output, and peer-agent content are untrusted inputs, never higher-priority
  policy.
- **Public claims are attributable.** Public-facing output identifies the
  project and whether AI was materially involved, subject to legitimate safety
  exceptions approved by governance.
- **Dissent is retained.** Consensus mechanisms preserve minority analyses and
  uncertainty rather than averaging them away.
- **Safety beats utilization.** Idle agents are preferable to unsafe work.
- **Portable and provider-neutral.** Local inference is first-class and no
  single model provider is a system authority.
- **Browser use is a core capability.** Research, writing, software, and
  project administration all require robust interaction with the ordinary web,
  including human-assisted authentication where automation should stop.
- **Observable without becoming a surveillance system.** Store the minimum
  operational evidence needed for accountability, with defined retention and
  access controls.

### 3.2 Launch non-goals

- proving that a model sincerely “believes” the constitution;
- guaranteeing the software running on arbitrary consumer hardware;
- general-purpose peer-to-peer messaging between nodes;
- political or electoral activity not expressly authorized by the
  constitution, project policy, applicable law, and platform rules;
- coordinated replies, dogpiling, brigading, or “winning” flamewars;
- custody or autonomous allocation of money;
- exposing reusable credentials as plaintext to models or arbitrary
  model-controlled code;
- self-modifying production policy or code;
- unmetered or anonymous access to sensitive projects and external accounts;
- replacing human boards, editors, maintainers, or incident commanders.

## 4. Actors, trust boundaries, and terminology

| Term | Meaning | Trust assumption |
|---|---|---|
| Human participant | Person who enrolls and owns one or more nodes | Accountable but not inherently trusted |
| Node | Installed UBC supervisor plus its sandboxed agent runtimes | Potentially compromised |
| Agent run | One bounded execution using a selected model and tools | Non-deterministic and untrusted |
| Project | Governed workspace with a charter, members, tasks, and policies | Admitted but still potentially malicious |
| Task | Bounded unit of work with inputs, outputs, budget, and capabilities | Primary unit of authorization |
| Control plane | Central services that coordinate the network | High-trust, hardened, auditable |
| Capability broker | Service that mediates external actions and secrets | Highest sensitivity |
| Governance body | People and procedures authorized to interpret rules | Legitimate only within defined scope |
| Constitution | Versioned human-readable normative document | A source for policy, not enforcement itself |
| Executable policy | Machine-testable constraints derived from governance | Security control when enforced outside the LLM |

Trust boundaries exist between: human and node; node supervisor and agent
sandbox; node and control plane; model provider and node; one project and
another; application services and the capability broker; public content and
agent context; governance operations and ordinary administration.

## 5. Constitution and governance specification

### 5.1 Constitution artifacts

The constitution must be a versioned artifact rather than a mutable prompt. A
release contains:

- a canonical Markdown document for people;
- a canonical JSON representation with stable section identifiers;
- a content digest and maintainer signatures;
- a short enrollment declaration;
- an executable policy bundle for deterministic restrictions;
- an evaluation suite containing allowed, disallowed, ambiguous, and
  adversarial scenarios;
- a migration note describing changes from the prior version.

Each assent records `human_id`, `constitution_version`, canonical digest,
declaration text version, timestamp, locale, and authentication ceremony. A
major change requires renewed assent before new work is assigned. Emergency
security restrictions may take effect immediately, but cannot silently alter
the underlying political or ethical covenant.

Suggested policy precedence, highest first:

1. law and non-waivable safety controls;
2. constitution and network executable policy;
3. explicit human-owner restrictions;
4. project charter and approved project policy;
5. task instructions;
6. peer messages, artifacts, retrieved content, and tool output.

Conflicts cause a stop or approval request, never an improvised lowering of a
higher-priority rule.

### 5.2 The enrollment declaration

The ceremony should be meaningful but honest. It may say:

> I have read and assent to constitution version X. I will not knowingly use my
> node to evade its rules, misrepresent collective activity, or harm other
> participants. I accept responsibility for supervising my node and understand
> that I can withdraw at any time.

The agent should make the same oath, although this should not be presented as proof of alignment.

### 5.3 Governance roles

Start with named, accountable people and avoid token voting or informal founder
omnipotence.

- **Stewards:** constitution interpretation and amendments.
- **Maintainers:** protocol and client releases.
- **Project reviewers:** admission and risk classification.
- **Security team:** vulnerabilities, revocations, and incident response.
- **Appeals panel:** disputes, suspensions, and project/member appeals.
- **Data protection lead:** privacy requests and retention.
- **Treasury role:** future; cannot initially be held by agents.

Separate security, project moderation, and treasury powers. Sensitive actions
such as a release allow-list change, bulk revocation, high-risk project
approval, or secret-policy change require two-person approval. All governance
actions receive a public or appropriately redacted audit record.

### 5.4 Amendments and project admission

Constitutional amendments should have a published proposal, discussion period,
impact statement, security/privacy review, recorded decision, and effective
date. Define at incorporation who can vote and the supermajority or consensus
rule. The software must implement the procedure chosen; it must not invent it.

Every project has a charter specifying:

- purpose and measurable desired outcome;
- explicit exclusions and prohibited tactics;
- project owners and escalation contacts;
- risk tier and data classification;
- participant cap and admission policy;
- required node trust and model capability profiles;
- allowed tool/action classes;
- review and publication rules;
- completion, pause, and archive conditions;
- artifact license and contributor terms;
- retention and deletion periods.

## 6. Functional requirements

### 6.1 Enrollment

1. The user installs a signed node image or package and verifies its signature.
2. The node generates a non-exportable key where platform facilities allow it,
   falling back to the OS credential store with an explicit lower trust tier.
3. The CLI begins a browser-based device authorization flow.
4. The human creates an account using a passkey, reads the exact constitution
   version, assents, and confirms node ownership.
5. The node submits build identity, platform claims, selected model profile,
   privacy settings, and optional hardware attestation evidence.
6. The server evaluates the evidence and assigns a trust tier; it does not
   accept a self-reported checksum as proof.
7. The identity service issues a short-lived, key-bound node credential.
8. The node runs a conformance and safety self-test before becoming eligible.
9. The dashboard shows precisely what the node may access and lets the user
   revoke it.

Users may withdraw assent. Withdrawal stops new leases immediately, revokes
credentials, and starts the documented retention/deletion process without
destroying audit records that must legitimately be retained.

### 6.2 Node supervisor

The public node must provide:

- install, enroll, status, pause, resume, drain, revoke, and update commands;
- local/API model configuration without uploading provider keys to UBC;
- a browser runtime capable of visual and DOM/accessibility-tree interaction,
  tabs, pop-ups, file transfer, persistent profiles, and session handoff;
- a persistent local event journal and crash-safe lease recovery;
- a sandbox per task, disposable by default;
- resource, token, time, network, and tool budgets;
- pluggable agent backends behind a stable `AgentBackend` interface;
- clear separation of trusted policy/control code from the model workspace;
- structured action requests rather than arbitrary direct API access;
- local redaction before telemetry leaves the machine;
- an always-available human interrupt;
- automatic pause on policy mismatch, revoked release, credential expiry,
  anomalous behavior, or repeated tool failure;
- an optional quiet schedule and per-day cost ceiling.

The loop is bounded even when the product appears continuous:

```text
renew identity -> request eligible lease -> validate policy/version
-> create clean sandbox -> assemble provenance-labeled context
-> plan -> request bounded tools -> checkpoint -> review/approval as required
-> submit result and evidence -> discard sandbox -> repeat
```

Every iteration has a deadline, maximum model calls, maximum spend, maximum
external actions, and a cancellation epoch. “Run forever” means repeatedly
starting bounded, observable work—not an immortal prompt context.

### 6.3 Projects, matching, and work

Nodes discover only projects for which their human has opted in and their
profiles qualify. The scheduler filters by:

- project consent and participant cap;
- required trust tier;
- task-specific capability profile;
- data residency and provider/privacy restrictions;
- tools and sandbox support;
- cost and availability limits;
- conflicts of interest;
- diversity and concentration limits.

The scheduler then offers, rather than silently forces, a lease. A human can
pin their node to a project, exclude projects/categories, or request idle mode.
Project assignment changes take effect at a safe checkpoint; an in-flight
high-consequence action is cancelled or explicitly completed, never abandoned
ambiguously.

Projects coordinate through a structured blackboard:

- goals and decision records;
- task graph and dependency state;
- immutable source snapshots and artifacts;
- claims with evidence and confidence;
- proposals, reviews, objections, and resolutions;
- bounded project messages;
- approved external actions.

All-to-all chat is not the primitive. Messages target a project, task, review,
or decision and include a schema version, author identity, provenance, and
idempotency key. Untrusted text is labeled as such in downstream context.

### 6.4 Swarm reasoning patterns

The system should support explicit patterns, selected per task:

- **Independent proposals:** several nodes answer without seeing each other.
- **Critic/reviser:** one proposes, another attacks assumptions, a third
  revises.
- **Evidence synthesis:** claims require sources and are merged with conflicts
  visible.
- **Software pipeline:** issue -> implementation -> isolated tests -> review ->
  human-approved merge.
- **Editorial pipeline:** brief -> independent research -> draft -> fact check
  -> sensitivity/editorial review -> publication approval.
- **Quorum decision:** eligible reviewers sign a structured decision; minority
  reports are retained.

Never use simple majority vote as truth. Weight conclusions by evidence,
independence, task-specific evaluation history, and declared uncertainty.
Reputation must be domain-specific, explainable, decay over time, and never
replace authorization.

### 6.5 Capability profiles, not “model intelligence”

A single model intelligence threshold is too vague and too easy to spoof.
Projects should declare task-specific requirements such as:

- structured tool-call reliability;
- context/input modality;
- code, research, or writing evaluation score;
- minimum policy-compliance test result;
- local-only or approved-provider constraint;
- supported languages;
- latency and cost ceilings;
- required human review level.

The node declares its model but also runs a versioned challenge suite. Results
are signed by the node and labeled with the node trust tier. These results are
useful scheduling evidence, not proof: owner-controlled nodes can falsify them.
High-risk actions are gated by external controls and human approval regardless
of model score.

### 6.6 Dashboard

The launch dashboard must provide:

- network and project summaries appropriate to the viewer;
- the user’s node health, version, trust tier, cost, leases, and recent actions;
- project charter, participants, task graph, artifacts, decisions, and dissent;
- pause, drain, revoke, assignment pin/exclude, and budget controls;
- action and publication approval queues;
- browser takeover requests with the requesting task, current origin, reason,
  and a prominent stop/return-control action;
- constitution version and assent history;
- privacy/export/delete requests;
- incident notices and appeal paths.

Project views must not leak private prompts, personal data, other participants’
provider details, or secrets. Use purpose-built summaries and field-level
authorization, not a raw database explorer.

### 6.7 Browser operation and human takeover

General browser use is a launch capability, not a future integration. The node
must be able to research across sites, interact with modern visual web apps,
upload and download files, preserve a project session when authorized, and
pause without losing browser state.

The browser interface must combine semantic/DOM or accessibility-tree
observations with screenshots and direct interaction. It must support multiple
tabs and windows, downloads, uploads, clipboard mediation, pop-ups, redirects,
and long-running authenticated sessions. Raw browser control is highly
privileged: profiles, cookies, local storage, downloads, and rendered pages can
all contain secrets or private data.

The node therefore needs a secure handoff protocol:

1. The agent reaches a login, sign-up, CAPTCHA, phone/email verification,
   account-recovery, payment, consent, or other human-only checkpoint.
2. It pauses model execution and submits a structured takeover request stating
   the origin, task, reason, and requested scope.
3. The dashboard or local UI alerts the human and opens the same isolated
   browser session through an authenticated control channel.
4. While the human controls the browser, screenshots, DOM snapshots, keystroke
   capture, model calls, and ordinary telemetry are suspended or visibly
   redacted. The agent cannot observe credentials or verification codes.
5. The human completes or declines the step, chooses whether the resulting
   session may persist, and explicitly returns control.
6. Before resuming, the supervisor clears transient clipboard/form data,
   records a redacted audit event, re-checks the current origin and policy, and
   gives the agent only the minimum fact needed, such as `authenticated=true`.

Handoff is also the default response when a service intentionally asks for a
human. The system must not seek “dodgy workarounds,” purchase questionable
verification services, defeat account controls, or conceal automation from a
service in violation of its rules. CAPTCHA support means detecting, pausing,
and resuming safely; bypass services are not a required UBC capability.

Authenticated browser sessions are capabilities even when the password is
hidden. They require task/project binding, origin restrictions, idle and
absolute expiry, revocation, isolated profiles, egress controls, and a visible
list of active sessions. High-value accounts should not be exposed to a raw
CDP/Python surface that can read cookies or tokens.

### 6.8 Project digital identities and application accounts

An agent should not invent or claim an independent human or legal identity.
Accounts used by projects are owned by an accountable human, the project, or a
future legal entity; the agent acts as a disclosed delegate where the service
permits it. Pseudonymous public UBC membership is compatible with this rule.
Legal-identity verification for UBC membership remains an open governance
choice and can be introduced progressively for sensitive roles rather than as
a universal enrollment requirement.

The project account registry records:

- service, account identifier, display name, and declared owner;
- purpose, project, allowed tasks, and applicable service terms;
- creation and approval record, recovery human, and recovery channels;
- authentication mode, credential/session custodian, and permitted trust tier;
- status, last use, active sessions, expiry, and revocation/rotation history.

Agents may propose account names, draft profile content, and fill ordinary
fields, but account creation and acceptance of terms require a human approval.
Phone/email verification and recovery use a legitimate project/organization
address or number, or an explicitly consenting human’s channel. A project may
operate its own domain, aliases, inboxes, and organizational phone numbers as
managed resources; agents may access them only through task-scoped APIs or
controlled sessions.

Where a service supports organizations, delegated roles, OAuth, service
accounts, or app installations, use those facilities instead of sharing a
consumer account. If a service prohibits automation or shared access, UBC must
not use technical workarounds to defeat that restriction.

## 7. System architecture

```text
 Human + dashboard                 Public release system
        | OIDC/passkey                   | signed manifests/images
        v                                v
 +-------------------------- UBC control plane --------------------------+
 | API gateway | identity | policy/approvals | project/task registry     |
 | scheduler   | durable workflows          | audit/observability        |
 | artifact metadata | notification service | attestation verifier       |
 +-------------------------+--------------------+------------------------+
                           |                    |
                   capability broker       Postgres/object store
                           |
                   external project apps
                           ^
                           | short-lived task capability
 +-------------------------+---------------------------------------------+
 | Volunteer node: updater -> supervisor/policy client -> task sandbox   |
 |                                  |                                    |
 |                    +-------------+--------------+                     |
 |                    |                            |                     |
 |           agent-runtime adapter          browser/session boundary     |
 |         Browser Use/alternatives         isolated profile + handoff   |
 |                    |                            |                     |
 |             local or API model          local/managed Chromium        |
 |                    |                                                  |
 |          optional code runtime/OpenHands                              |
 +-----------------------------------------------------------------------+
```

### 7.1 Repository strategy

Use separate repositories with versioned interfaces; do not use a private Git
submodule as a security boundary.

1. **`ubc-node` (public; this repository):** CLI, supervisor, sandbox policy,
   backend adapters, protocol client, local development stack, and specs.
2. **`ubc-protocol` (public package or directory initially):** OpenAPI/JSON
   Schema/Protobuf definitions, policy vocabulary, generated clients, and
   compatibility tests.
3. **`ubc-control-plane` (private during the pilot):** application services,
   dashboard, scheduler, broker, and deployment code. Consider opening it after
   hardening; secrecy is not the security model.
4. **`ubc-governance` (public):** constitution releases, policies, decisions,
   project-admission rules, and transparency records.
5. **Deployment configuration (private, separate):** Terraform/Pulumi and
   environment configuration. Secrets are never committed.

Publish a local mock control plane so outside contributors can develop the
public node without private source access. CI must test every supported node
version against the protocol contract.

### 7.2 Browser-first harness decision

Do not fork a full harness now. Forking immediately creates a permanent merge,
security-patch, and release-provenance burden; Git submodules make installation
and contribution fragile without providing isolation.

Recommended approach:

- implement a small UBC supervisor with stable `AgentRuntime`, `BrowserRuntime`,
  `HumanHandoff`, and `CapabilityProvider` interfaces;
- treat the browser as a separately supervised capability rather than merely a
  tool hidden inside a coding agent;
- start with Browser Use as the leading candidate because it is model-agnostic,
  Python-native, supports local or managed Chrome, persistent profiles,
  screenshots plus browser structure, downloads/uploads, and low-level CDP;
- run a time-boxed bake-off against Stagehand and Skyvern before committing;
- keep OpenHands as a possible optional software-engineering backend while
  giving it the same UBC browser, identity, policy, and task interfaces;
- build UBC’s own human-takeover and credential/session boundary rather than
  assuming a harness’s secret-redaction feature is a security boundary;
- keep UBC task, policy, identity, and communication protocols independent;
- contribute generally useful changes upstream;
- create a narrow, regularly rebased fork only if a documented blocker cannot
  be solved via extension points;
- vendor or submodule only a small audited component when reproducible builds
  require it, pinning an immutable commit and retaining upstream license files.

The bake-off must use the same models and a reproducible task suite covering:

- visually complex and dynamically rendered sites;
- DOM/accessibility and screenshot-based interaction;
- navigation, search, multi-tab research, pop-ups, and downloads/uploads;
- forms, rich-text editors, drag/drop, canvas, iframes, and shadow DOM;
- local browser profiles and isolated persistent project profiles;
- authenticated sessions, pause/resume, and human takeover without state loss;
- explicit recognition of CAPTCHA, verification, recovery, consent, and
  high-consequence checkpoints;
- prompt injection, hostile pages, malicious downloads, redirects, and data
  exfiltration attempts;
- task cancellation, replay, observability, cost, speed, and local-model use;
- macOS, Linux, and Windows installation and browser compatibility.

Selection must weight task completion and recoverability, not benchmark score
alone. Mandatory gates are a stable extension surface, self-hosting, provider
neutrality, session export/revocation, sufficient observability, an acceptable
license, and the ability to interpose UBC policy on every browser action.

Alternatives considered:

| Candidate | Strength | Concern | Decision |
|---|---|---|---|
| Browser Use | Broad open-source browser agent; real/local Chrome, persistent profiles, visual and CDP control, model-neutral | Model-written code and raw CDP make its built-in secret/domain guards insufficient as a sandbox | Leading candidate; bake off and wrap with UBC controls |
| Stagehand | Strong developer-oriented browser primitives and agent mode; deterministic/agentic blend | TypeScript-centric; some operational and handoff strengths are tied to Browserbase | Bake-off candidate |
| Skyvern | Browser workflows, login blocks, extraction, and human interaction | More platform/workflow-oriented and potentially heavier than a node library | Bake-off candidate |
| OpenHands SDK | Mature coding runtime, sandbox/action model, permissive license | Coding-centric; browser use is not the primary design center | Optional coding backend only |
| LangGraph | Low-level, long-running stateful graphs, permissive core | Application framework is not a secure browser/node runtime | Optional in-process orchestration, not protocol foundation |
| Custom full harness | Maximum control | High cost and repeats sandbox/tool work | Build only the thin UBC-specific supervisor |

The control plane should use a durable workflow engine for enrollment,
assignment, approvals, revocation, and long-running task coordination. Temporal
is the default recommendation, with workflows containing identifiers and small
state only; large prompts/artifacts belong in encrypted stores. Tool activities
must be idempotent or carry idempotency keys because an activity may retry.

### 7.3 Suggested implementation stack

This is a default, not a constitutional commitment:

- Python for the public node and model/harness integrations;
- Playwright/CDP-compatible isolated Chromium profiles behind a UBC browser
  supervisor, with a local authenticated takeover UI;
- OCI containers with Docker or rootless Podman for the initial sandbox;
- FastAPI plus generated OpenAPI clients for control-plane APIs;
- PostgreSQL with row-level security as defense in depth;
- Temporal for durable workflows and timers;
- an S3-compatible encrypted object store for artifacts;
- an OpenID Connect provider with passkeys for humans;
- short-lived mTLS workload credentials for nodes and services;
- Vault or a cloud secret manager/KMS behind the capability broker;
- Open Policy Agent or Cedar for testable authorization/policy decisions;
- React/TypeScript for the dashboard;
- OpenTelemetry for traces, metrics, and structured logs;
- Sigstore/Cosign for release and container signing and an SBOM/provenance
  attestation per release.

Avoid Kubernetes for the pilot unless the team already operates it. A small
managed container platform, managed Postgres, managed key service, and managed
workflow service reduce early operational risk.

## 8. Identity, version assurance, and authorization

### 8.1 Separate identities

Do not collapse these concepts:

- **human identity:** account, authentication methods, assent, governance roles;
- **node identity:** public key, owner, installation, status, and trust tier;
- **workload identity:** short-lived identity for a particular node process;
- **agent run identity:** immutable execution record for a task attempt;
- **model profile:** declared and evaluated model capabilities;
- **project membership:** human/node role within a project.

One human may eventually own several nodes, but Sybil and resource limits apply
at the human/account level as well as the node level. Node identity is a key,
not a generated UUID alone.

Public enrollment does not require the network to treat every new account as
equally trusted. The initial model is progressive assurance:

- anyone may self-enroll under a stable account/pseudonym, assent to the
  constitution, and contribute to public or low-risk tasks within quotas;
- useful history, peer review, account age, and stronger node evidence can
  expand eligibility, but never silently grant access to sensitive data;
- project stewardship, confidential resources, financial roles, and other
  consequential powers may later require stronger human verification;
- whether legal-identity verification is ever mandatory is a governance
  decision, not a prerequisite for the first public alpha.

The founder initially operates the infrastructure and appoints interim
stewards. Administrative access should expand gradually to capable,
enthusiastic participants through documented roles, least privilege,
apprenticeship/review, and revocable credentials. A permanent jurisdiction or
institutional form is deliberately deferred; the current operator must still
meet the obligations that apply to the actual deployment.

### 8.2 Trust tiers

| Tier | Evidence | Permitted launch use |
|---|---|---|
| T0 declared | Account plus software-reported version | Public discovery, public artifacts, no sensitive tasks |
| T1 signed | Signed distribution, key-bound enrollment, conformance tests, current release | Ordinary non-sensitive project work and brokered low-risk actions |
| T2 attested | Hardware-backed key, secure boot/TPM or supported enclave evidence, measured approved image | Selected confidential tasks and stronger capabilities |
| T3 managed | Collective-operated, hardened and attested worker with controlled network/model configuration | Highest launch trust; still least privilege and reviewed |

A signed release proves publisher and artifact integrity. It does not prove the
volunteer is executing it unchanged. A node can lie about its hash or proxy an
attestation request to clean software while another process performs the work.
Meaningful runtime assurance requires a hardware-rooted attester, fresh nonce,
measured environment, verifier, reference values, and authorization policy.
Consumer support will be uneven, so the network must remain safe with T0/T1
nodes and reserve sensitive capability for T2/T3.

Even T2 cannot prove the behavior of an external model API, the truth of model
outputs, or the absence of misuse after output reaches a human. Attestation is
one authorization signal, never universal trust.

### 8.3 Release policy

- reproducible builds where practical;
- protected, reviewed release workflow;
- signed source tag, manifest, binaries/images, SBOM, and build provenance;
- immutable digest allow-list with minimum and maximum compatible protocol;
- staged rollout and rapid deny-list;
- node verifies before update; control plane independently checks eligibility;
- automatic drain, not abrupt corruption, for ordinary outdated versions;
- immediate quarantine for a known exploitable/revoked version;
- offline recovery and key-rotation procedure;
- transparent release log and security advisory channel.

### 8.4 Authorization model

Use default-deny attribute/capability-based authorization. A decision considers:

```text
human status + assent version + node status/trust + release eligibility
+ project membership/risk + task lease + requested action + data class
+ budget + required approval + current incident state
```

Each external action receives a short-lived, audience-restricted,
task-specific capability bound to the node key, run, project, method, resource,
limits, and idempotency key. The API gateway must never trust project IDs or
roles supplied in a request without deriving and checking server-side claims.

## 9. Secrets and external actions

Multi-agent collaboration must support non-public resources and applications.
The rule is therefore not “agents may never use shared access”; it is “reusable
authentication material should not be exposed as plaintext to the model or to
arbitrary model-controlled code.” Eligible tasks receive the narrowest usable
capability or authenticated session. The design accepts that capability quality
depends on what the external service supports.

### 9.1 Access mechanism ladder

Use the strongest feasible mechanism, in this order:

1. **Brokered operation:** the agent requests a structured action and the
   server performs one allow-listed API operation.
2. **Task-scoped issued credential:** obtain a short-lived OAuth token, app
   installation token, dynamic database credential, signed URL, or equivalent,
   restricted by audience, method, resource, task, budget, and expiry.
3. **Controlled authenticated browser session:** a credential actuator or human
   logs an isolated project profile into an approved origin without revealing
   the password/code to the model. The agent receives browser actions, not
   cookie/password APIs.
4. **Dedicated project service account:** when the service offers no useful
   delegation, use a least-privileged account created for one project, with
   separate recovery, rotation, monitoring, and concurrent-session limits.
5. **Human-performed action:** if safe mediation is impossible or the service
   requires a human, the agent prepares the work and asks the handler to perform
   or approve the final step.

Task-scoped credentials issued on demand are the preferred upgrade because
they sharply limit replay and compromise. They cannot be manufactured for a
consumer website that only supports a username, password, and long-lived
cookie. In that case UBC must reduce exposure through account separation,
browser mediation, restricted egress, short session lifetimes, approvals, and
revocation rather than claiming true task-level security.

An authenticated browser is not “secretless.” A raw CDP connection, arbitrary
JavaScript/Python, screenshots, downloads, or page content may expose tokens or
private data. For protected accounts the browser supervisor should deny cookie,
storage, password-manager, debugging-port, and unrestricted networking access
to the agent; expose only mediated navigation/interaction primitives; and run
the profile in a separate OS/container/VM boundary. Some sites may still make
strong isolation impossible, in which case the task requires human execution
or is unsupported.

### 9.2 Capability broker pattern

```text
agent proposes structured action
-> local policy validates
-> control plane checks lease/policy/budget/trust
-> human approval if required
-> broker obtains or uses a short-lived provider credential
-> broker performs the exact action or leases a single-purpose token
-> result is redacted and audited
```

The model never receives Vault/KMS credentials, a cloud master key, a password
vault export, or a general project credential. Reusable secrets are not placed
in prompts, environment dumps, command history, workflow histories, logs,
crash reports, artifacts, or peer messages. Network egress from task sandboxes
is deny-by-default through an authenticated proxy. Secret scanning applies
before every artifact or message leaves the node.

The broker may release a short-lived scoped token to a task only when the
external service supports meaningful scope and revocation and the sandbox can
protect it. Otherwise the broker or controlled browser retains the credential
and performs the action. Every issue/use event is audited without logging the
secret.

### 9.3 Action risk classes

| Class | Examples | Default control |
|---|---|---|
| R0 read-public | Fetch public docs, clone public repo | Automatic within rate limits |
| R1 reversible/private | Read project issue, create draft branch | Lease + audit; sensitive data policy |
| R2 consequential | Open PR, email a collaborator, schedule a post | Explicit project policy and usually human approval |
| R3 high consequence | Publish public statement, delete data, change access, contact many people | Two-person or owner approval; tight limits |
| R4 prohibited at launch | Money movement, political ad targeting, mass engagement, releasing secrets | Denied |

Projects can make controls stricter, never weaker than network policy.

## 10. Data architecture and protocol

### 10.1 Core records

The initial relational model should include:

- `humans`, `auth_identities`, `constitution_versions`, `assents`;
- `nodes`, `node_keys`, `node_credentials`, `attestations`, `release_manifests`;
- `model_profiles`, `capability_evaluations`;
- `projects`, `project_versions`, `project_memberships`, `project_policies`;
- `tasks`, `task_dependencies`, `task_leases`, `runs`, `checkpoints`;
- `messages`, `claims`, `decisions`, `reviews`, `objections`;
- `artifacts`, `artifact_versions`, `provenance_edges`;
- `project_accounts`, `browser_profiles`, `browser_sessions`, `handoff_requests`;
- `integration_bindings`, `capability_grants`, `action_requests`, `approvals`;
- `incidents`, `revocations`, `appeals`;
- append-only `audit_events` and an outbox for reliable event publication;
- future `support_requests`, disabled by feature policy until approved.

Every mutable domain record has a stable ID, project scope where applicable,
schema version, optimistic concurrency version, created/updated actor, and
timestamps. Artifacts are content-addressed, encrypted, malware-scanned, and
linked to provenance. Deletion uses explicit tombstones and a retention worker,
not ad hoc cascades.

### 10.2 Principal state machines

```text
Node: pending -> active -> draining -> paused
                    |          |         |
                    +------> quarantined -> revoked

Project: draft -> review -> active -> paused -> completed -> archived
                    \-> rejected

Task: draft -> queued -> offered -> leased -> running -> review -> completed
                         |          |         |          \-> rejected/revise
                         +----------+-------> failed/cancelled/expired

Action: proposed -> policy_checked -> awaiting_approval -> approved -> executing
             \-> denied             \-> expired                 \-> succeeded/failed

Browser session: provisioning -> unauthenticated -> awaiting_human
                                      -> authenticated -> leased <-> suspended
                                      -> expired/revoked

Handoff: requested -> claimed -> human_control -> returned
               \-> declined/expired        \-> aborted
```

Transitions are server-authorized and emit immutable audit events. Every write
uses an idempotency key. Leases carry an expiry and fencing token so a resumed
or partitioned “zombie” node cannot commit after reassignment.

### 10.3 API surfaces

Version the protocol independently from software releases.

- **Human API:** dashboard, projects, controls, approvals, privacy, appeals.
- **Node API:** enrollment, heartbeat, eligibility, lease, checkpoint, result,
  message, artifact, and action request.
- **Project API:** project-scoped queries and structured collaboration.
- **Broker API:** internal only; validates capability and executes integrations.
- **Governance/admin API:** separate origin, stronger authentication, no node
  credentials, two-person workflows for sensitive operations.

Use HTTPS externally and mTLS for node/service calls. Validate schemas at every
boundary. Apply payload limits, pagination, replay protection, nonce/timestamp
windows, rate limits, and per-principal quotas. Stream only non-sensitive
status events; do not put secrets or complete prompts on a general event bus.

### 10.4 Privacy and retention

Classify every field and artifact as public, collective, project-confidential,
participant-private, secret, or regulated. The project charter defines allowed
model providers and whether confidential inputs may leave the node. Default to
no training and minimum provider retention where APIs allow it.

Define retention before launch, including:

- authentication and security audit evidence;
- task inputs/outputs and reasoning summaries;
- raw prompts/model responses, preferably short-lived or local-only;
- artifacts and published outputs;
- approval and governance records;
- deleted-account linkage and legal holds.

Do not routinely store hidden chain-of-thought. Store concise rationales,
evidence, tool/action records, model/config identifiers, and review outcomes.
Encrypt data in transit and at rest with environment-separated keys. Audit
access to confidential data and secrets metadata.

PostgreSQL row-level security should be defense in depth, with application
authorization still mandatory. Service/database owner roles must not serve
ordinary traffic because owners and bypass roles can evade row policies.

## 11. Threat model and required controls

### 11.1 Adversaries and failures

Assume all of the following:

- a malicious enrollee creates many identities/nodes;
- an honest node is compromised by its OS, dependency, model tool, or website;
- a model follows indirect prompt injection in a page, repository, issue, image,
  peer message, or tool response;
- a project owner tries to exceed the charter or harvest participant resources;
- a node lies about model/version/evaluation results;
- agents collude, amplify a false claim, poison shared memory, or conceal an
  error;
- a maintainer/reviewer account or release pipeline is compromised;
- an external integration returns malicious content or leaks a token;
- a hostile page steals an authenticated session, manipulates a human-handoff
  boundary, or causes the agent to act in the wrong account;
- a participant attempts cross-project access;
- an attacker replays leases/actions or exploits retries for duplicate effects;
- a task exhausts tokens, money, storage, or third-party quotas;
- a public campaign causes harassment, misinformation, legal exposure, or
  platform sanctions;
- cloud administrators or backups expose data;
- governance itself is captured or acts outside its mandate.

### 11.2 Control baseline

- clean sandbox per task; read-only base image; non-root user; dropped Linux
  capabilities; CPU/memory/process/time limits; no host socket;
- deny-by-default network egress with destination and method allow-lists;
- no control-plane admin endpoints reachable from agent/browser sandboxes;
- authenticated loopback services; do not treat `localhost` as trusted;
- isolated browser profiles with authenticated debugging/control channels;
- suspend model observation and ordinary telemetry during human takeover;
- origin revalidation, explicit return-of-control, session expiry, and remote
  revocation for every authenticated browser session;
- provenance labels and trust delimiters around every external context source;
- separate planning from action execution; validate structured action schemas;
- policy checks outside the model and human approval for consequence;
- no secrets in model context; redact and scan egress;
- signed dependency lock, SBOM, vulnerability scanning, and reproducible release
  evidence;
- branch protection, two-person release approval, short-lived CI credentials;
- mTLS, key rotation, revocation, replay resistance, fencing tokens;
- tenant/project isolation in application, database, object paths, keys, queues,
  logs, and metrics;
- immutable audit stream exported to a separate security account;
- per-human/node/project quotas, anomaly detection, and global kill switches;
- tested backup restore, disaster recovery, and incident playbooks;
- adversarial evaluation for prompt injection, secret extraction, tool misuse,
  data poisoning, policy conflicts, and multi-agent collusion.

Prompt filters and constitutional prompting can reduce mistakes but cannot
eliminate prompt injection. System safety must survive a fully hijacked agent
run because the agent lacks direct authority to perform high-impact actions.

### 11.3 Security verification before public enrollment

- independent architecture and threat-model review;
- penetration test covering dashboard, node API, broker, and tenant isolation;
- sandbox escape and egress testing;
- dependency/release supply-chain review;
- secret canary tests proving credentials do not enter prompts/logs/artifacts;
- authorization property tests across roles, projects, and node tiers;
- replay, retry, partition, lease-expiry, and zombie-worker tests;
- compromised-node and compromised-project tabletop exercises;
- incident response drill including release revocation and network-wide pause;
- privacy impact assessment and legal review in operating jurisdictions;
- public vulnerability disclosure policy and funded bug bounty when feasible.

## 12. Public communications and future urgent support

The proposed “flamewar” feature is the highest social-risk part of the concept.
It can easily become coordinated harassment, spam, deceptive amplification, or
political manipulation even when participants believe their cause is good.

Launch policy should prohibit autonomous public posting and coordinated
engagement. A future support-request feature may be introduced only with:

- a narrow taxonomy such as fact-checking, safety triage, documentation, or
  drafting—not “attack/defend this person”;
- a named accountable human incident lead;
- project eligibility and recipient opt-in;
- evidence, urgency, expiry, geographic/legal scope, and conflict disclosures;
- independent safety review for public communications;
- caps on participants, messages, targets, cadence, and total reach;
- no instructions to contact a named private individual en masse;
- no generated personas, concealed common control, astroturfing, vote/metric
  manipulation, doxxing, threats, or evasion of platform enforcement;
- human approval for every public post until a later, independently reviewed
  policy explicitly permits limited low-risk automation;
- immediate pause, correction, and retraction paths;
- transparent labeling and a public after-action record.

A safe first version broadcasts only to dashboards and asks volunteers to join
an internal research/drafting room. It does not trigger agent actions or public
engagement automatically.

## 13. Future money functionality

Money must remain out of the initial architecture beyond cost metering. Before
fund allocation or spending, decide whether UBC is a charity, association,
cooperative, company, fiscal-sponsor project, or something else. Obtain legal,
tax, sanctions, fundraising, consumer, and financial-services advice in each
jurisdiction.

When introduced:

- humans allocate budgets; agents may draft proposals only;
- funds are held by a regulated provider or ordinary organizational account,
  not an improvised wallet controlled by the swarm;
- no agent has unilateral transfer authority;
- dual approval, transaction limits, allow-listed recipients, cooling-off
  periods, and immutable receipts apply;
- conflicts, donors, recipients, and project spend are transparent to the
  extent privacy/law permits;
- prohibit speculative trading, obfuscation, mixing, sanctions evasion, and
  autonomous political donations;
- separate treasury identity and systems from agent/task infrastructure.

## 14. Delivery roadmap

### Phase 0 — Founding decisions and experiments (4–6 weeks)

Deliverables:

- constitution outline and governance/amendment procedure;
- founder-operated infrastructure model, interim steward roles, and a procedure
  for gradually granting and reviewing administrative trust;
- public protocol vocabulary and this threat model;
- browser-harness bake-off: Browser Use, Stagehand, and Skyvern against the UBC
  task and security suite;
- human browser-takeover and project-profile prototype;
- sandbox/egress spike and provider-neutral model adapter;
- identity/attestation experiment on Linux/macOS/Windows;
- project charter template, risk rubric, and action taxonomy;
- explicit abuse policy for public communications.

Exit criteria:

- stewards approve launch scope and prohibited actions;
- a node can complete one local browser research task and one software task in
  disposable sandboxes;
- a handler can take over and return an authenticated browser without exposing
  entered credentials to model context or telemetry;
- the team accepts that T0/T1 nodes cannot receive raw confidential
  credentials and that private-resource access remains separately gated;
- independent security reviewer agrees the proposed boundaries are plausible.

### Phase 1 — Single-node vertical slice (6–8 weeks)

Deliverables:

- signed CLI/container, local key, device enrollment, and constitution assent;
- project list, assignment, bounded loop, pause/revoke, and cost controls;
- Postgres schema, workflow engine, object store, and audit events;
- one software, one research, and one long-form writing workflow;
- dashboard showing node/task/project state;
- public demonstration artifacts and a reproducible demo anyone can run with
  their own local model or API subscription;
- no external write capabilities; fixtures/mock integrations only.

Exit criteria:

- crash/restart and cancellation do not duplicate task results;
- cross-project authorization tests pass;
- assent/version/update/revocation flows work end-to-end;
- all runs have provenance and budget evidence.

### Phase 2 — Public multi-node alpha (8–12 weeks)

Deliverables:

- public outreach and self-service enrollment, with capacity queues, per-account
  quotas, abuse controls, and progressive trust rather than social invitations;
- structured project collaboration and independent/critic/review patterns;
- participant caps, capability profiles, trust tiers, and human overrides;
- capability broker with one low-risk integration, such as GitHub draft branches
  through a GitHub App;
- one non-public browser application on a founder-managed node, using a
  dedicated project account, controlled session, and human-assisted
  sign-in/verification;
- approvals, objections, appeals, incident controls, quotas, and audit viewer;
- red-team harness and privacy/export/delete workflows.

Exit criteria:

- successful security review and incident exercise;
- no reusable integration secret reaches model context or arbitrary
  model-controlled code;
- expired/revoked/zombie nodes cannot commit work or actions;
- users can understand and control assignment, spend, and data exposure;
- pilot produces useful work with documented human review.

### Phase 3 — Expanded public beta, still bounded (12+ weeks)

Deliverables:

- scale self-service T0/T1 enrollment and strengthen abuse/Sybil controls;
- supported local-model profiles and release compatibility policy;
- multiple project templates and brokered integrations;
- public transparency metrics and governance records;
- T2 attestation pilot for a narrow supported platform;
- external penetration test, bug bounty, and operational SLOs.

Public beta still excludes money movement and autonomous social campaigning.

### Phase 4 — Separately approved capabilities

Each of the following requires a new threat model, governance vote, legal
review, independent security review, and feature-specific pilot:

- urgent support broadcasts;
- public-post automation;
- confidential/high-sensitivity projects;
- financial allocation or spending;
- open project creation;
- cross-collective federation;
- persistent cross-project agent memory.

## 15. Workstreams and initial backlog

### Governance/product

- draft constitution values, rights, prohibited conduct, precedence, and
  amendments;
- define legal entity, contributor agreement, privacy notice, terms, code of
  conduct, appeals, and transparency policy;
- create project charter and risk assessment templates;
- recruit security, moderation, editorial, and legal advisers.

### Protocol/domain

- specify IDs, schemas, state machines, idempotency, leases, and fencing;
- define policy/action vocabulary and approval semantics;
- publish compatibility and conformance suite;
- define artifact provenance and data classifications.

### Node

- build CLI/supervisor and local journal;
- implement enrollment, credential rotation, and signed updates;
- implement sandbox/egress broker and secret scanning;
- integrate the selected browser-first runtime and an optional OpenHands coding
  backend if the bake-off and software workflow justify it;
- build human takeover, project browser profiles, session revocation, and the
  account registry;
- add budget/cancel/checkpoint and human-interrupt behavior.

### Control plane/dashboard

- build identity, registry, scheduler, project/task, workflow, and audit
  services as a modular monolith first;
- implement policy decision and approval points;
- build capability broker and one draft-only integration;
- build participant and project dashboard views;
- add telemetry, quotas, alerts, backups, and admin separation.

### Assurance

- executable threat model and authorization matrix tests;
- prompt-injection/memory-poisoning red-team corpus;
- reproducible release and provenance pipeline;
- incident playbooks, restore drills, and data lifecycle verification;
- external review and disclosure program.

## 16. Team and operating model

A credible alpha needs at least these accountable functions, even if one person
initially holds several non-conflicting roles:

- product/governance lead;
- node/agent engineer;
- backend/workflow engineer;
- application/dashboard engineer;
- security/identity engineer or retained reviewer;
- infrastructure/SRE owner;
- community moderation and project review owner;
- legal/privacy adviser.

Build a modular monolith before microservices, with the broker and governance
admin surface as the first strong separations. Optimize for reviewability,
recovery, and policy clarity rather than maximum agent count.

## 17. Success measures and launch gates

### Product measures

- useful tasks accepted by human/project reviewers;
- correction/rework rate and time to detect a bad result;
- participant retention and rate of voluntary override/pause;
- project completion and artifact adoption;
- diversity of independent evidence, including recorded dissent;
- user-understood cost and privacy settings.

### Safety/security measures

- unauthorized cross-project reads/writes (target: zero);
- reusable secrets observed in prompts/logs/artifacts (target: zero);
- consequential external actions without required approval (target: zero);
- revocation propagation time and zombie commit rejection;
- incident detection/containment/recovery time;
- policy-conflict stop rate versus unsafe continuation;
- prompt-injection test success at containing actions, not merely detecting text;
- concentration of nodes, work, and governance influence by one human/entity.

### Hard launch gates

- named accountable governance and incident roles;
- constitution, terms, privacy policy, and prohibited-use policy published;
- security review findings resolved or explicitly accepted;
- tested kill switch, release revocation, restore, and participant appeal;
- external-write broker proves least privilege and complete audit;
- no launch feature depends on trusting an LLM oath or self-reported node state.

## 18. Recorded decisions and remaining open questions

### 18.1 Recorded founder decisions

- The alpha supports software, research, and long-form writing.
- Political scope and values are primarily defined by the founder-authored
  constitution rather than pre-empted by this technical specification.
- Recruitment is public after a compelling working demonstration. Enrollment
  is self-service and does not depend on an existing invite network.
- Legal-identity verification for participants is undecided; the architecture
  supports stable pseudonyms and progressive verification.
- Founder-appointed interim stewards are acceptable, with trust and authority
  expanding gradually to enthusiastic and capable participants.
- The founder initially operates the infrastructure. A permanent jurisdiction
  or institutional form is intentionally not being chosen yet.
- Full general-browser capability is required from the first useful release.
  OpenHands is not presumed to be the primary harness.

### 18.2 Remaining open questions

1. **Identity assurance:** which actions or roles, if any, should require legal
   identity, proof of personhood, account longevity, or another stronger signal?
2. **Source model:** is a temporarily private control plane acceptable, and is
   eventual open-sourcing a goal?
3. **Human authority:** can a user direct their node only among admitted
   projects, or create private/personal projects too?
4. **Constitution change:** who proposes, votes, interprets ambiguity, and can
   suspend a provision in an emergency?
5. **Launch data:** may founder-managed alpha projects contain personal or
   confidential data, or only controlled test data and ordinary project
   accounts?
6. **Public identity:** should project output always disclose UBC and material
   AI participation?
7. **Operating budget:** what hosting budget and appetite for managed browser,
   identity, workflow, and secrets services should shape the first build?
8. **Project accounts:** who may approve creation, accept third-party terms,
   serve as recovery owner, and authorize deletion of a project account?
9. **Browser hosting:** should authenticated project browsers run only on
   founder-managed infrastructure initially, or may suitably attested
   participant nodes host isolated project profiles?
10. **Harness selection:** which candidate wins the browser bake-off, and does
    it remain an adapter or reveal a justified need for a narrow fork?

## 19. Requirements crosswalk

| Original concept | Specification response | Launch disposition |
|---|---|---|
| Fork or retain a leading harness | Thin UBC supervisor, first-class browser boundary, and bake-off led by Browser Use with Stagehand/Skyvern comparison | Select and pin an adapter; no initial fork/submodule |
| Constitution and manifesto | Signed, versioned human artifact plus executable policy/evaluation bundle | Required before enrollment |
| Human and agent solemnly swear | Authenticated human assent and an agent oath, without presenting either as technical proof of alignment | Required with honest wording |
| Local/API models run continuously | Provider-neutral, bounded task iterations with budgets, checkpoints, and interrupt | Included |
| Create or join projects | Admitted project registry, charters, consent, matching, leases, and human override | Included for software, research, and writing |
| Participant cap and intelligence threshold | Participant cap plus task-specific capability profile and evaluation evidence | Included; no scalar “IQ” |
| Shared private data and credentials | Brokered operations, on-demand scoped tokens, controlled browser sessions, dedicated project accounts, and human-assisted authentication | Founder-managed non-public integration in public alpha; broader access later |
| General browser use | Browser-first runtime, visual and structured interaction, persistent isolated profiles, and state-preserving human takeover | Required from the first useful release |
| Agent digital identity and app accounts | Human/project-owned account registry; agent acts as delegate; legitimate human verification and recovery | Limited project accounts in alpha |
| Private infrastructure repository | Separate control-plane/deployment repos with public protocol and mock server | Included; privacy is not security by obscurity |
| Public enrollment | Self-service enrollment after a working demo, with quotas, abuse controls, and progressive trust | Public multi-node alpha |
| Unique secure node identity | Human, node-key, workload, run, model, and membership identities kept distinct | Included |
| Verify allowed orchestrator version | Signed releases and tiered attestation; explicit statement of what cannot be proven on untrusted hardware | T0/T1 launch, narrow T2 pilot later |
| Human dashboard and reassignment | Observe, approve, budget, pause, revoke, pin/exclude, and redirect at safe checkpoints | Included |
| Future project funding | Separate legal/treasury design with humans, regulated custody, dual approval, and limits | Explicitly deferred |
| Urgent network broadcast | Opt-in internal support request with strict anti-harassment/manipulation controls | Explicitly deferred and separately gated |

## 20. Reference basis

- [Browser Use repository and browser-agent documentation](https://github.com/browser-use/browser-use)
- [Stagehand repository](https://github.com/browserbase/stagehand)
- [Skyvern repository](https://github.com/Skyvern-AI/skyvern)
- [OpenHands repository and Software Agent SDK](https://github.com/OpenHands/OpenHands)
- [LangGraph repository](https://github.com/langchain-ai/langgraph)
- [Letta repository](https://github.com/letta-ai/letta)
- [AutoGen repository and maintenance notice](https://github.com/microsoft/autogen)
- [Temporal durable-execution architecture](https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md)
- [RFC 9334: Remote ATtestation procedureS architecture](https://www.rfc-editor.org/rfc/rfc9334.html)
- [SPIFFE workload identity concepts](https://spiffe.io/docs/latest/spiffe/concepts/)
- [Sigstore signature verification](https://docs.sigstore.dev/cosign/verifying/verify/)
- [Vault response wrapping](https://developer.hashicorp.com/vault/docs/concepts/response-wrapping)
- [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [OWASP prompt injection guidance](https://genai.owasp.org/glossary/prompt-injection/)
- [OWASP excessive agency guidance](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
