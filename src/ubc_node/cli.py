"""Command-line entry point for the UBC node runtime."""

import argparse
import asyncio
import json
import os
import platform
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from time import sleep
from typing import NoReturn

from ubc_node import __version__
from ubc_node.artifacts.quarantine import (
    ClamAVScanner,
    DownloadQuarantine,
    DownloadScope,
    load_canaries,
)
from ubc_node.browser.isolation.isolated_browser import IsolatedBrowserError, PodmanBrowserRuntime
from ubc_node.core.contracts import RunBudget, RunState, TaskSpec
from ubc_node.core.event_log import JsonlEventLog
from ubc_node.demos.demo import DemoCancelled, DemoError, run_phase5_demo
from ubc_node.pilot.browser_research import (
    RESEARCH_INSTRUCTION,
    RESEARCH_SCHEMA,
    run_browser_research,
    validate_research_inventory,
)
from ubc_node.providers.agent_backend import StructuredAgentBackend
from ubc_node.providers.openai_backend import OpenAIModelProvider, OpenAIProviderConfig
from ubc_node.release.release_manifest import (
    ReleaseManifestError,
    create_release_manifest,
    verify_release_manifest,
    write_release_manifest,
)
from ubc_node.research.supervisor import ResearchProject


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ubc",
        description="Operate a Universal Benevolent Collective node.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("version", help="Show the installed UBC node version.")
    subcommands.add_parser("doctor", help="Check the local development runtime.")
    manifest = subcommands.add_parser(
        "release-manifest", help="Create verified trusted-core release metadata."
    )
    manifest.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    manifest.add_argument("--release-id", required=True)
    manifest.add_argument("--constitution-version", required=True)
    manifest.add_argument("--browser-image-reference", required=True)
    manifest.add_argument("--browser-image-digest", required=True)
    manifest.add_argument("--output", type=Path, required=True)
    quarantine = subcommands.add_parser("quarantine", help="Manage pilot download quarantine.")
    quarantine.add_argument("--store", type=Path, required=True)
    quarantine_commands = quarantine.add_subparsers(dest="quarantine_command", required=True)
    ingest = quarantine_commands.add_parser("import", help="Scan and release a completed CSV.")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--filename", required=True, help="Filename offered by the source.")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--task", required=True)
    ingest.add_argument("--session", required=True)
    ingest.add_argument("--source", required=True, help="Public source URL without a query.")
    ingest.add_argument("--expected-sha256", required=True, help="Digest of the complete file.")
    ingest.add_argument("--scanner-socket", type=Path, required=True, help="Private clamd socket.")
    ingest.add_argument("--canaries-file", type=Path, required=True, help="One literal per line.")
    quarantine_commands.add_parser("recover", help="Expire and clean interrupted imports.")
    research = subcommands.add_parser(
        "research", help="Collect public-page evidence in an isolated Linux browser."
    )
    research.add_argument("--root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    research.add_argument(
        "--manifest", type=Path, default=Path("release/trusted-core-manifest.json")
    )
    research.add_argument("--journal", type=Path, required=True, help="New research audit file.")
    research.add_argument("--task", required=True)
    research.add_argument("--objective", required=True)
    research.add_argument(
        "--source", action="append", nargs=2, metavar=("ID", "URL"), required=True
    )
    research.add_argument("--model", help="OpenAI model name. Reads OPENAI_API_KEY.")
    activity = subcommands.add_parser("research-activity", help="Inspect a project's run journal.")
    activity.add_argument("project", type=Path)
    activity.add_argument(
        "--follow", action="store_true", help="Print new activity until interrupted."
    )
    stop = subcommands.add_parser("research-stop", help="Revoke a project's research authority.")
    stop.add_argument("project", type=Path)
    demo = subcommands.add_parser("demo", help="Run or resume the Phase 5 fixture demonstration.")
    demo.add_argument(
        "--workspace",
        type=Path,
        default=Path(".ubc/phase5-demo"),
        help="Durable demo workspace (default: .ubc/phase5-demo).",
    )
    demo.add_argument(
        "--provider",
        choices=("local", "openai", "gemini"),
        default="local",
        help="Report model provider; API keys are read only from the environment.",
    )
    demo.add_argument("--model", help="Optional provider model name.")
    demo.add_argument(
        "--base-url",
        help="OpenAI-compatible base URL, including for a locally hosted model.",
    )
    demo.add_argument("--chrome-path", type=Path, help="Optional Chrome/Chromium executable.")
    demo.add_argument(
        "--browser-runtime",
        choices=("in-process", "supervised"),
        default="in-process",
        help="Browser isolation mode (default: in-process).",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Run the CLI with an optional explicit argument sequence."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "version":
        print(f"ubc-node {__version__}")
    elif arguments.command == "doctor":
        print(f"ubc-node {__version__}")
        print(f"Python {platform.python_version()}")
        print("Project scaffold: ready")
    elif arguments.command == "release-manifest":
        try:
            manifest = create_release_manifest(
                arguments.root,
                release_id=arguments.release_id,
                constitution_version=arguments.constitution_version,
                browser_image_reference=arguments.browser_image_reference,
                browser_image_digest=arguments.browser_image_digest,
            )
            verify_release_manifest(arguments.root, manifest)
            write_release_manifest(arguments.output, manifest)
        except (ReleaseManifestError, OSError) as error:
            print(f"release manifest failed: {error}", file=sys.stderr)
            return 1
        print(f"Release manifest: {arguments.output}")
    elif arguments.command == "quarantine":
        return _run_quarantine(arguments)
    elif arguments.command in {"research-activity", "research-stop"}:
        try:
            project = ResearchProject(arguments.project)
            if arguments.command == "research-stop":
                project.stop()
                print(f"Research project stopped: {arguments.project}")
            else:
                if not arguments.follow and not arguments.project.is_dir():
                    raise FileNotFoundError(
                        f"research project directory not found: {arguments.project}"
                    )
                _print_research_activity(project, follow=arguments.follow)
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError) as error:
            print(f"research project failed: {error}", file=sys.stderr)
            return 1
    elif arguments.command == "research":
        try:
            return asyncio.run(_run_research(arguments))
        except KeyboardInterrupt:
            print("research cancelled by operator", file=sys.stderr)
            return 130
        except TimeoutError:
            print("research failed: duration budget exhausted", file=sys.stderr)
            return 1
        except (IsolatedBrowserError, ValueError, OSError) as error:
            print(f"research failed: {error}", file=sys.stderr)
            return 1
    elif arguments.command == "demo":
        try:
            result = asyncio.run(
                run_phase5_demo(
                    workspace=arguments.workspace,
                    provider_name=arguments.provider,
                    model=arguments.model,
                    base_url=arguments.base_url,
                    chrome_path=arguments.chrome_path,
                    browser_runtime=arguments.browser_runtime,
                )
            )
        except DemoCancelled as error:
            print(str(error), file=sys.stderr)
            return 130
        except KeyboardInterrupt:
            print("demo cancelled by operator", file=sys.stderr)
            return 130
        except (DemoError, ValueError, OSError) as error:
            print(f"demo failed: {error}", file=sys.stderr)
            return 1
        print(f"Phase 5 demonstration complete: {result.report_path}")
        print(f"Captured evidence: {result.evidence_count}")
        print(f"Audit journal: {result.journal_path}")
    else:
        parser.print_help()

    return 0


def _print_research_activity(project: ResearchProject, *, follow: bool) -> None:
    sequences: dict[str, int] = {}
    while True:
        for event in project.activity():
            if event.sequence > sequences.get(event.run_id, 0):
                print(json.dumps(event.to_record(), ensure_ascii=True), flush=True)
                sequences[event.run_id] = event.sequence
        if not follow:
            return
        sleep(0.5)


async def _run_research(arguments: argparse.Namespace) -> int:
    sources = dict(arguments.source)
    if len(sources) != len(arguments.source):
        raise ValueError("duplicate research source identifier")
    validate_research_inventory(sources)
    task = TaskSpec(
        arguments.task,
        arguments.objective,
        RunBudget(
            max_steps=8,
            max_model_calls=8,
            max_browser_actions=7,
            max_handoffs=0,
            max_downloads=0,
            max_consecutive_failures=1,
            max_duration_seconds=120,
        ),
    )
    runtime = PodmanBrowserRuntime(arguments.root, arguments.manifest)
    config = OpenAIProviderConfig.from_env(model=arguments.model)
    journal: Path = arguments.journal
    journal.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    async with StructuredAgentBackend(
        OpenAIModelProvider(config),
        system_instruction=RESEARCH_INSTRUCTION,
        json_schema=RESEARCH_SCHEMA,
    ) as agent:
        result = await run_browser_research(
            task,
            sources,
            runtime=runtime,
            image_digest=runtime.image_digest,
            agent=agent,
            event_sink=JsonlEventLog(journal),
        )
    print(f"Research audit journal: {journal}")
    if result.state is not RunState.COMPLETED:
        print(f"research failed: {result.error}", file=sys.stderr)
        return 1
    return 0


def _run_quarantine(arguments: argparse.Namespace) -> int:
    store = DownloadQuarantine(arguments.store)
    try:
        if arguments.quarantine_command == "recover":
            store.recover()
            return 0
        scope = DownloadScope(
            arguments.project, arguments.task, arguments.session, arguments.source
        )
        release = store.ingest(
            scope,
            arguments.file,
            arguments.filename,
            expected_sha256=arguments.expected_sha256,
            scanner=ClamAVScanner(arguments.scanner_socket),
            canaries=load_canaries(arguments.canaries_file),
        )
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"quarantine failed: {error}", file=sys.stderr)
        return 1
    print(f"{release.receipt} sha256:{release.sha256} {release.size}")
    return 0


def main() -> NoReturn:
    """Run the console-script entry point."""
    raise SystemExit(run())
