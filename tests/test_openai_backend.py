"""Unit tests for the OpenAI StructuredModelProvider wire adapter."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import pytest

from tests.browser_fakes import FakeBrowser, FakeContext, FakePage, FakeStagehand
from tests.fakes import MemoryEventSink, ScriptedPolicyEvaluator
from ubc_node.agent_backend import (
    PROPOSAL_JSON_SCHEMA,
    PROPOSAL_SYSTEM_INSTRUCTION,
    ModelPricing,
    ProposalRequest,
)
from ubc_node.browser_contracts import BrowserSessionConfig
from ubc_node.contracts import (
    AgentBackendError,
    EventKind,
    JsonValue,
    RunState,
    TaskSpec,
)
from ubc_node.openai_backend import (
    OpenAIModelProvider,
    OpenAIProviderConfig,
    create_openai_agent_backend,
)
from ubc_node.stagehand_browser import (
    StagehandBrowserAdapter,
    _BrowserLike,  # pyright: ignore[reportPrivateUsage]
    _StagehandLike,  # pyright: ignore[reportPrivateUsage]
)
from ubc_node.supervisor import RunSupervisor

NOW = datetime(2026, 11, 21, 5, 0, tzinfo=UTC)
TASK = TaskSpec("task-1", "Research the fixture site", metadata={"origin": "https://fixture.test"})
REQUEST = ProposalRequest(
    system_instruction=PROPOSAL_SYSTEM_INSTRUCTION,
    user_prompt='{"task":{"objective":"test"}}',
    json_schema=PROPOSAL_JSON_SCHEMA,
)


def _openai_response(
    proposal: Mapping[str, JsonValue],
    *,
    usage: Mapping[str, object] | None = None,
    use_output_blocks: bool = False,
) -> httpx.Response:
    text = json.dumps(proposal)
    body: dict[str, object]
    if use_output_blocks:
        body = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ]
        }
    else:
        body = {"output_text": text}
    if usage is not None:
        body["usage"] = dict(usage)
    return httpx.Response(200, json=body)


def test_openai_config_validation_redaction_pricing_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = OpenAIProviderConfig(api_key="sk-super-secret", base_url="https://api.test/v1/")
    assert "sk-super-secret" not in repr(config)
    assert config.model == "gpt-5-mini"
    assert config.base_url == "https://api.test/v1"
    assert config.resolved_pricing == ModelPricing(0.25, 2.00)

    custom_pricing = ModelPricing(1.0, 2.0)
    custom = OpenAIProviderConfig(api_key="k", model="unknown-model", pricing=custom_pricing)
    assert custom.resolved_pricing is custom_pricing
    unknown = OpenAIProviderConfig(api_key="k", model="unknown-model")
    assert unknown.resolved_pricing == ModelPricing(0.0, 0.0)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
    monkeypatch.setenv("UBC_OPENAI_MODEL", "gpt-5-nano")
    from_env = OpenAIProviderConfig.from_env()
    assert from_env.api_key == "sk-env-key"
    assert from_env.model == "gpt-5-nano"

    with pytest.raises(ValueError, match="api_key"):
        OpenAIProviderConfig(api_key=" ")
    with pytest.raises(ValueError, match="model"):
        OpenAIProviderConfig(api_key="k", model=" ")
    with pytest.raises(ValueError, match="base_url"):
        OpenAIProviderConfig(api_key="k", base_url="ftp://example.test")
    with pytest.raises(ValueError, match="timeout_seconds"):
        OpenAIProviderConfig(api_key="k", timeout_seconds=0)
    with pytest.raises(ValueError, match="max_output_tokens"):
        OpenAIProviderConfig(api_key="k", max_output_tokens=0)
    with pytest.raises(ValueError, match="temperature"):
        OpenAIProviderConfig(api_key="k", temperature=2.5)


@pytest.mark.asyncio
async def test_openai_provider_sends_strict_schema_headers_and_usage() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _openai_response(
            {"kind": "complete", "description": "Done", "output": "ok"},
            usage={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
        )

    config = OpenAIProviderConfig(api_key="sk-test", temperature=0.2)
    async with OpenAIModelProvider(config, transport=httpx.MockTransport(handler)) as provider:
        response = await provider.generate_proposal(REQUEST)

    assert json.loads(response.text) == {
        "kind": "complete",
        "description": "Done",
        "output": "ok",
    }
    assert (response.input_tokens, response.output_tokens, response.total_tokens) == (200, 80, 280)

    request = captured_requests[0]
    assert request.headers["Authorization"] == "Bearer sk-test"
    payload = json.loads(request.content.decode("utf-8"))
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["temperature"] == 0.2

    fmt = payload["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())
    assert None in schema["properties"]["operation"]["enum"]
    assert schema["properties"]["handoff"]["additionalProperties"] is False
    assert schema["properties"]["schema_fields"]["items"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_openai_provider_handles_output_blocks_shared_client_and_bad_usage() -> None:
    responses = [
        _openai_response(
            {"kind": "complete", "description": "Done", "output": "ok"},
            usage={"input_tokens": True, "output_tokens": -5, "total_tokens": "bad"},
            use_output_blocks=True,
        ),
        _openai_response({"kind": "complete", "description": "Done", "output": "ok"}),
    ]
    config = OpenAIProviderConfig(api_key="sk-test", reasoning_effort=None)
    minimal_request = ProposalRequest(
        system_instruction="sys",
        user_prompt="user",
        json_schema={"type": "object"},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: responses.pop(0))
    ) as shared_client:
        provider = OpenAIModelProvider(config, client=shared_client)
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
            httpx.Response(200, json={"output": []}),
            "no output",
        ),
        (
            httpx.Response(
                200,
                json={
                    "output": [
                        "non-dict",
                        {"type": "reasoning"},
                        {"type": "message", "content": ["non-dict", {"type": "other"}]},
                        {"type": "message", "content": [{"type": "output_text", "text": " "}]},
                    ]
                },
            ),
            "no output text",
        ),
        (
            httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": "Policy violation"}],
                        }
                    ]
                },
            ),
            "OpenAI model refused proposal: Policy violation",
        ),
        (
            httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "refusal", "refusal": " "}],
                        }
                    ]
                },
            ),
            "OpenAI model refused proposal: refused",
        ),
    ],
)
async def test_openai_provider_rejects_malformed_or_failed_responses(
    response: httpx.Response,
    expected_match: str,
) -> None:
    config = OpenAIProviderConfig(api_key="sk-test")
    async with OpenAIModelProvider(
        config,
        transport=httpx.MockTransport(lambda _request: response),
    ) as provider:
        with pytest.raises(AgentBackendError, match=expected_match):
            await provider.generate_proposal(REQUEST)


@pytest.mark.asyncio
async def test_openai_provider_wraps_http_transport_errors_without_leaking_key() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = OpenAIProviderConfig(api_key="sk-secret-api-key")
    async with OpenAIModelProvider(
        config,
        transport=httpx.MockTransport(failing_handler),
    ) as provider:
        with pytest.raises(AgentBackendError, match="ConnectError") as exc_info:
            await provider.generate_proposal(REQUEST)
    assert "sk-secret-api-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_supervisor_openai_factory_and_stagehand_adapter_run_bounded_loop(
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
        _openai_response(
            {
                "kind": "browser",
                "description": "Open the fixture homepage",
                "operation": "open",
                "url": "https://fixture.test/",
            },
            usage={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        ),
        _openai_response(
            {
                "kind": "complete",
                "description": "Report findings",
                "output": "Observed Welcome to fixture",
            },
            usage={"input_tokens": 180, "output_tokens": 20, "total_tokens": 200},
        ),
    ]
    sink = MemoryEventSink()
    config = OpenAIProviderConfig(api_key="sk-test", model="gpt-5-mini")
    async with create_openai_agent_backend(
        config,
        transport=httpx.MockTransport(lambda _request: responses.pop(0)),
    ) as backend:
        supervisor = RunSupervisor(
            agent=backend,
            browser=adapter,
            policy=ScriptedPolicyEvaluator(),
            event_sink=sink,
            clock=lambda: NOW,
            run_id_factory=lambda: "run-openai-e2e",
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
