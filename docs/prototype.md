# Single-node prototype reference

The repository implements a bounded local runtime for one human-supervised
agent and a repeatable research demonstration. This reference covers the
implemented supervisor, browser tools, policy, and audit. The next milestone
uses these foundations to run a living research programme across two cycles,
with versioned data, reproducible analysis, and thesis review.
[Product and architecture specification](product-and-architecture-spec.md) ·
[Single-node research pilot roadmap](single-node-pilot-roadmap.md)

The central design rule is that a model may propose work but does not receive
direct browser authority. Budgets, policy, approval, browser execution, human
takeover, and audit are separate runtime responsibilities.

## Architecture

```text
task + budget
     |
     v
RunSupervisor <---- AgentBackend <---- structured model provider
     |
     +----> policy evaluator ----> optional one-shot approval
     |
     +----> BrowserSession ----> Stagehand + Chromium
     |              |
     |              +----> optional supervised worker + egress gate
     |
     +----> human takeover in the same visible browser
     |
     +----> append-only audit events
```

The main implementation boundaries are:

| Component | Responsibility |
|---|---|
| `TaskSpec` and `RunBudget` | Objective, allowed duration, steps, model usage, browser actions, downloads, and handoffs |
| `AgentBackend` | Propose one structured action at a time without executing it |
| `RunSupervisor` | Enforce lifecycle, budgets, cancellation, policy, approval, execution order, and terminal outcomes |
| `PrototypePolicyEvaluator` | Classify semantic capability and consequence, then allow, require approval, or deny |
| `BrowserSession` | Expose a narrow browser API without exposing Stagehand, Playwright, selectors, cookies, or CDP |
| `CliActionApprover` | Grant or deny one specific action; approval is not reusable authority |
| `CliTakeoverHandler` | Pause automation while a human directly handles a checkpoint |
| Event sinks | Persist separately attributable proposal, policy, approval, execution, and lifecycle facts |

The contracts are in [contracts.py](../src/ubc_node/contracts.py) and
[browser_contracts.py](../src/ubc_node/browser_contracts.py). Deterministic
fakes exercise the same boundaries in the ordinary test suite.

## Runtime lifecycle and bounded loop

The supervisor owns this lifecycle:

```text
created -> running -> awaiting_human -> running
                   \-> completed | failed | cancelled
awaiting_human --------------------> failed | cancelled
```

Terminal states cannot be reopened. A normal model-driven iteration is:

1. Check cancellation and hard budgets.
2. Capture a bounded browser observation when available.
3. Ask the model backend for exactly one structured `ActionProposal`.
4. Account for reported tokens and estimated cost.
5. Classify and evaluate the proposal outside the model.
6. Obtain one-shot human approval when required.
7. Execute the approved browser action, perform a handoff, or complete.
8. Record each stage separately and repeat.

Budgets cover steps, model calls, tokens, estimated cost, wall-clock duration,
browser actions, downloads, handoffs, and consecutive recoverable failures.
They are checked before work and again after boundaries that can consume time or
usage. Navigation-policy failures, inactive browser sessions, and failures
during takeover are fatal rather than silently retried.

Cancellation is cooperative and checked around model, policy, approval,
browser, handoff, and durable demo checkpoints.

## Models and untrusted context

`StructuredAgentBackend` is provider-neutral. It renders a constrained request,
labels browser observations as `untrusted_external_data`, validates the model's
JSON response, and produces an `ActionProposal`. The model-safe operation set
does not include browser lifecycle operations such as close, revoke, pause, or
resume.

The repository contains OpenAI and Gemini structured-provider adapters. API
keys are read from provider-standard environment variables, are sent in request
headers, and are not copied into prompts, events, evidence, or artifacts.
OpenAI-compatible local endpoints are supported through `--base-url`.

The included `ubc demo` command is intentionally more deterministic than the
generic agent loop: trusted workflow code chooses the browser pages and
checkpoints. A selected hosted model is used only to turn captured evidence
into a structured cited report. It receives no browser or action tools. The
`local` provider performs deterministic fact extraction without an API key.

## Browser capability boundary

The pinned Stagehand 4.4.0 adapter implements this typed surface:

| Capability | Boundary behavior |
|---|---|
| Navigate | Accept only HTTP(S) URLs whose normalized origin is allowed; recheck the final page origin |
| Inspect | Return URL, title, and a size-bounded snapshot marked untrusted |
| Screenshot | Write beneath the task workspace and return relative path, size, and SHA-256 |
| Observe | Convert Stagehand suggestions into opaque, session-local candidate IDs |
| Click and fill | Act only on a current candidate of the correct kind; invalidate candidates after page or tab changes |
| Extract | Accept a constrained schema of string, integer, number, and boolean fields and validate the result |
| Tabs | List, open, switch, and close through opaque tab IDs |
| Download | Confine output to the task workspace, enforce a size limit, and return its hash |
| Lifecycle | Pause, resume, close, revoke, observe safe state, and perform human handoff |

The model-facing surface provides no raw Stagehand or Playwright object,
locator, selector, arbitrary JavaScript, Python, shell, CDP command, cookie or
storage API, password manager, or arbitrary host path. Unsupported operation
names and malformed parameters fail closed.

The adapter itself is not a Python sandbox. The stronger supervised mode moves
the adapter and Chromium authority behind a child-process protocol, described
below.

## Policy, risk, and approval

Policy keeps four concepts separate:

- **Mechanism:** how the browser performs an operation, such as open, fill, or
  click.
- **Capability:** the authority required, such as navigate, edit a draft, or
  submit a form.
- **Risk:** the expected consequence, currently R0, R1, or R2.
- **Control:** automatic, one-time approval, or denied for this task.

The default prototype policy is:

| Expected effect | Capability | Risk | Default control |
|---|---|---:|---|
| Read, inspect, observe, or extract | `read` | R0 | Automatic |
| Open a URL or follow a link | `navigate` | R0 | Automatic within approved origins |
| Capture a screenshot | `capture` | R1 | Automatic and audited |
| Fill a draft field | `edit_draft` | R1 | Automatic and audited |
| Switch or close a tab | `manage_tabs` | R1 | Automatic and audited |
| Download a file | `download` | R1 | Automatic and audited |
| Change reversible page-local UI | `reversible_interaction` | R1 | Automatic and audited |
| Submit a form | `submit_form` | R2 | One-time approval |
| Send a message | `send_message` | R2 | One-time approval |
| Change account state | `change_account` | R2 | One-time approval |
| Upload a file | `upload` | R2 | One-time approval, but direct file selection is not exposed |

A task may make any capability stricter. It may also explicitly pre-authorize a
bounded R2 capability while retaining its R2 classification and audit record.

A click has no universal risk class. Its required `interaction_effect` maps
navigation to R0, reversible page-local changes to R1, and submission,
messaging, account, or upload effects to R2. Missing and unknown effects fail
closed; the free-form action description never grants authority.

Payments, account creation, public posting, and recovery changes are denied
without an override path. Unknown internal browser operations are also denied.

Approval and takeover are deliberately different:

- Approval authorizes one immutable, redacted proposal and is consumed once.
- Takeover suspends automation while the human directly controls the browser.
- Takeover does not approve a subsequent action; policy is rechecked after
  control returns.

## Human takeover

When authentication or another human-only checkpoint is reached:

1. The supervisor stops model calls and enters `awaiting_human`.
2. The browser adapter pauses screenshots, inspection, observation, extraction,
   clicks, fills, and downloads.
3. The CLI displays the task, normalized origin, reason, and instructions.
4. The human uses the same visible Chromium window.
5. The human switches back to the terminal and explicitly enters `return` or
   `decline`.
6. The runtime clears the clipboard without reading it and clears transient
   text/password form fields without returning their values.
7. On return, every open tab's origin and the resume policy are rechecked. On
   decline or mismatch, the browser is revoked.
8. Only a redacted outcome such as `authentication_completed=true` is exposed
   to the agent loop and audit log.

Screenshots, DOM snapshots, keystroke capture, model calls, and ordinary
telemetry remain suspended during takeover. The prototype uses the visible
browser plus CLI prompt; it does not yet provide an authenticated local web UI.

## Supervised browser and egress boundary

`--browser-runtime supervised` uses three local domains. For the Linux
container boundary, see the [isolated browser fixture](trusted-core-and-isolation.md).

```text
agent + policy + workflow
          |
          | versioned, size-bounded browser messages
          v
browser worker process  ------>  HTTP egress gate  ------> approved fixture
  owns Stagehand + Chrome          decides first          denied fixture gets
  owns disposable profile          and audits             zero requests
```

The parent and worker communicate over an inherited socket. Messages have a
protocol version, positive request ID, maximum size, explicit operation
allow-list, typed parameters, and one response per request. Unknown, duplicate,
malformed, and out-of-order messages fail closed. Closing the control channel
revokes the worker; unexpected worker exit is terminal and does not replay the
last action.

Each worker has idle, absolute, and bounded-takeover deadlines enforced with a
monotonic clock. Accepted parent commands may refresh the idle deadline, but
browser background traffic cannot. Expiry denies commands and egress, closes
Chromium, and terminates the worker. Explicit revocation is terminal and
idempotent.

Supervised profiles are disposable. Normal close, expiry, and revocation remove
them; a later supervisor startup removes abandoned profiles whose recorded
owner process is no longer live. Supervised sessions do not inherit
authentication across a worker restart.

The current egress gate is intentionally narrow:

- it is a local HTTP forward proxy for the loopback demonstration;
- it checks normalized scheme, host, effective port, and supported method
  before opening an upstream connection;
- denied redirects receive a local response and the denied fixture records
  zero requests;
- audit data contains session, decision, origin, method, rule, timestamp, and
  reason—not paths, queries, headers, cookies, or bodies;
- HTTPS `CONNECT`, general DNS and internet browsing, WebRTC, QUIC, extensions,
  and other unsupported traffic are not claimed as supported.

Chrome's exact internal debugging port is bypassed so Stagehand can connect to
the browser it owns; other loopback HTTP traffic remains routed through the
gate.

## Audit and restart behavior

Model intent, authorization, approval, dispatch, and observed completion are
separate append-only facts joined by a run-local `action_id`:

```text
action_proposed
-> policy_decided
-> approval_requested -> approval_decided        (when required)
-> browser_action_started -> browser_action_completed
```

Execution failure retains the same action ID. Policy events include risk,
semantic capability, control, and stable rule ID. Fill values and URL
credentials, queries, and fragments are omitted from audit rendering.

Supervised mode adds boundary events for worker start/exit, session lease,
egress decisions, expiry, and revocation. Its demonstration journal is
`browser-boundary-events.jsonl`.

The fixture demonstration separately checkpoints completed workflow steps and
validates their artifacts before skipping them on restart. This avoids
repeating completed downloads, model synthesis, and other checkpointed work.
It does not claim general exactly-once behavior across a crash between a real
third-party side effect and its local checkpoint.

## Repeatable demonstration

The demonstration uses two ephemeral loopback origins: an approved research
fixture and a deliberately denied redirect target. It never submits work to an
external service.

Run the deterministic report model:

```shell
uv run ubc demo --provider local
```

Run through the supervised browser boundary:

```shell
CHROME_PATH=/path/to/chrome \
  uv run ubc demo --provider local --browser-runtime supervised
```

Hosted or OpenAI-compatible report models are optional:

```shell
OPENAI_API_KEY=... uv run ubc demo --provider openai --model gpt-5-mini
GEMINI_API_KEY=... uv run ubc demo --provider gemini --model gemini-3.5-flash-lite

OPENAI_API_KEY=local \
  uv run ubc demo --provider openai \
  --base-url http://127.0.0.1:11434/v1 --model your-local-model
```

The workflow:

1. Exercises a denied redirect.
2. Captures two public research pages as bounded evidence.
3. Captures prompt-injection text as untrusted evidence without following it.
4. Downloads and hashes a fixture document using trusted workflow code.
5. Pauses at a mock login for human takeover.
6. Captures a non-secret authenticated finding after origin and policy checks.
7. Produces structured findings and a Markdown report whose material claims
   link to captured evidence JSON.

The mock login accepts any non-empty throwaway values. When the browser shows
**Private appendix**, switch back to the terminal and type `return`; the
browser does not automatically return authority to the agent.

The default workspace is `.ubc/phase5-demo`:

```text
checkpoint.json                validated completed-step results
events.jsonl                   workflow journal
browser-boundary-events.jsonl  supervised worker, lease, and egress events
downloads/research-note.txt    bounded downloaded document
evidence/E1.json ...           captured evidence and hashes
findings.json                  structured claims and evidence IDs
report.md                      final cited report
fixture-ports.json             stable loopback fixture ports
```

The default in-process demo uses a policy-authorized persistent mock profile
outside the task workspace. Supervised mode always uses a disposable profile,
so a browser restart can require another mock login.

## What the prototype proves

The implemented and tested guarantees are deliberately bounded:

- the model proposes actions but cannot approve or directly execute them;
- budgets, policy, and lifecycle are enforced outside the model;
- unsupported operations and unknown effects fail closed;
- consequential capabilities cannot execute without their configured control;
- raw browser and credential primitives are absent from the model-facing API;
- human-entered credentials are not returned in prompts, normal events,
  screenshots, or report artifacts;
- prompt-injection text cannot widen the fixed demo workflow or destination
  allow-list;
- supervised fixture redirects are denied before the outside server is
  contacted;
- session expiry, revocation, channel loss, and worker failure are terminal;
- material demo claims link to captured evidence.

## Important limitations

This remains a pre-alpha prototype. In particular:

- A child process is fault separation, not an OS sandbox. Other processes
  running as the same user can inspect or interfere with Chromium, its
  temporary profile, or its debugging port.
- Stagehand 4.4.0 cannot authenticate a local Chrome proxy. The gate binds an
  ephemeral loopback port but cannot prove that a connection came from the
  intended Chrome process. A same-user process could send its own allowed-origin
  traffic or create misleading egress events.
- In this demo, a compromised process can bypass the proxy. The separate
  C02 Linux fixture disables external networking at the container boundary.
- Supervised networking supports the local HTTP acceptance fixture, not general
  HTTPS or internet browsing.
- Persistent profiles are not encrypted, and supervised mode deliberately
  avoids persistent authentication.
- Downloads are size-bounded and hashed but are not quarantined, unpacked, or
  malware-scanned.
- Semantic click effects are model-proposed rather than derived from trusted
  browser structure, so automatic click authority should remain narrow.
- There is no authenticated takeover web UI, active-session dashboard, remote
  revocation service, account registry, capability broker, or control plane.
- General exactly-once external effects are not implemented.

Do not use an ordinary personal profile, valuable account, reusable credential,
or real project account with this prototype. Real accounts remain gated on
OS-enforced isolation and egress, authenticated control channels, download
quarantine, encrypted and revocable profiles, and a security-reviewed takeover
surface.

## Verification and development

The ordinary repository check runs formatting, lint, strict type checking,
deterministic tests with 100% statement and branch coverage, and package builds:

```shell
task check
```

Real-browser tests are opt-in:

```shell
CHROME_PATH=/path/to/chrome task test-browser
CHROME_PATH=/path/to/chrome task test-browser-security
```

The browser-security acceptance test loads approved content through the worker
and gate, follows a redirect toward the denied fixture, proves the denied
server received zero requests, and verifies terminal revocation. Tests use only
local fixtures and no real account.
