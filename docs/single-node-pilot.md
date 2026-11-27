# Single-node research pilot

The pilot exercises two cycles of a human-supervised electricity-market
research programme. The worked example measures how often five-minute
wholesale prices are negative in one Australian National Electricity Market
region. A charter fixes the region, UTC observation window, price units
(AUD/MWh), approved source, and rights to retain its data. The integrated
fixture uses two synthetic NSW1 releases and a deterministic agent.

Use the [pilot runbook](pilot-runbook.md) to operate the fixture and verify
its output. The [prototype reference](prototype.md) describes the underlying
supervisor, policy, approvals, and audit. The
[isolation reference](trusted-core-and-isolation.md) covers the Linux browser
runtime, egress, and download quarantine.

## How a run works

1. **Admit and choose a project.** An operator admits charters and the owner
   opts in to a bounded set of projects. The agent chooses an eligible
   charter, creating one scoped task with a fixed budget. Follow-up proposals
   queue for human admission. Revocation blocks publication of new results
   under that task, including work that finishes after revocation.
2. **Collect evidence and acquire a source.** In the Linux runtime, the
   browser selects from an approved inventory and captures a bounded text
   snapshot. The evidence record retains the exact locator, retrieval time,
   session, HTTP status, text, digest, and rights. Browser downloads are
   disabled. An operator imports the approved price CSV through quarantine
   with an expected SHA-256. Quarantine checks scope, size, format, digest,
   canaries, and the scanner verdict. The catalogue binds the committed
   receipt to a source version and records its upstream release time,
   rights, and any previous version.
3. **Analyse the pinned source.** A fixed price method runs in a Linux
   container with a digest-pinned Python image, no network or host mounts,
   a read-only root, and resource limits. It rejects missing, duplicate,
   and unaligned intervals in the selected window. Each dataset row retains
   its one-based source CSV line and price in integer millionths of AUD/MWh.
   The analysis manifest links the source, inputs, method, environment,
   parameters, diagnostics, and output digests. The ratio of negative
   intervals to all intervals gives the exact frequency.
4. **Submit a thesis proposal and freeze a forecast.** A proposal cites
   evidence in the pinned source and can draft a thesis, revise a pinned
   revision, or give a reason for keeping it. A human reviews the evidence
   and diff and records the decision, rationale, independence, and
   conflicts. Approval can promote an immutable local revision. The binary
   forecast freezes the cutoff, deadline, named outcome source, resolution
   rule, and Brier scoring method. The submission records a probability
   alongside the fixed 0.5 baseline before the outcome becomes available.
5. **Reopen and update.** After baseline approval, the second fixture process
   finds the prior source's dependants, retains both vintages, reruns the
   analysis, and submits the update for review. It resolves and scores the
   forecast once the named outcome is available and queues a follow-up
   charter. For a direct source correction, the forecast API can append a
   superseding score while retaining the original.

The private SQLite catalogue stores records, dependency links, and audit
events. It retains source vintages, thesis revisions, forecasts, and their
evidence across retries and restarts. A reviewer can rerun the pinned method
and trace a sampled dataset row back to the original CSV. The catalogue
and review fixtures show how to use the operator Python API directly.
[Catalogue examples](../tests/pilot/test_catalogue.py) ·
[Two-cycle fixture](../tests/pilot/test_two_cycle.py)

## Where it runs

- **macOS fixture.** The orchestration and human review run in the terminal.
  Docker Desktop runs the real analysis worker in Linux. The fixture
  simulates the browser session and ClamAV verdict.
- **Linux deployment fixture.** The same workflow uses real Chromium via
  the systemd and Podman launcher, a private egress gate, and a real ClamAV
  daemon. The intended deployment still needs its component checks and
  integrated run with human review. See the
  [deployment procedure](pilot-runbook.md#intended-linux-deployment).
- **Live-source demo.** The integrated driver serves bundled sources. Live
  acceptance is pending and requires a separately reviewed, account-free
  source, access and retention rights, and a configured model run on the
  intended Linux deployment. A live forecast stays pending until the named
  outcome is published. External analysis code and public publication have
  separate gates.

The [product and architecture specification](product-and-architecture-spec.md)
and [knowledge base specification](knowledge-base-spec.md) describe the
broader system.
