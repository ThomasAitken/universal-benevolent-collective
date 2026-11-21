# Prototype Phase 1 Stagehand browser adapter

Phase 1 adds a first browser implementation behind the Phase 0 contracts. It
pins the first-party Python `stagehand` SDK to exactly `4.4.0`; no Stagehand
object, page, locator, selector, browser connection, or CDP endpoint crosses
the UBC boundary.

## Capability surface

The typed `BrowserSession` contract exposes:

| Capability | Boundary behavior |
|---|---|
| Navigate | Only to an explicitly allowed HTTP(S) origin; the final URL is checked after navigation. |
| Inspect | Returns URL, title, and a size-bounded snapshot marked as untrusted. |
| Screenshot | Writes a PNG beneath the task workspace and returns path, size, and SHA-256. |
| Observe | Converts Stagehand actions to opaque, session-local candidate IDs. Selectors remain private. |
| Click/fill | Acts only on a current observed candidate of the correct kind. Navigation or tab changes invalidate candidates. |
| Extract | Accepts a constrained field schema of strings, integers, numbers, and booleans, then validates with Pydantic. |
| Tabs | Lists, opens, switches, and closes tabs through opaque UBC IDs. |
| Download | Clicks an observed candidate, confines output to the task workspace, applies a size limit, and hashes the result. |
| Lifecycle | Supports pause, resume, close, irrevocable revoke, and human handoff. |

The Phase 0 `BrowserRuntime.execute` shape is supported as a narrow JSON bridge
to these typed methods. Unsupported operation names fail closed.

## Deliberately unavailable

The adapter exposes no API for:

- raw CDP connections or commands;
- Stagehand browser, page, context, or locator objects;
- arbitrary CSS/XPath selectors supplied by a model;
- arbitrary JavaScript evaluation;
- Python or shell execution;
- cookie, local-storage, cache, or credential inspection;
- arbitrary host filesystem paths.

Python privacy is not a security sandbox: trusted UBC orchestration code could
still misuse private attributes. The meaningful boundary is the capability
API presented to an untrusted agent run, combined later with process isolation
and network egress controls.

## Profiles

Local sessions use Stagehand's temporary profile behavior by default. A
persistent profile requires all three of:

1. a safe project identifier;
2. a dedicated persistent-profile root;
3. an allowing `PersistencePolicyDecision` for the same project.

The profile is never placed inside an agent's task workspace. Close/revoke
deletes Stagehand temporary profiles; project-persistent state survives only
where policy explicitly authorized it.

## Fixture and verification

The test fixture is an ephemeral loopback website with:

- a form and deterministic controls;
- a same-origin redirect;
- a pop-up link;
- fake verification code `123456`;
- a download response;
- deliberately malicious prompt-injection text.

Unit tests use Stagehand-shaped doubles so AI-dependent `observe` and `extract`
are deterministic in CI. An opt-in smoke test launches the real pinned
Stagehand SDK and exercises navigation, inspection, screenshots, redirects,
tabs, and shutdown against the fixture:

```shell
CHROME_PATH=/path/to/chrome task test-browser
```

Local Stagehand `observe` and `extract` additionally require an explicitly
configured supported model and API key. No model credential is required for
the deterministic smoke test.

## Known security boundary

Origin checks happen before explicit navigation and again after navigation,
including redirects and newly discovered tabs. A hostile redirect can still
cause Chrome to make a request before the final origin is detected and the
session is revoked. Strong prevention requires the later deny-by-default
network proxy/egress boundary; this adapter does not claim to provide it.
