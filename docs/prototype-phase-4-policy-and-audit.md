# Prototype Phase 4: Policy and audit

Phase 4 adds a fail-closed policy implementation and a separate one-shot human
approval boundary. The model still only proposes actions. It cannot approve
its own action or call the browser without passing through the supervisor.

## Semantic capabilities

`PrototypePolicyEvaluator` applies a task-local `PrototypePolicyConfig` with an
explicit origin allow-list and a control for each semantic capability. Risk,
browser mechanism, capability, and control are deliberately separate:

- **Risk** describes the expected consequence: R0, R1, or R2.
- **Mechanism** describes how the browser performs it: open, fill, click, and
  so on.
- **Capability** names the authority needed: navigate, edit a draft, submit a
  form, send a message, or another bounded effect.
- **Control** says whether that capability is automatic, requires one-time
  approval, or is denied for this task.

The default capability policy is:

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
| Upload a file | `upload` | R2 | One-time approval; direct file selection is not yet exposed |

Any capability can be made stricter for a task. An explicit automatic control
can also pre-authorize a bounded R2 workflow—for example, submitting a search
form repeatedly during a research task—without pretending that the submission
has become R0 or R1. The audit trail still records `risk=R2`, capability
`submit_form`, and `control=automatic`.

Clicks therefore do not have a single risk class. A click must include an
`interaction_effect`: navigation maps to R0, a reversible local interaction to
R1, and submission/message/account/upload effects to R2. A missing or unknown
effect fails closed. The free-form action description is never used to grant
authority.

Payments, account creation, public posting, and recovery changes are denied
without an override path. Upload remains unavailable as a direct browser
operation; an interaction declared as an upload is R2, but the current browser
surface exposes no arbitrary file-selection capability. Unknown, malformed,
or internal browser operations fail closed. Navigation and takeover resume are
checked against the same normalized origin allow-list used by the browser
adapter.

The R0/R1/R2 scheme is the deliberately smaller prototype vocabulary. The
long-term specification's R3/R4 distinctions remain useful for later network
policy, multi-person authorization, and capabilities that are not exposed in
this prototype.

## Approval is not takeover

Approval and browser takeover have different authority and are implemented as
different protocols:

- `CliActionApprover` shows a redacted, immutable proposal and requires an
  explicit `approve` or `deny`. Approval is bound to one run-local action ID,
  applies once, and grants no reusable capability.
- `CliTakeoverHandler` suspends the model and telemetry while a human directly
  controls the visible browser for authentication or another human-only step.

If a capability configured for one-time approval has no approver, approval
fails, the browser is not called, and the denial is audited. This is not limited
to R2: a task can require approval for an R0 or R1 capability as a stricter
local policy. Approval-handler errors, explicit denials, cancellation, and
duration expiry also prevent execution. A takeover does not implicitly approve
a later action; policy is rechecked after control returns.

## Correlated audit trail

Every model proposal receives a run-local `action_id`. That identifier joins
separate append-only facts rather than collapsing intent, authorization, and
effect into one ambiguous event:

```text
action_proposed
-> policy_decided
-> approval_requested -> approval_decided        (when required)
-> browser_action_started -> browser_action_completed
```

The existing `step_failed` event records an execution failure and carries the
same action ID when one exists. Policy events include the risk class, semantic
capability, selected control, and stable rule ID. A `browser_action_started`
event is written immediately before the browser call, so a crash between
dispatch and completion does not look like an action that was never attempted.
Phase 5 will add restart reconciliation and idempotency for consequential
actions.

Audit rendering removes fill values and strips URL credentials, queries, and
fragments. The exact proposal remains in memory for execution and approval,
but secret-like values are not copied into proposal, approval, or execution
events. Browser observations remain task evidence and are not treated as
instructions.

## Deliberate limits

This phase does not claim that Python process boundaries, post-navigation
origin checks, or semantic `interaction_effect` labels are a hardened security
boundary. The effect is structured and policy-checked, but is still proposed by
the model. Automatic click capabilities should therefore remain narrow. A
later adapter revision should attach trusted structural evidence to candidates
(for example, link destination versus form association) and keep ambiguous
controls approval-gated.

In addition, Chromium can issue a request before the adapter detects a hostile
redirect. Phase 6 must move browser and network enforcement out of process.

The Phase 4 guarantee is narrower and testable: unsupported and prohibited
actions fail closed; every action is classified by effect and checked against a
task capability control; approval-required capabilities cannot execute without
a fresh positive decision; cancellation is checked after policy and approval;
action values are redacted from audit records; and proposal, policy, approval,
dispatch, and result remain separately attributable.
