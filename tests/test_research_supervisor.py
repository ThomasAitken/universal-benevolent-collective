"""Exercise budgets and operator controls through the mediated research entry point."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from tests.test_public_web import OK, URL, NetworkFixture, Wire, redirect
from ubc_node.browser.isolation.isolated_browser import PodmanBrowserRuntime
from ubc_node.browser.isolation.snapshot import SnapshotContent, SnapshotLink
from ubc_node.cli import run as cli
from ubc_node.core.contracts import (
    ActionKind,
    ActionProposal,
    EventKind,
    ModelUsage,
    RunBudget,
    RunState,
    TaskSpec,
)
from ubc_node.research.page_reader import PageHTTPError, PageReader
from ubc_node.research.public_web import FixtureSearch, PublicWebFetcher, SearchHit, WebReadError
from ubc_node.research.supervisor import ResearchProject, ResearchRun, ResearchSupervisor

_HIT = SearchHit(URL, "Price data", "Public price observations")


def supervisor(project: ResearchProject, browser: Mock) -> ResearchSupervisor:
    return ResearchSupervisor(
        project,
        FixtureSearch({"prices": (_HIT,)}),
        lambda: PageReader(
            PublicWebFetcher(),
            cast(PodmanBrowserRuntime, Mock(launch_snapshot=AsyncMock(return_value=browser))),
        ),
    )


@pytest.fixture
def browser() -> Mock:
    return Mock(
        render_snapshot=AsyncMock(
            return_value=SnapshotContent(
                "Prices", "Evidence", (SnapshotLink("CSV", "/data"),), False, False
            )
        ),
        revoke=AsyncMock(),
    )


async def proposal(usage: ModelUsage | None = None) -> ActionProposal:
    return ActionProposal(ActionKind.COMPLETE, "Complete", output="Done", usage=usage)


@pytest.mark.parametrize(
    ("resource", "budget", "expected"),
    [
        ("search-query", RunBudget(max_search_queries=1), 1),
        ("page-read", RunBudget(max_page_reads=1), 1),
        ("web byte", RunBudget(max_web_bytes=4), 4),
        ("model-call", RunBudget(max_model_calls=1), 1),
        ("token", RunBudget(max_tokens=3), 3),
        ("cost", RunBudget(max_estimated_cost_usd=0.15), 0.1),
    ],
)
async def test_budget_checkpoint_and_resume(
    resource: str,
    budget: RunBudget,
    expected: float,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: Mock,
) -> None:
    net = NetworkFixture(monkeypatch)
    net.outcomes = [Wire(OK)]
    project = ResearchProject(tmp_path / "research")
    control = supervisor(project, browser)
    task = TaskSpec("task", "Study prices", budget)

    async def workflow(run: ResearchRun) -> str:
        run.save_checkpoint({"next": "compare prices"})
        if resource == "search-query":
            assert await run.search("prices") == (_HIT,)
            await run.search("prices")
        elif resource in {"page-read", "web byte"}:
            assert (await run.read_page(URL)).text == "Evidence"
            await run.read_page(URL)
        elif resource == "token":
            await run.propose(lambda: proposal(ModelUsage(2, 1)), max_cost_usd=0.1)
            await run.propose(lambda: proposal(ModelUsage(2, 1)), max_cost_usd=0.1)
        else:
            await run.propose(proposal, max_cost_usd=0.1)
            await run.propose(proposal, max_cost_usd=0.1)
        pytest.fail("budget must stop the workflow")

    paused = await control.run(task, workflow)
    assert paused.state is RunState.PAUSED and paused.error == f"{resource} budget exhausted"
    assert {
        "search-query": paused.usage.search_queries,
        "page-read": paused.usage.page_reads,
        "web byte": paused.usage.web_bytes,
        "model-call": paused.usage.model_calls,
        "token": paused.usage.total_tokens,
        "cost": paused.usage.estimated_cost_usd,
    }[resource] == expected
    assert len(net.connections) == int(resource in {"page-read", "web byte"})

    async def resume(run: ResearchRun) -> str:
        notes = [
            e.payload["data"]
            for e in run.previous_events
            if e.kind is EventKind.RESEARCH_CHECKPOINT
        ]
        assert notes == [{"next": "compare prices"}]
        assert await run.search("prices") == (_HIT,)
        return "Compared prices"

    resumed = await supervisor(ResearchProject(tmp_path / "research"), browser).run(
        replace(task, budget=RunBudget()), resume, resume_from=paused.run_id
    )
    assert resumed.state is RunState.COMPLETED and resumed.output == "Compared prices"
    events = project.activity()
    assert events[-2].payload["hits"] == [
        {"url": URL, "title": _HIT.title, "snippet": _HIT.snippet}
    ]
    assert project.journal(resumed.run_id).read_all()[0].payload["resume_from"] == paused.run_id
    with pytest.raises(ValueError, match="paused run for the same task"):
        await control.run(task, resume, resume_from=resumed.run_id)


async def test_deadline_interrupts_model_and_keeps_reserved_cost(
    tmp_path: Path, browser: Mock
) -> None:
    project = ResearchProject(tmp_path)
    control = supervisor(project, browser)
    cancelled = asyncio.Event()

    async def stalled() -> ActionProposal:
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        pytest.fail("model should have been cancelled")

    async def workflow(run: ResearchRun) -> str:
        run.save_checkpoint({"remaining": "request model summary"})
        await run.propose(stalled, max_cost_usd=0.25)
        pytest.fail("deadline must interrupt model call")

    task = TaskSpec("task", "Study prices", RunBudget(max_duration_seconds=0.1))
    result = await control.run(task, workflow)
    assert cancelled.is_set()
    assert result.state is RunState.PAUSED and result.error == "duration budget exhausted"
    assert result.usage.estimated_cost_usd == 0.25
    assert project.activity()[-2].payload["outcome"] == "cancelled"
    assert project.activity()[1].kind is EventKind.RESEARCH_CHECKPOINT


async def test_model_reservation_reconciles_and_checks_reported_cost(
    tmp_path: Path, browser: Mock
) -> None:
    project = ResearchProject(tmp_path)

    async def workflow(run: ResearchRun) -> str:
        for cost in (0.01, 0.06):
            await run.propose(
                lambda cost=cost: proposal(ModelUsage(2, 1, estimated_cost_usd=cost)),
                max_cost_usd=0.05,
            )
        pytest.fail("usage above a model reservation must stop the workflow")

    result = await supervisor(project, browser).run(
        TaskSpec("task", "Study prices", RunBudget(max_estimated_cost_usd=0.07)), workflow
    )
    assert result.state is RunState.PAUSED and result.error == "cost budget exhausted"
    assert result.usage.model_calls == 2 and result.usage.total_tokens == 6
    assert result.usage.estimated_cost_usd == 0.07
    finished_tool = project.activity()[-2]
    assert finished_tool.kind is EventKind.RESEARCH_TOOL_FINISHED
    assert finished_tool.payload["outcome"] == "ok"
    assert finished_tool.payload["proposal_kind"] == ActionKind.COMPLETE.value

    async def token_overrun(run: ResearchRun) -> str:
        await run.propose(lambda: proposal(ModelUsage(4, 2)), max_cost_usd=0.05)
        pytest.fail("token usage above budget must stop the workflow")

    token_result = await supervisor(project, browser).run(
        TaskSpec("task", "Study prices", RunBudget(max_tokens=5)), token_overrun
    )
    assert token_result.state is RunState.PAUSED and token_result.error == "token budget exhausted"
    assert token_result.usage.total_tokens == 6
    token_tool = project.journal(token_result.run_id).read_all()[-2]
    assert token_tool.payload["outcome"] == "ok"
    assert token_tool.payload["proposal_kind"] == ActionKind.COMPLETE.value


async def test_journal_retains_snapshot_and_failed_http_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Mock
) -> None:
    net = NetworkFixture(monkeypatch)
    net.answers *= 3
    net.outcomes = [
        redirect("/final"),
        Wire(OK),
        Wire(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\ncf-mitigated: challenge\r\n\r\n"),
    ]
    project = ResearchProject(tmp_path)

    async def workflow(run: ResearchRun) -> str:
        await run.read_page(URL)
        with pytest.raises(PageHTTPError, match="browser challenge"):
            await run.read_page("https://source.example/challenge")
        return "Collected evidence"

    result = await supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    assert result.state is RunState.COMPLETED
    assert result.usage.page_reads == 2 and result.usage.web_bytes == 4
    events = project.activity()
    assert events[1].payload["url"] == URL
    assert events[2].kind is EventKind.RESEARCH_REDIRECT
    assert events[2].payload["target_url"] == "https://source.example/final"
    snapshot = events[3].payload["snapshot"]
    assert isinstance(snapshot, dict) and snapshot["text"] == "Evidence"
    assert events[-2].payload["challenge_required"] is True
    retrieval = events[-2].payload["retrieval"]
    assert isinstance(retrieval, dict) and retrieval["status"] == 403
    browser.revoke.assert_awaited_once()


async def test_project_stop_from_another_process_revokes_all_active_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Mock
) -> None:
    net = NetworkFixture(monkeypatch)
    net.outcomes = [Wire(OK)]
    project = ResearchProject(tmp_path / "project")
    rendering = asyncio.Event()
    calling_model = asyncio.Event()

    async def render(*_args: object, **_kwargs: object) -> None:
        rendering.set()
        await asyncio.Future[None]()

    browser.render_snapshot.side_effect = render

    async def read(run: ResearchRun) -> str:
        await run.search("prices")
        await run.read_page(URL)
        pytest.fail("the page read should have been cancelled")

    async def model(run: ResearchRun) -> str:
        async def stalled() -> ActionProposal:
            calling_model.set()
            await asyncio.Future[None]()
            pytest.fail("the model should have been cancelled")

        await run.propose(stalled, max_cost_usd=0)
        pytest.fail("the workflow should have been cancelled")

    task = TaskSpec("task", "Study prices")
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        reading = group.create_task(supervisor(project, browser).run(task, read))
        modeling = group.create_task(
            supervisor(ResearchProject(tmp_path / "project"), browser).run(task, model)
        )
        await rendering.wait()
        await calling_model.wait()
        assert not reading.done() and not modeling.done()
        assert {
            str(e.payload["tool"])
            for e in project.activity()
            if e.kind is EventKind.RESEARCH_TOOL_STARTED
        } == {"page-read", "model-call", "search-query"}
        operator = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "ubc_node", "research-stop", str(tmp_path / "project")
        )
        assert await operator.wait() == 0
    assert reading.result().state is modeling.result().state is RunState.CANCELLED
    assert reading.result().usage.web_bytes == 4
    browser.revoke.assert_awaited_once()
    assert (await supervisor(project, browser).run(task, read)).state is RunState.CANCELLED
    assert len(net.connections) == 1


@pytest.mark.parametrize(
    ("exception", "expected"),
    [(TimeoutError(), "TimeoutError"), (RuntimeError("private details"), "RuntimeError")],
)
async def test_workflow_error_is_distinct_from_budget_exhaustion(
    tmp_path: Path, browser: Mock, exception: Exception, expected: str
) -> None:
    async def workflow(run: ResearchRun) -> str:
        with pytest.raises(RuntimeError, match="owning workflow"):
            await asyncio.create_task(run.search("prices"))
        with pytest.raises(ValueError, match="checkpoint exceeds"):
            run.save_checkpoint({"data": "x" * 65_536})
        with pytest.raises(ValueError, match="cost ceiling"):
            await run.propose(proposal, max_cost_usd=-1)
        raise exception

    result = await supervisor(ResearchProject(tmp_path), browser).run(
        TaskSpec("task", "Study prices"), workflow
    )
    assert result.state is RunState.FAILED and result.error == expected


async def test_external_cancellation_preserves_terminal_record(
    tmp_path: Path, browser: Mock
) -> None:
    project = ResearchProject(tmp_path)
    active = asyncio.Event()

    async def workflow(_run: ResearchRun) -> str:
        active.set()
        await asyncio.Future[None]()
        pytest.fail("workflow should have been cancelled")

    task = asyncio.create_task(
        supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    )
    await asyncio.wait_for(active.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    terminal = project.activity()[-1]
    assert terminal.kind is EventKind.RUN_FINISHED and terminal.state is RunState.CANCELLED


def test_operator_activity_follow_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "project"
    browser = Mock()
    project = ResearchProject(directory)

    async def workflow(run: ResearchRun) -> str:
        await run.search("prices")
        return "done"

    result = asyncio.run(
        supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    )
    command = ["research-activity", str(directory)]
    assert cli(["research-activity", str(tmp_path / "missing")]) == 1
    assert "not found" in capsys.readouterr().err
    assert cli(command) == 0
    assert [json.loads(line)["run_id"] for line in capsys.readouterr().out.splitlines()] == [
        result.run_id
    ] * result.event_count

    # A writer's incomplete final record is invisible to observers.
    with project.journal(result.run_id).path.open("ab") as stream:
        stream.write(b'{"incomplete')
    pause = Mock(side_effect=[None, KeyboardInterrupt()])
    monkeypatch.setattr("ubc_node.cli.sleep", pause)
    assert cli([*command, "--follow"]) == 130
    assert len(capsys.readouterr().out.splitlines()) == result.event_count

    assert cli(["research-stop", str(directory)]) == 0
    assert project.stopped
    with project.journal(result.run_id).path.open("ab") as stream:
        stream.write(b"\n")
    assert cli(command) == 1
    assert "invalid event record" in capsys.readouterr().err


async def test_stream_budget_failure_remains_paused_if_workflow_catches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Mock
) -> None:
    net = NetworkFixture(monkeypatch)
    net.outcomes = [Wire(OK)]
    project = ResearchProject(tmp_path)

    async def workflow(run: ResearchRun) -> str:
        with pytest.raises(RuntimeError, match="web byte budget"):
            await run.read_page(URL)
        return "caught budget exception"

    task = TaskSpec("task", "Study prices", RunBudget(max_web_bytes=3))
    result = await supervisor(project, browser).run(task, workflow)
    assert result.state is RunState.PAUSED and result.usage.page_reads == 1
    assert project.activity()[-2].payload["error"] == "WebByteBudgetExhausted"
    browser.render_snapshot.assert_not_awaited()


async def test_project_stop_keeps_checkpoint_but_prevents_resume(
    tmp_path: Path, browser: Mock
) -> None:
    project = ResearchProject(tmp_path)
    task = TaskSpec("task", "Study prices", RunBudget(max_search_queries=0))

    async def workflow(run: ResearchRun) -> str:
        run.save_checkpoint({"next": "search"})
        await run.search("prices")
        pytest.fail("budget must pause")

    control = supervisor(project, browser)
    paused = await control.run(task, workflow)
    assert paused.state is RunState.PAUSED
    project.stop()
    result = await control.run(task, workflow, resume_from=paused.run_id)
    assert result.state is RunState.CANCELLED
    assert [
        e.payload["data"] for e in project.activity() if e.kind is EventKind.RESEARCH_CHECKPOINT
    ] == [{"next": "search"}]


async def test_elapsed_deadline_prevents_tool_before_timer_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Mock
) -> None:
    elapsed = 0.0
    monkeypatch.setattr("ubc_node.research.supervisor.monotonic", lambda: elapsed)

    async def workflow(run: ResearchRun) -> str:
        nonlocal elapsed
        elapsed = 300.0
        await run.search("prices")
        pytest.fail("deadline passed")

    project = ResearchProject(tmp_path)
    result = await supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    assert result.state is RunState.PAUSED and result.error == "duration budget exhausted"
    assert result.usage.search_queries == 0


async def test_project_stop_during_cancellation_allows_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Mock
) -> None:
    cleanup = asyncio.Event()
    watcher_started = asyncio.Event()
    watcher_poll = asyncio.Event()
    original_sleep = asyncio.sleep

    async def poll(delay: float) -> None:
        watcher_started.set()
        await original_sleep(delay)
        watcher_poll.set()

    monkeypatch.setattr("ubc_node.research.supervisor.asyncio.sleep", poll)
    project = ResearchProject(tmp_path)

    async def workflow(_run: ResearchRun) -> str:
        await watcher_started.wait()
        owner = asyncio.current_task()
        assert owner is not None
        owner.cancel()
        try:
            await asyncio.Future[None]()
        finally:
            project.stop()
            await watcher_poll.wait()
            cleanup.set()
        pytest.fail("run was cancelled")

    async with asyncio.timeout(2):
        result = await supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    assert result.state is RunState.CANCELLED and cleanup.is_set()


async def test_deadline_and_external_cancel_wait_for_async_tool_cleanup(
    tmp_path: Path, browser: Mock
) -> None:
    project = ResearchProject(tmp_path)
    in_tool = asyncio.Event()

    async def slow_cleanup() -> ActionProposal:
        in_tool.set()
        try:
            await asyncio.Future[None]()
        finally:
            await asyncio.sleep(0.02)
        pytest.fail("model should have been cancelled")

    async def workflow(run: ResearchRun) -> str:
        await run.propose(slow_cleanup, max_cost_usd=0.1)
        pytest.fail("workflow should have been cancelled")

    task = TaskSpec("task", "Study prices", RunBudget(max_duration_seconds=0.05))
    paused = await supervisor(project, browser).run(task, workflow)
    assert paused.state is RunState.PAUSED and paused.error == "duration budget exhausted"
    paused_events = project.journal(paused.run_id).read_all()
    assert paused_events[-2].kind is EventKind.RESEARCH_TOOL_FINISHED
    assert paused_events[-2].payload["outcome"] == "cancelled"
    assert paused_events[-1].kind is EventKind.RUN_FINISHED
    assert paused.event_count == len(paused_events)

    in_tool.clear()
    external = asyncio.create_task(
        supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    )
    await asyncio.wait_for(in_tool.wait(), 1)
    external.cancel()
    with pytest.raises(asyncio.CancelledError):
        await external
    all_events = project.activity()
    assert all_events[-2].kind is EventKind.RESEARCH_TOOL_FINISHED
    assert all_events[-2].payload["outcome"] == "cancelled"
    assert all_events[-1].kind is EventKind.RUN_FINISHED


async def test_invalid_page_url_is_audited_and_counted(tmp_path: Path, browser: Mock) -> None:
    project = ResearchProject(tmp_path)

    async def workflow(run: ResearchRun) -> str:
        with pytest.raises(WebReadError, match="public HTTPS"):
            await run.read_page("http://127.0.0.1/private")
        return "handled"

    result = await supervisor(project, browser).run(TaskSpec("task", "Study prices"), workflow)
    assert result.state is RunState.COMPLETED and result.usage.page_reads == 1
    events = project.activity()
    assert events[1].kind is EventKind.RESEARCH_TOOL_STARTED
    assert events[2].kind is EventKind.RESEARCH_TOOL_FINISHED
    assert events[2].payload["outcome"] == "error"
    assert events[2].payload["error"] == "WebReadError"
