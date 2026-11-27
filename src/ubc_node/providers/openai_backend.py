"""OpenAI wire adapter implementing the provider-neutral StructuredModelProvider."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType, TracebackType
from typing import Literal, Self, cast

import httpx

from ubc_node.core.contracts import AgentBackendError, JsonValue
from ubc_node.providers.agent_backend import (
    ModelPricing,
    ModelResponse,
    ProposalRequest,
    StructuredAgentBackend,
)

DEFAULT_OPENAI_MODEL = "gpt-5-mini"

DEFAULT_OPENAI_PRICING: Mapping[str, ModelPricing] = MappingProxyType(
    {
        "gpt-5-mini": ModelPricing(0.25, 2.00),
        "gpt-5-nano": ModelPricing(0.05, 0.40),
        "gpt-5.4-mini": ModelPricing(0.75, 4.50),
        "gpt-5.4-nano": ModelPricing(0.20, 1.25),
        "gpt-4.1-mini": ModelPricing(0.40, 1.60),
    }
)

type OpenAIReasoningEffort = Literal["minimal", "low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    """Connection and generation settings for the OpenAI Responses API."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_OPENAI_MODEL
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 4096
    reasoning_effort: OpenAIReasoningEffort | None = "low"
    temperature: float | None = None
    pricing: ModelPricing | None = None

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("api_key must not be empty")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not (0.0 <= self.temperature <= 2.0)
        ):
            raise ValueError("temperature must be between 0.0 and 2.0")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def resolved_pricing(self) -> ModelPricing:
        """Return explicit pricing, known OpenAI model pricing, or zero-cost fallback."""
        if self.pricing is not None:
            return self.pricing
        return DEFAULT_OPENAI_PRICING.get(self.model, ModelPricing(0.0, 0.0))

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        pricing: ModelPricing | None = None,
    ) -> Self:
        """Construct configuration from standard OpenAI environment variables."""
        api_key = os.environ.get("OPENAI_API_KEY") or ""
        resolved_model = (
            model
            or os.environ.get("UBC_OPENAI_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or DEFAULT_OPENAI_MODEL
        )
        return cls(api_key=api_key, model=resolved_model, pricing=pricing)


class OpenAIModelProvider:
    """Thin HTTP adapter translating ProposalRequest to OpenAI's Responses API."""

    def __init__(
        self,
        config: OpenAIProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            transport=transport,
        )

    @property
    def pricing(self) -> ModelPricing:
        """Return the configured per-token pricing for this OpenAI model."""
        return self._config.resolved_pricing

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned by this provider."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.aclose()

    async def generate_proposal(self, request: ProposalRequest) -> ModelResponse:
        """Send one structured JSON request to OpenAI and return raw text and token counts."""
        endpoint = f"{self._config.base_url}/responses"
        request_body: dict[str, JsonValue] = {
            "model": self._config.model,
            "instructions": request.system_instruction,
            "input": request.user_prompt,
            "max_output_tokens": self._config.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ubc_action_proposal",
                    "strict": True,
                    "schema": _to_openai_strict_schema(request.json_schema),
                }
            },
        }
        if self._config.reasoning_effort is not None:
            request_body["reasoning"] = {"effort": self._config.reasoning_effort}
        if self._config.temperature is not None:
            request_body["temperature"] = self._config.temperature

        try:
            response = await self._client.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._config.api_key}",
                },
                json=request_body,
            )
        except httpx.HTTPError as error:
            raise AgentBackendError(f"OpenAI request failed: {type(error).__name__}") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise AgentBackendError(
                f"OpenAI API returned HTTP {response.status_code}: {self._error_summary(response)}"
            )

        try:
            raw_body = response.json()
        except ValueError as error:
            raise AgentBackendError("OpenAI API returned invalid JSON") from error

        if not isinstance(raw_body, dict):
            raise AgentBackendError("OpenAI API response must be a JSON object")

        body = cast(Mapping[str, object], raw_body)
        text = self._extract_output_text(body)
        input_tokens, output_tokens, total_tokens = self._extract_usage_counts(body.get("usage"))
        return ModelResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _extract_usage_counts(raw_usage: object) -> tuple[int, int, int]:
        if not isinstance(raw_usage, Mapping):
            return 0, 0, 0
        usage_map = cast(Mapping[str, object], raw_usage)
        return (
            OpenAIModelProvider._non_negative_int(usage_map.get("input_tokens")),
            OpenAIModelProvider._non_negative_int(usage_map.get("output_tokens")),
            OpenAIModelProvider._non_negative_int(usage_map.get("total_tokens")),
        )

    @staticmethod
    def _non_negative_int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    @staticmethod
    def _extract_output_text(body: Mapping[str, object]) -> str:
        top_level = body.get("output_text")
        if isinstance(top_level, str) and top_level.strip():
            return top_level

        output_items = body.get("output")
        if not isinstance(output_items, list) or not output_items:
            raise AgentBackendError("OpenAI API response contained no output")

        texts: list[str] = []
        for raw_item in cast(list[object], output_items):
            if not isinstance(raw_item, Mapping):
                continue
            item = cast(Mapping[str, object], raw_item)
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for raw_part in cast(list[object], content):
                if not isinstance(raw_part, Mapping):
                    continue
                part = cast(Mapping[str, object], raw_part)
                if part.get("type") == "refusal":
                    refusal = part.get("refusal")
                    reason = refusal if isinstance(refusal, str) and refusal.strip() else "refused"
                    raise AgentBackendError(f"OpenAI model refused proposal: {reason}")
                if part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
        if not texts:
            raise AgentBackendError("OpenAI API response contained no output text")
        return "".join(texts)

    @staticmethod
    def _error_summary(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.reason_phrase or "request failed"
        if isinstance(body, Mapping):
            error = cast(Mapping[str, object], body).get("error")
            if isinstance(error, Mapping):
                message = cast(Mapping[str, object], error).get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        return response.reason_phrase or "request failed"


def _to_openai_strict_schema(schema: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Adapt standard JSON Schema to OpenAI's strict structured-output subset."""
    translated: dict[str, JsonValue] = {}
    schema_type = schema.get("type")
    is_nullable = isinstance(schema_type, list) and "null" in schema_type
    is_object = schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )

    for key, value in schema.items():
        if key == "properties" and isinstance(value, Mapping):
            translated["properties"] = {
                prop_name: _to_openai_strict_schema(cast(Mapping[str, JsonValue], prop_schema))
                for prop_name, prop_schema in value.items()
            }
        elif key == "items" and isinstance(value, Mapping):
            translated["items"] = _to_openai_strict_schema(cast(Mapping[str, JsonValue], value))
        elif key == "enum" and isinstance(value, list) and is_nullable and None not in value:
            translated["enum"] = [*value, None]
        else:
            translated[key] = value

    if is_object:
        translated["additionalProperties"] = False
        properties = translated.get("properties")
        if isinstance(properties, Mapping):
            translated["required"] = list(properties.keys())
    return translated


def create_openai_agent_backend(
    config: OpenAIProviderConfig,
    *,
    max_history_events: int = 20,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> StructuredAgentBackend:
    """Create a provider-neutral StructuredAgentBackend backed by OpenAI."""
    provider = OpenAIModelProvider(config, transport=transport, client=client)
    return StructuredAgentBackend(provider, max_history_events=max_history_events)
