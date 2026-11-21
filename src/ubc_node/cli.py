"""Command-line entry point for the UBC node runtime."""

import argparse
import platform
from collections.abc import Sequence
from typing import NoReturn

from ubc_node import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ubc",
        description="Operate a Universal Benevolent Collective node.",
    )
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("version", help="Show the installed UBC node version.")
    subcommands.add_parser("doctor", help="Check the local development runtime.")
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
    else:
        parser.print_help()

    return 0


def main() -> NoReturn:
    """Run the console-script entry point."""
    raise SystemExit(run())
