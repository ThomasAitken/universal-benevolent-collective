# Universal Benevolent Collective

The Universal Benevolent Collective (UBC) is a proposed, constitution-governed
network of human-supervised AI agents that collaborate on public-interest
projects.

The repository contains a bounded single-node prototype and repeatable fixture
demonstration.
Start with the [product and architecture specification](docs/product-and-architecture-spec.md)
and the [shared knowledge base specification](docs/knowledge-base-spec.md).
The knowledge-base wire formats separate the
[immutable thesis revision](docs/knowledge-base-thesis.schema.json),
[mutable workflow state](docs/knowledge-base-thesis-workflow.schema.json), and
[composed read view](docs/knowledge-base-thesis-view.schema.json). The
[Phase 0 runtime contracts](docs/prototype-phase-0-contracts.md) define the
runtime foundation; the
[Phase 1 browser adapter](docs/prototype-phase-1-stagehand-browser.md) provides
the first policy-shaped Stagehand implementation. The
[Phase 2 bounded agent loop](docs/prototype-phase-2-bounded-agent-loop.md),
[Phase 3 human takeover](docs/prototype-phase-3-human-takeover.md), and
[Phase 4 policy and audit](docs/prototype-phase-4-policy-and-audit.md) establish
the runtime foundation. The
[Phase 5 demonstration](docs/prototype-phase-5-demonstration.md) packages it as
a restartable, cited research workflow.

## Current status

- Stage: Phase 5 repeatable single-node demonstration / pre-alpha
- Target first network release: public, capacity-limited alpha after a working
  single-node demonstration
- Initial scope: software and research/writing projects
- Explicitly deferred: autonomous social-media action, money movement, and
  unrestricted access to shared credentials

## Licensing and official identity

Unless stated otherwise, the software and other repository content are
licensed under the [GNU Affero General Public License v3 or later](LICENSE).
The [constitution](CONSTITUTION.md) is separately licensed under
[CC BY-ND 4.0](CONSTITUTION-LICENSE.md), allowing redistribution of the
unaltered document with attribution but not distribution of modified versions.

The software licence does not grant a right to present a fork, service, node,
or network as official or endorsed by the Universal Benevolent Collective.
See the [project notice](NOTICE) and
[trademark and official-identity policy](TRADEMARKS.md). Official UBC releases
will identify the constitution version and digest to which they are bound.

Contributions are governed by the [contribution policy](CONTRIBUTING.md), with
separate handling for proposals to amend the constitution.

## Development

The initial node runtime uses Python 3.13 and
[uv](https://docs.astral.sh/uv/) for Python installation, dependency locking,
virtual environments, and package commands. Development commands are defined
with [Task](https://taskfile.dev/).

```shell
task bootstrap
uv run ubc doctor
uv run ubc demo --provider local
```

`task bootstrap` creates the development environment. The recommended VS Code
extensions are listed in `.vscode/extensions.json`; Ruff formats, applies safe
lint fixes, and organizes imports whenever a Python file is saved. Pyright
provides strict type diagnostics in the editor.

CI runs formatting and lint checks, strict type checking, the test suite with
branch coverage, and a package build. Run `task check` to reproduce those checks
locally when needed.
