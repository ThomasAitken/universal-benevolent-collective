# C07 two-cycle pilot fixture

Run `task pilot` from the repository root on macOS with Docker Desktop
running, or on Linux with Docker and cgroup v2. Use `task sync` to install
the locked development dependencies and provision the
[pinned C06 analysis image](c06-research-records.md#analysis-deployment-gate).
On a Mac, the workflow runs in your terminal and the analysis worker runs
inside Docker Desktop's Linux environment. The browser session and ClamAV
verdict are simulated. You can exercise both cycles and their human review
checkpoints without setting up a separate Linux machine.

The [deployment acceptance run](#intended-linux-deployment) uses real
Chromium through the Linux systemd and Podman launcher and a real ClamAV
daemon. The browser inventory, supervisor, event writer, quarantine,
catalogue, and analysis runner are shared. Neither fixture run needs a model
account or a public network destination.

## Sources and configuration

The fixture uses CC0-1.0 synthetic releases for NSW1. It fixes two
five-minute UTC settlement intervals beginning at 00:00 on 1 January 2026,
with prices in AUD/MWh. The charter rejects missing, duplicate, and unaligned
intervals. The first CSV has one negative interval. The next vintage changes
the sign on CSV line 2. The synthetic logical clock controls the forecast
cutoff, deadline, and release dates. The browser and quarantine journals
record their actual acquisition times separately.

The browser inventory has one page per cycle, at
`https://approved.ubc.test:18443/pilot/v1` and `/pilot/v2`. The CSV locator
`https://data.example/prices.csv` identifies the synthetic publisher in the
catalogue. The operator fixture stages the bundled CSV locally and imports it
by pinned digest. The workspace records the browser mode, browser image, and
scanner mode. Use a separate workspace for a different deployment,
configuration, or fixture run.

## Run and inspect

The default workspace is `.ubc/c07`. Pass `--workspace PATH` after the task
separator to use a different one. All commands print a JSON record or a
diagnostic with a nonzero exit status.

1. Run cycle 1.

   ```sh
   task pilot
   task pilot -- verify
   task pilot -- inspect --record proposal:task-c07-v1
   ```

   The deterministic agent chooses the NSW1 charter from two eligible
   projects. The catalogue records the owner grant, project, and task budget.
   The browser run uses that task's budget. The fixture commits the browser
   evidence, versioned CSV, dataset, analysis manifest, thesis proposal, and
   forecast. The forecast asks whether v2 has two negative prices. Its
   probability comes from a fixed demonstration rule that raises the 0.5
   estimate to 0.75 after observing a negative interval.

2. Review the baseline as a human. Check the proposal against its diff,
   browser evidence ID and journal, dataset, and source. The verification
   command reruns the analysis, checks the retained input and audit digest,
   and shows the sampled dataset row alongside the original CSV line. Record
   your own review decision, rationale, independence basis, and conflicts.

   ```sh
   task pilot -- review --cycle 1 --reviewer YOUR_REVIEWER_ID --decision approve \
     --reason 'State what you verified against the original source.' \
     --independence 'Describe your relationship to the proposal author.'
   ```

   Use an identifier of at least eight characters and repeat `--conflict`
   for disclosed conflicts. The other decisions are `request_changes` and
   `reject`. Only approval advances the baseline checkpoint. Review
   decisions are terminal for a proposal. Start a fresh workspace for another
   fixture attempt after a baseline rejection or request for changes.

3. Start cycle 2 in a fresh process.

   ```sh
   task pilot -- run --cycle 2
   task pilot -- inspect --record source:fixture-nsw-prices:v2 --artifact
   task pilot -- inspect --record analysis:task-c07-v2
   task pilot -- verify
   ```

   The driver reopens the catalogue, finds the first source's dependants and
   prior review, and checks that the forecast exists before staging the
   outcome. The new analysis finds two negative intervals and keeps both
   vintages. The Brier scores reproduce as `0.06250000` for the forecast and
   `0.25000000` for the frozen 0.5 baseline. A bounded follow-up charter
   stays in the programme queue for human admission. That charter proposes a
   further descriptive comparison of vintages.

4. Review the update. A second reviewer can inspect the raw CSV and analysis
   before opening `proposal:task-c07-v2`. Use the review command from step 2
   with `--cycle 2` and the actual decision. A request for changes or
   rejection keeps the baseline revision current. Inspect
   `review:proposal:task-c07-v2` for the recorded decision, and
   `followup:task-c07-v2` for the pending charter.

## Pause, revoke, retry, and diagnose

- Each cycle parks at review and exits after retiring its browser. To stop in
  mid-run, press Ctrl-C. Rerun the same cycle in the same workspace to
  continue from committed records, with a fresh browser session if evidence
  capture was interrupted. A successful quarantine release reuses its
  receipt and avoids a second scan for the same task, session, locator,
  filename, and digest. Use a fresh workspace if the scan or canary policy
  changes. Review retries require the original decision and explanation.
- To withdraw scope, run `task pilot -- revoke --cycle 2 --reason 'Owner withdrew the grant.'`
  with the relevant cycle. This can run from a second terminal while the
  pilot is active. The browser is retired on observing revocation. Already
  running operator acquisition or analysis can finish within their component
  limits. The catalogue rejects their new publications under the revoked
  task. Committed records remain available to inspect. Use a new workspace
  for a fresh fixture run.
- For browser failures, inspect the named `browser-v*.jsonl` attempt journal.
  Treat page content as untrusted. The evidence record includes the retained
  text, digest, locator, time, session, and rights. For scanner failures check
  the quarantine acquisition journal and private socket. Get the receipt
  from the source record or the `acquisitions` table in
  `quarantine/downloads.sqlite3`, then run `task pilot -- inspect --receipt RECEIPT`.
  For worker failures, run `task pilot -- journal` and check the local engine
  and pinned image against the bounded failure code. Avoid adding
  credentials or private canary values to operator review text.
- Archive the private workspace if you need reproducibility evidence.
  Otherwise remove that dedicated workspace after the pilot exits. A killed
  process can leave an incoming fixture file or quarantine staging until
  retry. Run `uv run ubc quarantine --store WORKSPACE/quarantine recover`
  if you need quarantine cleanup before retry. On an isolation test host,
  follow the [C03 teardown](trusted-core-and-isolation.md#c03-acceptance) for
  the fixture containers, network, and image.

## Intended Linux deployment

Install the matching launcher and current browser image on the intended
Linux node. Follow the [C03 fixture setup](trusted-core-and-isolation.md#c03-acceptance)
to provide the private HTTP/TLS fixture and a matching manifest. Run the
[C04 scanner gate](trusted-core-and-isolation.md#c04-download-quarantine),
[C06 analysis gate](c06-research-records.md#analysis-deployment-gate), and
[C05 browser gates](trusted-core-and-isolation.md#c05-acceptance), including
the ordinary node and root operator egress runs. Those component gates cover
injection, malformed inputs, canaries, resource limits, and privileged
component loss. Use the same checkout and runtime configuration in the
following integrated fixture:

```sh
task pilot -- run --workspace .ubc/c07-deployment \
  --browser-manifest .ubc/c03-fixture-manifest.json \
  --scanner-socket /run/clamav/clamd.ctl
```

Keep the same workspace, manifest, and scanner settings when running cycle 2.
Pass `--engine` on worker commands if the deployment uses a compatible
engine other than Docker. The Linux fixture uses real Chromium and ClamAV
when those options are supplied. Check the recorded browser and scanner
modes. Run the verification and both human reviews. Preserve the workspace
and component gate results, record reviewer time, and obtain an independent
review of the exercised boundaries. Record the anonymous source policy and
[residual HTTPS restrictions](trusted-core-and-isolation.md#private-egress-gate)
before configuring live retrieval.

Live-source acceptance remains pending. It needs a separately reviewed
account-free source, confirmed access and artifact-retention rights, a
configured model run, and recorded source and tool limitations. Keep a live
forecast pending until its named outcome is published. Use those deployment
and live results to record the go/no-go decision for a private multi-node
trial.
