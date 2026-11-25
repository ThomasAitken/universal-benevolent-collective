"""Command-line entry point for the UBC node runtime."""

import argparse
import asyncio
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from ubc_node import __version__
from ubc_node.demo import DemoCancelled, DemoError, run_phase5_demo


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ubc",
        description="Operate a Universal Benevolent Collective node.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("version", help="Show the installed UBC node version.")
    subcommands.add_parser("doctor", help="Check the local development runtime.")
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
    elif arguments.command == "demo":
        try:
            result = asyncio.run(
                run_phase5_demo(
                    workspace=arguments.workspace,
                    provider_name=arguments.provider,
                    model=arguments.model,
                    base_url=arguments.base_url,
                    chrome_path=arguments.chrome_path,
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


def main() -> NoReturn:
    """Run the console-script entry point."""
    raise SystemExit(run())
