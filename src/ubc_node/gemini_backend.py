"""Gemini wire adapter implementing the provider-neutral StructuredModelProvider."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType, TracebackType
from typing import Literal, Self, cast

import httpx

from ubc_node.agent_backend import (
    ModelPricing,
    ModelResponse,
    ProposalRequest,
    StructuredAgentBackend,
)
from ubc_node.contracts import AgentBackendError, JsonValue

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

DEFAULT_GEMINI_PRICING: Mapping[str, ModelPricing] = MappingProxyType(
    {
        "gemini-3.8-flash": ModelPricing(0.75, 3.75),
        "gemini-2.5-flash": ModelPricing(0.30, 2.50),
        "gemini-2.5-pro": ModelPricing(1.25, 10.00),
    }
)


type GeminiThinkingLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig:
    """Connection and generation settings for the Gemini generateContent API."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_GEMINI_MODEL
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 60.0
    temperature: float = 0.0
    max_output_tokens: int = 4096
    thinking_level: GeminiThinkingLevel | None = "low"
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
        if not math.isfinite(self.temperature) or not (0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def resolved_pricing(self) -> ModelPricing:
        """Return explicit pricing, known Gemini model pricing, or zero-cost fallback."""
        if self.pricing is not None:
            return self.pricing
        return DEFAULT_GEMINI_PRICING.get(self.model, ModelPricing(0.0, 0.0))

    @classmethod
    def from_env(
        cls,
        *,
        model: str | None = None,
        pricing: ModelPricing | None = None,
    ) -> Self:
        """Construct configuration from standard Gemini environment variables."""
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        resolved_model = (
            model
            or os.environ.get("UBC_GEMINI_MODEL")
            or os.environ.get("GEMINI_MODEL")
            or DEFAULT_GEMINI_MODEL
        )
        return cls(api_key=api_key, model=resolved_model, pricing=pricing)


class GeminiModelProvider:
    """Thin HTTP adapter translating ProposalRequest to Gemini's generateContent API."""

    def __init__(
        self,
        config: GeminiProviderConfig,
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
        """Return the configured per-token pricing for this Gemini model."""
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
        """Send one structured JSON request to Gemini and return raw text and token counts."""
        endpoint = f"{self._config.base_url}/models/{self._config.model}:generateContent"
        generation_config: dict[str, JsonValue] = {
            "temperature": self._config.temperature,
            "maxOutputTokens": self._config.max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": dict(request.json_schema),
        }
        if self._config.thinking_level is not None:
            generation_config["thinkingConfig"] = {
                "thinkingLevel": self._config.thinking_level,
            }
        request_body: dict[str, JsonValue] = {
            "systemInstruction": {
                "parts": [{"text": request.system_instruction}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.user_prompt}],
                }
            ],
            "generationConfig": generation_config,
        }
        try:
            response = await self._client.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._config.api_key,
                },
                json=request_body,
            )
        except httpx.HTTPError as error:
            raise AgentBackendError(f"Gemini request failed: {type(error).__name__}") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise AgentBackendError(
                f"Gemini API returned HTTP {response.status_code}: {self._error_summary(response)}"
            )

        try:
            raw_body = response.json()
        except ValueError as error:
            raise AgentBackendError("Gemini API returned invalid JSON") from error

        if not isinstance(raw_body, dict):
            raise AgentBackendError("Gemini API response must be a JSON object")

        body = cast(Mapping[str, object], raw_body)
        text = self._extract_candidate_text(body)
        input_tokens, output_tokens, total_tokens = self._extract_usage_counts(
            body.get("usageMetadata")
        )
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
        input_tokens = GeminiModelProvider._non_negative_int(usage_map.get("promptTokenCount"))
        candidate_tokens = GeminiModelProvider._non_negative_int(
            usage_map.get("candidatesTokenCount")
        )
        thought_tokens = GeminiModelProvider._non_negative_int(usage_map.get("thoughtsTokenCount"))
        total_tokens = GeminiModelProvider._non_negative_int(usage_map.get("totalTokenCount"))
        # Thinking tokens are billed at the output token rate.
        return input_tokens, candidate_tokens + thought_tokens, total_tokens

    @staticmethod
    def _non_negative_int(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    @staticmethod
    def _extract_candidate_text(body: Mapping[str, object]) -> str:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise AgentBackendError("Gemini API response contained no candidates")
        first: object = cast(list[object], candidates)[0]
        if not isinstance(first, Mapping):
            raise AgentBackendError("Gemini candidate must be a JSON object")
        content = cast(Mapping[str, object], first).get("content")
        if not isinstance(content, Mapping):
            raise AgentBackendError("Gemini candidate contained no content")
        parts = cast(Mapping[str, object], content).get("parts")
        if not isinstance(parts, list) or not parts:
            raise AgentBackendError("Gemini candidate contained no content parts")
        texts: list[str] = []
        for raw_part in cast(list[object], parts):
            if isinstance(raw_part, Mapping):
                text = cast(Mapping[str, object], raw_part).get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        if not texts:
            raise AgentBackendError("Gemini candidate contained no text part")
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


def create_gemini_agent_backend(
    config: GeminiProviderConfig,
    *,
    max_history_events: int = 20,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> StructuredAgentBackend:
    """Create a provider-neutral StructuredAgentBackend backed by Gemini."""
    provider = GeminiModelProvider(config, transport=transport, client=client)
    return StructuredAgentBackend(provider, max_history_events=max_history_events)
