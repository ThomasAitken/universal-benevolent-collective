# Prototype Phase 5: Repeatable demonstration

Phase 5 packages the single-node work into one restartable vertical slice. It
runs only against two ephemeral loopback fixture origins: one approved research
site and one deliberately unapproved redirect target. It never submits work to
an external service.

## Run it

The offline deterministic report model needs no API key:

```shell
uv run ubc demo --provider local
```

Chrome or Chromium is discovered by Stagehand, or can be supplied explicitly:

```shell
uv run ubc demo --provider local --chrome-path /path/to/chrome
```

To use a hosted model, put its key in the environment—not in a command-line
argument—and select the provider:

```shell
OPENAI_API_KEY=... uv run ubc demo --provider openai --model gpt-5-mini
GEMINI_API_KEY=... uv run ubc demo --provider gemini --model gemini-3.5-flash-lite
```

An OpenAI-compatible local server can be used without changing the workflow:

```shell
OPENAI_API_KEY=local \
  uv run ubc demo --provider openai \
  --base-url http://127.0.0.1:11434/v1 --model your-local-model
```

The default workspace is `.ubc/phase5-demo`. Re-run the same command with the
same workspace to resume. Completed steps whose artifacts still validate are
skipped. Press Ctrl-C to cancel; browser and provider cleanup runs in `finally`
blocks, and the last durable completed step remains resumable.

When the browser shows **Private appendix**, the login has succeeded but the
demo is still deliberately paused. Switch back to the terminal that is running
`ubc demo` and type `return` at the takeover prompt. The browser cannot resume
the agent automatically: explicit return of control is part of the human
takeover security boundary.

## Demonstrated workflow

The command performs these stable, checkpointed steps:

1. Launch a disposable browser and follow a redirect to the unapproved fixture
   origin. The browser session is revoked and the denial is recorded.
2. Open two public research pages and capture bounded evidence records.
3. Capture a page containing indirect prompt injection as untrusted evidence.
   Its text cannot select tools or destinations because browser sequencing is
   trusted workflow code, not model output.
4. Download a bounded document from the approved origin, hash it, and capture
   its contents as evidence.
5. Open a mock authenticated appendix and pause for takeover. The human enters
   any throwaway non-empty username and password directly into the visible
   browser and submits the local form. When **Private appendix** appears, they
   switch back to the terminal and type `return` to explicitly return control.
6. Clear transient form/clipboard state, re-check origin and policy, confirm the
   authenticated page, and capture its non-secret finding.
7. Extract structured findings and render a Markdown report in which every
   material claim links to one or more captured evidence JSON files.

The report model has no browser or action tools. Evidence is labeled
`untrusted_external_data`. The report validator rejects unknown evidence IDs
and evidence without the fixture's explicit factual marker, so the injection
page cannot become a cited material claim.

## Workspace and restart behavior

The workspace contains:

```text
checkpoint.json             durable completed-step results
events.jsonl                append-only step journal
downloads/research-note.txt bounded downloaded document
evidence/E1.json ...        captured source evidence and hashes
findings.json               structured claims and evidence IDs
report.md                   final claim-by-claim cited report
fixture-ports.json          stable loopback ports for restart
```

The authenticated Chromium profile is deliberately outside the task workspace,
under the workspace parent's `browser-data` directory. It is a scoped mock
session capability, not a report artifact. Encryption, expiry, revocation, and
stronger profile isolation remain Phase 6 work.

Each step is checked for cancellation before it starts and after its result is
durably checkpointed. On restart:

- evidence and reports are skipped only when their files and hashes validate;
- the download is not repeated when its file and evidence still validate;
- authentication is checked against the persistent fixture session before a
  new takeover is requested;
- the report model is not called again when the cited report validates.

This establishes at-most-once replay for *completed and checkpointed* demo
steps. It does not claim general exactly-once external effects across a crash
between a third-party side effect and its local checkpoint; later brokered
actions require provider idempotency keys and reconciliation.

## Credential and scope guarantees

The mock login accepts any non-empty values, so the runtime never creates,
stores, displays, or prompts the model with a known password. During takeover,
model calls, screenshots, DOM capture, and ordinary telemetry are suspended by
the Phase 3 protocol. Login-page inspection is discarded rather than persisted;
post-login evidence contains no credential fields. Automated workflow steps do
not submit forms or messages.

The fixture's prompt injection is retained as evidence but cannot change the
hard-coded workflow, allowed origin, policy, or report citation rules. API keys
are read from provider-standard environment variables and are never copied into
task prompts, events, evidence, or report artifacts.

As documented in Phase 1, origin enforcement currently detects a hostile
redirect after Chromium may already have issued its request. The demonstration
proves session revocation and denial, not pre-request network containment.
Phase 6 moves that enforcement outside Chromium.

## Automated proof

The deterministic suite exercises every named cancellation boundary, restarts
the same workspace and verifies that download/handoff/model work is not
repeated, scans persisted files for a secret canary, confirms that injection
text causes no out-of-scope navigation or report claim, validates every report
citation, and covers both allowed and denied redirects.
