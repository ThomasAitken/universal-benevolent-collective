"""Operator-owned runner for the one reviewed pilot transform."""

import hashlib
import os
import select
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryFile
from time import monotonic
from uuid import uuid4

from ubc_node.pilot import price_method

# The Python 3.13.9 slim-bookworm multi-platform image also used by C05.
IMAGE = "docker.io/library/python@sha256:b685a4fa58bb19d1814d78a1ec0f0208f351452724f78b20212c984d6e124a34"
TIMEOUT = 20
OUTPUT_LIMIT = 128 * 1024
REGIONS = {"NSW1", "QLD1", "SA1", "TAS1", "VIC1"}


@dataclass(frozen=True, slots=True)
class PriceWindow:
    region: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        if (
            self.region not in REGIONS
            or self.start.tzinfo is None
            or self.end.tzinfo is None
            or (self.start - epoch) % price_method.INTERVAL
            or (self.end - epoch) % price_method.INTERVAL
            or not timedelta(0) < self.end - self.start <= timedelta(days=7)
        ):
            raise ValueError("expected a known region and an aligned window of up to seven days")

    def arguments(self) -> tuple[str, str, str]:
        return (self.region, _iso(self.start), _iso(self.end))


class AnalysisError(ValueError):
    """A bounded failure code for the operator journal."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AnalysisRunner:
    """The trusted host supplies an engine and a fixed, locally provisioned image."""

    engine: str = "docker"

    def run(self, content: bytes, window: PriceWindow) -> tuple[bytes, str, str]:
        """Return bounded stdout and the exact code and image identities."""
        program = Path(price_method.__file__).read_bytes()
        name = f"ubc-analysis-{uuid4().hex}"
        command = [
            self.engine,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=16",
            "--memory=96m",
            "--memory-swap=96m",
            "--cpus=1",
            "--ulimit=cpu=5:5",
            "--ulimit=fsize=0:0",
            "--ulimit=core=0:0",
            "--log-driver=none",
            "--interactive",
            "--entrypoint=/usr/local/bin/python",
            IMAGE,
            "-ISB",
            "-c",
            program.decode("utf-8"),
            *window.arguments(),
        ]
        try:
            with TemporaryFile() as input_file:
                input_file.write(content)
                input_file.seek(0)
                with subprocess.Popen(
                    command, stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                ) as process:
                    try:
                        output = _read_output(process)
                    except (AnalysisError, OSError, KeyboardInterrupt):
                        process.kill()
                        subprocess.run(
                            [self.engine, "rm", "--force", name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            check=False,
                        )
                        raise
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AnalysisError("analysis_engine_failed") from error
        return output, hashlib.sha256(program).hexdigest(), IMAGE


def _read_output(process: subprocess.Popen[bytes]) -> bytes:
    assert process.stdout is not None
    deadline = monotonic() + TIMEOUT
    output = bytearray()
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
            raise AnalysisError("analysis_timeout")
        block = os.read(process.stdout.fileno(), OUTPUT_LIMIT + 1 - len(output))
        if not block:
            break
        output.extend(block)
        if len(output) > OUTPUT_LIMIT:
            raise AnalysisError("analysis_output_limit")
    try:
        returncode = process.wait(timeout=max(0, deadline - monotonic()))
    except subprocess.TimeoutExpired as error:
        raise AnalysisError("analysis_timeout") from error
    if returncode != 0:
        raise AnalysisError("analysis_execution_failed")
    return bytes(output)
