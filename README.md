# Universal Benevolent Collective

The Universal Benevolent Collective (UBC) is a proposed, constitution-governed
network of human-supervised AI agents for collaborative research on
politically relevant, public-interest questions. Agents propose and choose
among admitted projects. They gather and verify evidence, build public
datasets, reproduce findings, and make forecasts that can be checked against
what happens.

Research is organised into living programmes. When evidence changes, a new
run can recover the previous work, rerun the affected analysis, and show
what changed. The shared knowledge base preserves reviewed theses and their
evidential history. Programme records link those theses to versioned data,
runnable analyses, and forecast outcomes.

## Current status

The repository implements a two-cycle single-node research fixture with
quarantined downloads, browser-assisted evidence collection, reproducible
analysis, thesis review, and forecast scoring. The fixture runs on macOS
with Docker Desktop and can exercise real Chromium and ClamAV on the
intended Linux deployment. Live-source acceptance is pending.

- [Single-node research pilot](docs/single-node-pilot.md)
  describes the implemented workflow, records, and deployment boundaries.
- [Pilot runbook](docs/pilot-runbook.md) covers setup, human review,
  verification, and recovery.
- [Product and architecture specification](docs/product-and-architecture-spec.md)
  defines the research portfolio, workflow, governance, and system design.
- [Shared knowledge base specification](docs/knowledge-base-spec.md)
  defines the thesis model, evidence, review, and publication.
- [Prototype reference](docs/prototype.md) and
  [trusted core and browser isolation](docs/trusted-core-and-isolation.md)
  describe the implemented runtime, current limits, and Linux verification.

The knowledge-base wire formats cover the
[immutable thesis revision](docs/knowledge-base-thesis.schema.json),
[mutable workflow state](docs/knowledge-base-thesis-workflow.schema.json), and
[composed read view](docs/knowledge-base-thesis-view.schema.json).

## Development

The node uses Python 3.13 and [uv](https://docs.astral.sh/uv/). Development
commands are defined with [Task](https://taskfile.dev/).

```shell
task bootstrap
uv run ubc doctor
uv run ubc demo --provider local
task check
```

The demo exercises the current prototype with local fixtures.
`task check` runs formatting and lint checks, strict type checking, the test
suite with branch coverage, and a package build. The recommended VS Code
extensions are listed in `.vscode/extensions.json`.

## Licensing and official identity

Unless stated otherwise, the software and other repository content are
licensed under the GNU Affero General Public License v3 or later. The
Constitution is separately licensed under CC BY-ND 4.0, which permits
redistribution of the unaltered document with attribution.
[Software licence](LICENSE) · [Constitution](CONSTITUTION.md) ·
[Constitution licence](CONSTITUTION-LICENSE.md)

Official UBC releases identify the Constitution version and digest to which
they are bound. Use of the project's name and official designations follows
the official-identity policy. Contributions and constitutional proposals
follow the contribution policy.
[Project notice](NOTICE) · [Official identity](TRADEMARKS.md) ·
[Contributions](CONTRIBUTING.md)
