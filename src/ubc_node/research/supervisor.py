"""Project-scoped authority and durable audit for mediated research tools."""

import asyncio
import json
import math
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from heapq import merge
from pathlib import Path
from time import monotonic
from typing import Literal, Self, cast
from uuid import UUID, uuid4

from ubc_node.core.contracts import (
    ActionProposal,
    BudgetUsage,
    EventKind,
    JsonValue,
    RunEvent,
    RunResult,
    RunState,
    TaskSpec,
)
from ubc_node.core.event_log import JsonlEventLog
from ubc_node.research.page_reader import PageHTTPError, PageReader, PageRetrieval, PageSnapshot
from ubc_node.research.public_web import (
    Redirect,
    SearchHit,
    SearchProvider,
    WebByteBudget,
    WebByteBudgetExhausted,
)

_STOP_POLL_SECONDS = 0.1
_MAX_CHECKPOINT_BYTES = 64 * 1024


class _BudgetExhausted(RuntimeError):
    pass


class _ProjectStopped(RuntimeError):
    pass


class ResearchProject:
    """Operator-owned directory shared by the project's local run processes."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory.resolve()
        self._stop_marker = self._directory / "stopped"

    @property
    def stopped(self) -> bool:
        """Check the durable revocation marker, including other processes' writes."""
        return self._stop_marker.exists()

    def stop(self) -> None:
        """Revoke all active and future runs under this project directory."""
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._stop_marker.touch(mode=0o600, exist_ok=True)
        directory = os.open(self._directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def activity(self) -> tuple[RunEvent, ...]:
        """Read committed activity from every run, including retained snapshots."""
        journals = (
            JsonlEventLog(journal).read_all(allow_incomplete_tail=True)
            for journal in self._directory.glob("*.jsonl")
        )
        return tuple(
            merge(*journals, key=lambda event: (event.occurred_at, event.run_id, event.sequence))
        )

    def journal(self, run_id: str) -> JsonlEventLog:
        """Locate one run's journal within this project."""
        return JsonlEventLog(self._directory / f"{UUID(run_id).hex}.jsonl")

    def new_run(self) -> tuple[str, JsonlEventLog]:
        """Reserve a new private journal, with one writer per run."""
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        run_id = uuid4().hex
        journal = self.journal(run_id)
        journal.path.touch(mode=0o600, exist_ok=False)
        return run_id, journal


class ResearchRun:
    """Only the owning workflow can invoke these tools."""

    def __init__(
        self,
        project: ResearchProject,
        search: SearchProvider,
        page_reader: PageReader,
        task: TaskSpec,
        previous: tuple[RunEvent, ...],
    ) -> None:
        self.previous_events = previous
        self._task = task
        self._project = project
        self._search = search
        self._page_reader = page_reader
        self._run_id, self._journal = self._project.new_run()
        self._owner: asyncio.Task[str] | None = None
        self._sequence = 0
        self._state = RunState.RUNNING
        self._started = monotonic()
        self._web = WebByteBudget(task.budget.max_web_bytes)
        self._exhausted: str | None = None
        self._usage = BudgetUsage()
        self._spend = Decimal(0)

    async def _execute(
        self, workflow: Callable[[Self], Awaitable[str]], resume_from: str | None
    ) -> RunResult:
        budget = self._task.budget
        self._emit(
            EventKind.RUN_CREATED,
            {
                "objective": self._task.objective,
                "limits": {
                    "search_queries": budget.max_search_queries,
                    "page_reads": budget.max_page_reads,
                    "web_bytes": budget.max_web_bytes,
                    "model_calls": budget.max_model_calls,
                    "tokens": budget.max_tokens,
                    "estimated_cost_usd": budget.max_estimated_cost_usd,
                    "duration_seconds": budget.max_duration_seconds,
                },
                "resume_from": resume_from,
            },
        )
        deadline = asyncio.timeout(budget.max_duration_seconds)
        try:
            async with deadline:
                self._check_authority()
                self._owner = asyncio.create_task(self._work(workflow))
                watcher = asyncio.create_task(self._watch_stop(self._owner))
                try:
                    output = await self._owner
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, self._owner, return_exceptions=True)
            self._check_authority()
            return self._finish(RunState.COMPLETED, output=output)
        except TimeoutError as error:
            if not deadline.expired():
                return self._finish(RunState.FAILED, error=type(error).__name__)
            return self._finish(RunState.PAUSED, error="duration budget exhausted")
        except _BudgetExhausted as error:
            return self._finish(RunState.PAUSED, error=str(error))
        except _ProjectStopped as error:
            return self._finish(RunState.CANCELLED, error=str(error))
        except asyncio.CancelledError:
            if self._project.stopped:
                return self._finish(RunState.CANCELLED, error="project stopped")
            self._finish(RunState.CANCELLED, error="run cancelled")
            raise
        except Exception as error:
            return self._finish(RunState.FAILED, error=type(error).__name__)
        finally:
            await self._page_reader.stop()

    async def _watch_stop(self, workflow: asyncio.Task[str]) -> None:
        while not self._project.stopped:  # noqa: ASYNC110 - marker is shared across processes
            await asyncio.sleep(_STOP_POLL_SECONDS)
        if not workflow.cancelling():
            workflow.cancel()

    async def _work(self, workflow: Callable[[Self], Awaitable[str]]) -> str:
        self._check_authority()
        output = await workflow(self)
        self._check_authority()
        return output

    def _check_authority(self) -> None:
        if self._project.stopped:
            raise _ProjectStopped("project stopped")
        if monotonic() - self._started >= self._task.budget.max_duration_seconds:
            self._pause("duration")
        if self._exhausted is not None:
            raise _BudgetExhausted(self._exhausted)

    def _check_owner(self) -> None:
        if asyncio.current_task() is not self._owner:
            raise RuntimeError("research tools require the owning workflow")
        self._check_authority()

    def save_checkpoint(self, data: Mapping[str, JsonValue]) -> None:
        """Commit workflow progress that a fresh attempt can restore."""
        self._check_owner()
        record = dict(data)
        if len(json.dumps(record, allow_nan=False).encode("utf-8")) > _MAX_CHECKPOINT_BYTES:
            raise ValueError("research checkpoint exceeds the byte limit")
        self._emit(EventKind.RESEARCH_CHECKPOINT, {"data": record})

    def _pause(self, resource: str) -> None:
        self._exhausted = f"{resource} budget exhausted"
        raise _BudgetExhausted(self._exhausted)

    def _emit(self, kind: EventKind, payload: Mapping[str, JsonValue]) -> None:
        self._sequence += 1
        self._usage = replace(
            self._usage,
            web_bytes=self._web.used,
            duration_seconds=monotonic() - self._started,
            estimated_cost_usd=float(self._spend),
        )
        self._journal.append(
            RunEvent(
                self._sequence,
                self._run_id,
                self._task.task_id,
                kind,
                self._state,
                datetime.now(UTC),
                {**payload, "usage": cast(dict[str, JsonValue], asdict(self._usage))},
            )
        )

    def _finish(
        self, state: RunState, *, output: str | None = None, error: str | None = None
    ) -> RunResult:
        self._state = state
        self._emit(EventKind.RUN_FINISHED, {"output": output, "error": error})
        return RunResult(
            self._run_id,
            self._task.task_id,
            state,
            self._usage,
            self._sequence,
            output=output,
            error=error,
        )

    async def _tool[T](
        self,
        name: str,
        counter: Literal["search_queries", "page_reads", "model_calls"],
        limit: int,
        details: Mapping[str, JsonValue],
        invoke: Callable[[], Awaitable[T]],
        record: Callable[[T], Mapping[str, JsonValue]],
        *,
        cost: Decimal = Decimal(0),
    ) -> T:
        self._check_owner()
        used = getattr(self._usage, counter)
        if used >= limit:
            self._pause(name)
        if counter != "model_calls" and not self._web.remaining:
            self._pause("web byte")
        if counter == "model_calls" and self._usage.total_tokens >= self._task.budget.max_tokens:
            self._pause("token")
        reserved_cost = self._spend + cost
        if reserved_cost > Decimal(str(self._task.budget.max_estimated_cost_usd)):
            self._pause("cost")
        self._usage = replace(self._usage, **{counter: used + 1})
        self._spend = reserved_cost
        self._emit(EventKind.RESEARCH_TOOL_STARTED, {"tool": name, **details})
        try:
            self._check_authority()
            result = await invoke()
            data = record(result)
        except asyncio.CancelledError:
            self._emit(EventKind.RESEARCH_TOOL_FINISHED, {"tool": name, "outcome": "cancelled"})
            raise
        except Exception as error:
            # Provider exceptions can include secrets. Web HTTP metadata is structured.
            failure: dict[str, JsonValue] = {"error": type(error).__name__}
            if isinstance(error, PageHTTPError):
                failure["retrieval"] = _retrieval_record(error.retrieval)
                failure["challenge_required"] = error.challenge_required
            self._emit(
                EventKind.RESEARCH_TOOL_FINISHED, {"tool": name, "outcome": "error", **failure}
            )
            if isinstance(error, WebByteBudgetExhausted):
                self._pause("web byte")
            raise
        self._emit(EventKind.RESEARCH_TOOL_FINISHED, {"tool": name, "outcome": "ok", **data})
        self._check_authority()
        return result

    async def search(self, query: str, *, count: int = 10) -> tuple[SearchHit, ...]:
        """Search the configured provider and retain its bounded hits as evidence."""
        return await self._tool(
            "search-query",
            "search_queries",
            self._task.budget.max_search_queries,
            {"query": query[:600], "count": count},
            lambda: self._search.search(query, count=count, byte_budget=self._web),
            lambda hits: {"hits": [asdict(hit) for hit in hits], "untrusted": True},
        )

    async def read_page(self, url: str, *, link_query: str = "") -> PageSnapshot:
        """Read one page through the mediator and retain the sanitized snapshot."""

        def redirect(hop: Redirect) -> None:
            self._check_authority()
            self._emit(EventKind.RESEARCH_REDIRECT, asdict(hop))

        return await self._tool(
            "page-read",
            "page_reads",
            self._task.budget.max_page_reads,
            {"url": url[:600], "link_query": link_query[:600]},
            lambda: self._page_reader.read(
                url, link_query=link_query, byte_budget=self._web, on_redirect=redirect
            ),
            lambda page: {
                "snapshot": {
                    **asdict(page),
                    "retrieval": _retrieval_record(page.retrieval),
                    "links": [asdict(link) for link in page.links],
                },
            },
        )

    async def propose(
        self, invoke: Callable[[], Awaitable[ActionProposal]], *, max_cost_usd: float
    ) -> ActionProposal:
        """Run one model call with a cost ceiling derived by the trusted adapter."""
        if not math.isfinite(max_cost_usd) or max_cost_usd < 0:
            raise ValueError("model cost ceiling must be finite and non-negative")
        old_cost = self._spend
        ceiling = Decimal(str(max_cost_usd))

        def record(proposal: ActionProposal) -> Mapping[str, JsonValue]:
            usage = proposal.usage
            if usage is not None:
                self._usage = replace(
                    self._usage,
                    input_tokens=self._usage.input_tokens + usage.input_tokens,
                    output_tokens=self._usage.output_tokens + usage.output_tokens,
                    total_tokens=self._usage.total_tokens + usage.total_tokens,
                )
                actual_cost = Decimal(str(usage.estimated_cost_usd))
                self._spend = old_cost + actual_cost
                if actual_cost > ceiling:
                    self._exhausted = "cost budget exhausted"
                elif self._usage.total_tokens > self._task.budget.max_tokens:
                    self._exhausted = "token budget exhausted"
            return {"proposal_kind": proposal.kind.value}

        return await self._tool(
            "model-call",
            "model_calls",
            self._task.budget.max_model_calls,
            {"reserved_cost_usd": max_cost_usd},
            invoke,
            record,
            cost=ceiling,
        )


class ResearchSupervisor:
    """Run trusted workflows with per-run budgets and project-level revocation."""

    def __init__(
        self,
        project: ResearchProject,
        search: SearchProvider,
        page_reader_factory: Callable[[], PageReader],
    ) -> None:
        self._project = project
        self._search = search
        self._page_reader_factory = page_reader_factory

    async def run(
        self,
        task: TaskSpec,
        workflow: Callable[[ResearchRun], Awaitable[str]],
        *,
        resume_from: str | None = None,
    ) -> RunResult:
        """Run a workflow, optionally giving it the journal of a paused attempt."""
        previous: tuple[RunEvent, ...] = ()
        if resume_from is not None:
            previous = self._project.journal(resume_from).read_all()
            if (
                not previous
                or previous[-1].kind is not EventKind.RUN_FINISHED
                or previous[-1].state is not RunState.PAUSED
                or previous[-1].task_id != task.task_id
            ):
                raise ValueError("resume requires a paused run for the same task")

        run = ResearchRun(self._project, self._search, self._page_reader_factory(), task, previous)
        return await run._execute(workflow, resume_from)  # pyright: ignore[reportPrivateUsage]


def _retrieval_record(retrieval: PageRetrieval) -> dict[str, JsonValue]:
    record = asdict(retrieval)
    record["retrieved_at"] = retrieval.retrieved_at.isoformat()
    record["redirects"] = [asdict(hop) for hop in retrieval.redirects]
    return cast(dict[str, JsonValue], record)
