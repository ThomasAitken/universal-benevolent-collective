# Pilot runbook

Run from the repository root on macOS with Docker Desktop running, or on
Linux with Docker and cgroup v2. Install the locked development dependencies
and pull the [pinned analysis image](../src/ubc_node/pilot/analysis_runner.py).

```sh
task sync
docker pull docker.io/library/python@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34
```

On a Mac, the workflow runs in your terminal and the analysis worker runs
inside Docker Desktop's Linux environment. The browser session and ClamAV
verdict are simulated.

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

Use a dedicated workspace and pass it after the task separator in every
command. For this run, set:

```sh
workspace=.ubc/pilot-mac
```

The run and inspect commands print JSON records. Copy record IDs from each
cycle's `data` fields for the later inspect commands. Replace the uppercase
placeholders in the examples with those values. Failures print a diagnostic
and exit with a nonzero status.

1. Run cycle 1.

   ```sh
   task pilot -- run --workspace "$workspace"
   task pilot -- verify --workspace "$workspace"
   task pilot -- inspect --workspace "$workspace" --record BASELINE_PROPOSAL_ID
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
   task pilot -- review --workspace "$workspace" --cycle 1 \
     --reviewer YOUR_REVIEWER_ID --decision approve \
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
   task pilot -- run --workspace "$workspace" --cycle 2
   task pilot -- inspect --workspace "$workspace" --record UPDATE_SOURCE_ID --artifact
   task pilot -- inspect --workspace "$workspace" --record UPDATE_ANALYSIS_ID
   task pilot -- verify --workspace "$workspace"
   ```

   The driver reopens the catalogue, finds the first source's dependants and
   prior review, and checks that the forecast exists before staging the
   outcome. The new analysis finds two negative intervals and keeps both
   vintages. The Brier scores reproduce as `0.06250000` for the forecast and
   `0.25000000` for the frozen 0.5 baseline. A bounded follow-up charter
   stays in the programme queue for human admission. That charter proposes a
   further descriptive comparison of vintages.

4. Review the update. Inspect the raw CSV, the analysis, and the record
   named in cycle 2's `proposal` field. Use the review command from step 2 with
   `--cycle 2` and the actual decision. A request for changes or rejection
   keeps the baseline revision current. Inspect the ID returned by the review
   command for the recorded decision and the ID in cycle 2's `followup` field
   for the pending charter.

## Pause, revoke, retry, and diagnose

- Each cycle parks at review and exits after retiring its browser. To stop in
  mid-run, press Ctrl-C. Rerun the same cycle in the same workspace to
  continue from committed records, with a fresh browser session if evidence
  capture was interrupted. A successful quarantine release reuses its
  receipt and avoids a second scan for the same task, session, locator,
  filename, and digest. Use a fresh workspace if the scan or canary policy
  changes. Review retries require the original decision and explanation.
- To withdraw scope, use the relevant cycle in the revoke command. This can
  run from a second terminal while the pilot is active.

  ```sh
  task pilot -- revoke --workspace "$workspace" --cycle 2 \
    --reason 'Owner withdrew the grant.'
  ```

  The browser is retired on observing revocation. Already
  running operator acquisition or analysis can finish within their component
  limits. The catalogue rejects their new publications under the revoked
  task. Committed records remain available to inspect. Use a new workspace
  for a fresh fixture run.
- For browser failures, inspect the named `browser-v*.jsonl` attempt journal.
  Treat page content as untrusted. The evidence record includes the retained
  text, digest, locator, time, session, and rights. For scanner failures check
  the quarantine acquisition journal and private socket. Get the receipt
  from the source record or the `acquisitions` table in
  `quarantine/downloads.sqlite3`, then inspect it with `--receipt`.

  ```sh
  task pilot -- inspect --workspace "$workspace" --receipt RECEIPT
  ```

  For worker failures, run `task pilot -- journal --workspace "$workspace"`
  and check the local engine and pinned image against the bounded failure
  code. Avoid adding credentials or private canary values to operator review
  text.
- Archive the private workspace if you need reproducibility evidence.
  Otherwise remove that dedicated workspace after the pilot exits. A killed
  process can leave an incoming fixture file or quarantine staging until
  retry. Run `uv run ubc quarantine --store "$workspace/quarantine" recover`
  if you need quarantine cleanup before retry. On an isolation test host,
  follow the [isolation fixture teardown](trusted-core-and-isolation.md#c03-acceptance) for
  the fixture containers, network, and image.

## Provision a Linux VM on macOS

On Apple Silicon, use Lima 2.3 or later with the repository's VZ template.
The template pins a Debian 13 ARM64 image by SHA-512 and gives the guest
two CPUs, 4 GiB of RAM, and a 20 GiB disk. Lima's plain mode disables host
filesystem mounts, dynamic port forwarding, and SSH agent forwarding.
Lima exposes its management SSH connection on host loopback.

From the repository root on the Mac, run:

```sh
brew install lima
sh vm/start-mac.sh
```

The script starts `ubc-pilot`, installs the guest's container and Python
tooling, and copies the current Git commit into the guest over SSH. The
`ubc-node` account owns a detached checkout and its locked Python environment.
The script runs `ubc doctor` there before updating `~/checkout` to point to
that revision. Rerun the command to start the VM again or provision a new
host commit. A new commit gets its own checkout. If a checkout for the same
commit has local edits, provisioning stops and keeps the edits. Pass an
instance name to the script to create a separate VM from the template.

Open a shell as the node account and inspect the provisioned revision:

```sh
limactl shell --workdir /tmp ubc-pilot -- sudo -iu ubc-node
cd ~/checkout
git rev-parse HEAD
uv run --locked ubc doctor
```

Exit the guest shell and use `limactl stop ubc-pilot` to stop the VM while
keeping its disk. This setup prepares the Linux guest. Continue with the
deployment procedure to install the browser launcher and configure its
image, network, and scanner.

## Intended Linux deployment

Install the matching launcher and current browser image on the intended
Linux node. Follow the [browser isolation fixture setup](trusted-core-and-isolation.md#c03-acceptance)
to provide the private HTTP/TLS fixture and a matching manifest. Run the
[scanner gate](trusted-core-and-isolation.md#c04-download-quarantine),
[analysis container gate](#analysis-container-gate), and
[browser research gates](trusted-core-and-isolation.md#c05-acceptance), including
the ordinary node and root operator egress runs. Those component gates cover
injection, malformed inputs, canaries, resource limits, and privileged
component loss. Use the same checkout and runtime configuration in the
following integrated fixture:

```sh
task pilot -- run --workspace .ubc/pilot-deployment \
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

### Run the Linux fixture with rootless Podman

On the Debian guest, use local rootless Podman with `runc` for analysis.
The guest's `crun` version fails to install the analysis worker's seccomp
filter with its zero file-size limit. The browser broker uses rootful
Podman with `crun`. Select `runc` in the node user's containers configuration
and provision the pinned analysis image into that user's image store. The
node user's systemd session needs delegated CPU, memory, and process
controllers to enforce the worker's limits.

If entering the node account with `sudo -iu ubc-node`, enable lingering for
that account as the guest administrator. In the node shell, set the user bus
environment and run the analysis gate:

```sh
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
UBC_ANALYSIS_ENGINE=podman task test-analysis-container
```

Set these paths to the fixture manifest and private ClamAV socket from the
deployment setup. Pass the same settings to both cycles and use the same
analysis engine for verification:

```sh
workspace=.ubc/pilot-deployment
browser_manifest=.ubc/pilot-fixture-manifest.json
scanner_socket=/run/ubc-clamav-fixture/clamd.ctl
task pilot -- run --workspace "$workspace" --engine podman \
  --browser-manifest "$browser_manifest" --scanner-socket "$scanner_socket"
task pilot -- verify --workspace "$workspace" --engine podman
```

Follow the review steps above. After baseline approval, add `--cycle 2` to
the run command. If the scanner uses a dedicated group, open a fresh node
shell after adding the account to that group.

## Analysis container gate

With the pinned analysis image available, run this check against the intended
Linux deployment engine before the integrated fixture.

```sh
task test-analysis-container
```

The gate checks repeatability, code digest, rejection of malformed input, and
the actual worker's network, user, resource limits, read-only filesystem, and
lack of fixture credentials and host authority. The host validates the
bounded worker output and checks task authority again before committing the
analysis. The gate exercises the fixed price method.
