# C06 local research catalogue

C06 provides a trusted operator Python API in `ubc_node.pilot` for a single
programme workspace. The component gate exercises synthetic data. C07 will
connect the API to the agent and
browser workflow. Keep the catalogue, checkout, schemas and container engine
under operator control. A model caller can bind an owner grant and task to
specific choices, pinned reads and proposal submissions. The operator retains
source acquisition, review, scoring, and engine authority.

## Work with a local catalogue

1. Open `ResearchCatalogue(Path(".ubc/research"))` in a private directory.
   Create a programme with `programme` and admit a charter with `admit`.
   Supply the owner, admitting reviewer, objective, `PriceWindow`, approved
   source URL and retained-data rights. The charter fixes a NEM region,
   half-open UTC window of up to seven days, five-minute settlement intervals,
   and prices in AUD/MWh. Admission and owner opt-in are separate records.
2. Use `opt_in` to record the owner's eligible projects and `choose` to claim
   one of them. Use the resulting `TaskSpec.task_id` as the C04 task identifier
   in `ubc quarantine import`. Pass a committed receipt to `register_source`
   with the exact source version, upstream release time, change description,
   and previous source record ID when applicable. The API verifies that the
   receipt belongs to the task, project and approved locator.
3. Call `analyse(task_id, source.id, AnalysisRunner())`. Follow the manifest's
   `dataset` ID with `get` to read the release and its bytes. Each row contains
   the interval, integer millionths of AUD/MWh, and one-based source CSV line.
   The diagnostic counts give negative intervals and total intervals, so the
   exact frequency is their ratio. The method rejects missing, duplicate,
   and unaligned intervals in the selected region and window. Use
   `rerun=True` to verify that the pinned input and checked-out method still
   produce the same release and manifest. Keep the matching code revision and
   pinned container image available for reproducibility.
4. Open `ThesisReview(catalogue, Path("docs"))` with the versioned schemas in a
   trusted checkout. Use `pin` to retrieve the exact base revision for an update
   or no-change decision. Use `submit` with an accepted analysis, rationale,
   trusted author identity, and optional v1 draft. The draft cites the
   registered source snapshot and an evidence locator such as a CSV line or
   window. The operator inspects the stored diff and follows the source and
   analysis IDs before calling `review`. Approval promotes a revision into the
   local catalogue, or confirms the no-change result. The reviewer records
   independence, conflicts and rationale. A request for changes can be
   followed by a fresh task and proposal. This pilot supports standalone theses
   in one project with one local source per draft.
5. Use `freeze_question` and `submit_forecast` from `ubc_node.pilot.forecasts` to
   freeze a threshold question and file an integer probability in basis
   points. The submitted analysis must already exist by the cutoff. The
   operator must withhold the outcome from the research context until after
   the deadline. The named source needs an upstream availability time after the
   deadline, based on the operator's source records. Run `resolve_forecast`
   with the outcome analysis once available. To re-score from a direct
   correction, provide the prior score ID as `supersedes`. Scores use exact
   decimal arithmetic for the forecast and the 0.5 baseline.
6. On a new source release, use the previous source ID with `affected` after
   reopening the catalogue. The result includes transitive users of that
   source. Use `journal` to retrieve the ordered `RunEvent` records. Use
   `follow_up` and `pending_followups` for suggested projects and
   `admit(..., followup=proposal.id)` for human admission. Use `revoke` to
   prevent later work from being accepted under a task. Accepted records and
   their audit events survive retries and restarts.

The [catalogue fixture](../tests/pilot/test_catalogue.py) shows the API calls,
including how to build a v1 draft and reproduce a scored forecast.

## Analysis deployment gate

Use a Linux container engine with cgroup v2 resource enforcement. Provision
the [pinned image](../src/ubc_node/pilot/analysis_runner.py) before running:

```sh
docker pull docker.io/library/python@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34
task test-analysis-container
```

The gate runs the same worker command twice and checks the output and code
digest. It checks that injected CSV text fails and probes the actual worker
namespace for its network interfaces, non-root user, process and memory
limits, read-only filesystem, and absence of fixture credentials and host
authority. The worker receives the fixed program and typed window arguments,
reads CSV from stdin, and returns a bounded JSON response on stdout. The
runner rejects oversized output, times out stalled execution, and discards
stderr. The host validates the output and commits the release and manifest
only after rechecking task authority.

The local gate uses the reviewed method and the operator's container engine.
Running third-party analysis code requires a separate containment gate. The
checked browser manifest records the Linux ARM64 build. Use the browser
deployment procedure to generate a manifest for another architecture. Repeat
the analysis gate with the intended Linux deployment engine and run the
[C04 scanner deployment gate](trusted-core-and-isolation.md#c04-download-quarantine)
before the integrated pilot.
