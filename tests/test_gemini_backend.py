"""Unit tests for the Gemini StructuredModelProvider wire adapter."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest

from tests.browser_fakes import FakeBrowser, FakeContext, FakePage, FakeStagehand
from tests.fakes import MemoryEventSink, ScriptedPolicyEvaluator
from ubc_node.browser.browser_contracts import BrowserSessionConfig
from ubc_node.browser.stagehand_browser import (
    StagehandBrowserAdapter,
    _BrowserLike,  # pyright: ignore[reportPrivateUsage]
    _StagehandLike,  # pyright: ignore[reportPrivateUsage]
)
from ubc_node.core.contracts import (
    AgentBackendError,
    EventKind,
    JsonValue,
    RunState,
    TaskSpec,
)
from ubc_node.core.supervisor import RunSupervisor
from ubc_node.providers.agent_backend import (
    PROPOSAL_JSON_SCHEMA,
    PROPOSAL_SYSTEM_INSTRUCTION,
    ModelPricing,
    ProposalRequest,
)
from ubc_node.providers.gemini_backend import (
    GeminiModelProvider,
    GeminiProviderConfig,
    create_gemini_agent_backend,
)

NOW = datetime(2026, 11, 21, 5, 0, tzinfo=UTC)
TASK = TaskSpec("task-1", "Research the fixture site", metadata={"origin": "https://fixture.test"})
REQUEST = ProposalRequest(
    system_instruction=PROPOSAL_SYSTEM_INSTRUCTION,
    user_prompt='{"task":{"objective":"test"}}',
    json_schema=PROPOSAL_JSON_SCHEMA,
)


def _gemini_response(
    proposal: Mapping[str, JsonValue],
    *,
    usage: Mapping[str, object] | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(proposal)}],
                },
                "finishReason": "STOP",
            }
        ],
    }
    if usage is not None:
        body["usageMetadata"] = dict(usage)
    return httpx.Response(200, json=body)


def test_gemini_config_validation_redaction_pricing_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GeminiProviderConfig(api_key="super-secret-key", base_url="https://api.test/v1beta/")
    assert "super-secret-key" not in repr(config)
    assert config.model == "gemini-3.8-flash"
    assert config.base_url == "https://api.test/v1beta"
    assert config.resolved_pricing == ModelPricing(0.75, 3.75)

    custom_pricing = ModelPricing(1.0, 2.0)
    custom = GeminiProviderConfig(api_key="k", model="unknown-model", pricing=custom_pricing)
    assert custom.resolved_pricing is custom_pricing
    unknown = GeminiProviderConfig(api_key="k", model="unknown-model")
    assert unknown.resolved_pricing == ModelPricing(0.0, 0.0)

    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setenv("UBC_GEMINI_MODEL", "gemini-2.5-pro")
    from_env = GeminiProviderConfig.from_env()
    assert from_env.api_key == "env-key"
    assert from_env.model == "gemini-2.5-pro"

    with pytest.raises(ValueError, match="api_key"):
        GeminiProviderConfig(api_key=" ")
    with pytest.raises(ValueError, match="model"):
        GeminiProviderConfig(api_key="k", model=" ")
    with pytest.raises(ValueError, match="base_url"):
        GeminiProviderConfig(api_key="k", base_url="ftp://example.test")
    with pytest.raises(ValueError, match="timeout_seconds"):
        GeminiProviderConfig(api_key="k", timeout_seconds=0)
    with pytest.raises(ValueError, match="temperature"):
        GeminiProviderConfig(api_key="k", temperature=2.5)
    with pytest.raises(ValueError, match="max_output_tokens"):
        GeminiProviderConfig(api_key="k", max_output_tokens=0)


@pytest.mark.asyncio
async def test_gemini_provider_sends_json_schema_headers_and_counts_thought_tokens() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _gemini_response(
            {"kind": "complete", "description": "Done", "output": "ok"},
            usage={
                "promptTokenCount": 200,
                "candidatesTokenCount": 40,
                "thoughtsTokenCount": 60,
                "totalTokenCount": 300,
            },
        )

    config = GeminiProviderConfig(api_key="test-key")
    async with GeminiModelProvider(config, transport=httpx.MockTransport(handler)) as provider:
        response = await provider.generate_proposal(REQUEST)

    assert json.loads(response.text) == {
        "kind": "complete",
        "description": "Done",
        "output": "ok",
    }
    assert response.input_tokens == 200
    assert response.output_tokens == 100
    assert response.total_tokens == 300

    request = captured_requests[0]
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)

    payload = json.loads(request.content.decode("utf-8"))
    assert payload["generationConfig"]["responseJsonSchema"] == PROPOSAL_JSON_SCHEMA
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


@pytest.mark.asyncio
async def test_gemini_provider_handles_non_nullable_type_list_shared_client_and_bad_usage() -> None:
    responses = [
        _gemini_response(
            {"kind": "complete", "description": "Done", "output": "ok"},
            usage={"promptTokenCount": True, "candidatesTokenCount": -5, "totalTokenCount": "bad"},
        ),
        _gemini_response({"kind": "complete", "description": "Done", "output": "ok"}),
    ]
    config = GeminiProviderConfig(api_key="test-key", thinking_level=None)
    minimal_request = ProposalRequest(
        system_instruction="sys",
        user_prompt="user",
        json_schema={"type": ["object"]},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: responses.pop(0))
    ) as shared_client:
        provider = GeminiModelProvider(config, client=shared_client)
        first = await provider.generate_proposal(minimal_request)
        second = await provider.generate_proposal(minimal_request)
        await provider.aclose()
        assert not shared_client.is_closed

    assert (first.input_tokens, first.output_tokens, first.total_tokens) == (0, 0, 0)
    assert (second.input_tokens, second.output_tokens, second.total_tokens) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_match"),
    [
        (
            httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}}),
            "HTTP 429: Rate limit exceeded",
        ),
        (
            httpx.Response(500, json={"error": {"message": " "}}),
            "HTTP 500: Internal Server Error",
        ),
        (
            httpx.Response(502, text="bad gateway"),
            "HTTP 502: Bad Gateway",
        ),
        (
            httpx.Response(503, json=["unexpected"]),
            "HTTP 503: Service Unavailable",
        ),
        (
            httpx.Response(504, json={"error": "gateway timeout"}),
            "HTTP 504: Gateway Timeout",
        ),
        (
            httpx.Response(200, text="not-json"),
            "invalid JSON",
        ),
        (
            httpx.Response(200, json=["not-an-object"]),
            "must be a JSON object",
        ),
        (
            httpx.Response(200, json={"candidates": []}),
            "no candidates",
        ),
        (
            httpx.Response(200, json={"candidates": ["bad"]}),
            "must be a JSON object",
        ),
        (
            httpx.Response(200, json={"candidates": [{}]}),
            "no content",
        ),
        (
            httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]}),
            "no content parts",
        ),
        (
            httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": ["non-dict", {"text": " "}]}}]},
            ),
            "no text part",
        ),
    ],
)
async def test_gemini_provider_rejects_malformed_or_failed_responses(
    response: httpx.Response,
    expected_match: str,
) -> None:
    config = GeminiProviderConfig(api_key="test-key")
    async with GeminiModelProvider(
        config,
        transport=httpx.MockTransport(lambda _request: response),
    ) as provider:
        with pytest.raises(AgentBackendError, match=expected_match):
            await provider.generate_proposal(REQUEST)


@pytest.mark.asyncio
async def test_gemini_provider_wraps_http_transport_errors_without_leaking_key() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = GeminiProviderConfig(api_key="secret-api-key")
    async with GeminiModelProvider(
        config,
        transport=httpx.MockTransport(failing_handler),
    ) as provider:
        with pytest.raises(AgentBackendError, match="ConnectError") as exc_info:
            await provider.generate_proposal(REQUEST)
    assert "secret-api-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_supervisor_gemini_factory_and_stagehand_adapter_run_bounded_loop(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    page = FakePage("page-1", "about:blank", title="Blank", content="Welcome to fixture")
    adapter = StagehandBrowserAdapter(
        config=BrowserSessionConfig(
            workspace=workspace,
            allowed_origins=frozenset({"https://fixture.test"}),
        ),
        browser=cast(_BrowserLike, FakeBrowser(FakeContext([page]))),
        stagehand=cast(_StagehandLike, FakeStagehand()),
    )
    responses = [
        _gemini_response(
            {
                "kind": "browser",
                "description": "Open the fixture homepage",
                "operation": "open",
                "url": "https://fixture.test/",
            },
            usage={"promptTokenCount": 120, "candidatesTokenCount": 30, "totalTokenCount": 150},
        ),
        _gemini_response(
            {
                "kind": "complete",
                "description": "Report findings",
                "output": "Observed Welcome to fixture",
            },
            usage={"promptTokenCount": 180, "candidatesTokenCount": 20, "totalTokenCount": 200},
        ),
    ]
    sink = MemoryEventSink()
    config = GeminiProviderConfig(api_key="test-key", model="gemini-2.5-flash")
    async with create_gemini_agent_backend(
        config,
        transport=httpx.MockTransport(lambda _request: responses.pop(0)),
    ) as backend:
        supervisor = RunSupervisor(
            agent=backend,
            browser=adapter,
            policy=ScriptedPolicyEvaluator(),
            event_sink=sink,
            clock=lambda: NOW,
            run_id_factory=lambda: "run-e2e-loop",
        )
        result = await supervisor.run(TASK)

    assert result.state is RunState.COMPLETED
    assert result.output == "Observed Welcome to fixture"
    assert result.usage.steps == 2
    assert result.usage.model_calls == 2
    assert result.usage.browser_actions == 1
    assert result.usage.total_tokens == 350
    assert result.usage.estimated_cost_usd > 0
    assert [e.kind for e in sink.events].count(EventKind.BROWSER_STATE_OBSERVED) == 2
