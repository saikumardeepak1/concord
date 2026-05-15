"""Claude API wrapper.

All model access in Concord goes through this module. It owns:

- Tier selection: callers pass a `ModelTier`, never a raw model ID. This is the
  enforcement point for ADR-003 (tiered model strategy).
- Retries with exponential backoff for transient failures (rate limits, 5xx).
- Structured-output parsing with one repair attempt.
- Token accounting routed to Prometheus.
- Prompt cache annotations on stable system prompts.

The Anthropic SDK is the only place we touch Anthropic's API; if we ever swap
providers or add a fallback model, this is the single file that changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog
from anthropic import APIError, APIStatusError, AsyncAnthropic, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from concord.config import ModelTier, get_settings
from concord.observability.metrics import get_metrics
from concord.observability.tracing import span

_log = structlog.get_logger("concord.llm")
_TModel = TypeVar("_TModel", bound=BaseModel)


# Models that reject the `temperature` parameter. Opus 4.7 uses extended
# thinking and returns 400 if temperature is provided. Keep this explicit so
# the failure is local when Anthropic adds more models with this restriction.
_NO_TEMPERATURE_MODELS = {
    "claude-opus-4-7",
}


def _model_rejects_temperature(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _NO_TEMPERATURE_MODELS)

# Rough public-list prices per 1M tokens (USD). Update as Anthropic prices change.
# Stored in micro-USD-per-token so a single multiplication gives micro-USD totals.
_PRICE_INPUT_MICRO = {
    ModelTier.FAST: 1.0,      # haiku ~ $1/MTok input
    ModelTier.STANDARD: 3.0,  # sonnet ~ $3/MTok input
    ModelTier.HIGH: 15.0,     # opus ~ $15/MTok input
}
_PRICE_OUTPUT_MICRO = {
    ModelTier.FAST: 5.0,
    ModelTier.STANDARD: 15.0,
    ModelTier.HIGH: 75.0,
}


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    tier: ModelTier
    model: str

    @property
    def cost_micro_usd(self) -> float:
        return (
            self.input_tokens * _PRICE_INPUT_MICRO[self.tier]
            + self.output_tokens * _PRICE_OUTPUT_MICRO[self.tier]
        )


class LLMError(Exception):
    """Raised when the model call fails after retries."""


class StructuredOutputError(LLMError):
    """Raised when structured output cannot be parsed even after one repair."""


class LLMClient:
    """Thin async wrapper around the Anthropic SDK with tier routing."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._client = AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._settings = settings
        self._metrics = get_metrics()

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.2,
        cache_system: bool = True,
        stop_sequences: list[str] | None = None,
    ) -> LLMResponse:
        """One model call. Retries transient errors. Records tokens + cost."""
        settings = self._settings
        model = settings.model_for_tier(tier)
        budget = max_tokens or settings.max_tokens_per_request

        # Use prompt caching on stable system prompts (ADR-006 cost mitigation).
        system_param: Any
        if cache_system:
            system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            system_param = system

        # Some models (e.g. Opus 4.7 with extended thinking) reject the
        # `temperature` parameter outright. We only send it for models that
        # still accept it. Keep the list explicit so failures are local.
        accepts_temperature = not _model_rejects_temperature(model)

        async with span("llm.complete", tier=tier.value, model=model) as s:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type((RateLimitError, APIError, APIStatusError)),
                reraise=True,
            ):
                with attempt:
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "system": system_param,
                        "messages": messages,
                        "max_tokens": budget,
                        "stop_sequences": stop_sequences or [],
                        "timeout": settings.request_timeout_seconds,
                    }
                    if accepts_temperature:
                        kwargs["temperature"] = temperature
                    resp = await self._client.messages.create(**kwargs)

            text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            text = "".join(text_parts).strip()

            in_tok = resp.usage.input_tokens
            out_tok = resp.usage.output_tokens

            self._metrics.tokens_total.labels(tier=tier.value, direction="input").inc(in_tok)
            self._metrics.tokens_total.labels(tier=tier.value, direction="output").inc(out_tok)
            cost = in_tok * _PRICE_INPUT_MICRO[tier] + out_tok * _PRICE_OUTPUT_MICRO[tier]
            self._metrics.cost_micro_usd.labels(tier=tier.value).inc(cost)

            s.attributes.update(
                input_tokens=in_tok,
                output_tokens=out_tok,
                stop_reason=resp.stop_reason,
                cost_micro_usd=cost,
            )

            return LLMResponse(
                text=text,
                input_tokens=in_tok,
                output_tokens=out_tok,
                stop_reason=resp.stop_reason,
                tier=tier,
                model=model,
            )

    async def complete_structured(
        self,
        *,
        tier: ModelTier,
        system: str,
        user_prompt: str,
        schema_model: type[_TModel],
        max_tokens: int | None = None,
        temperature: float = 0.0,
        cache_system: bool = True,
    ) -> tuple[_TModel, LLMResponse]:
        """Get JSON output validated against a pydantic schema.

        Strategy: prepend a strict instruction to emit only a single JSON object
        matching the schema. Parse, validate, and if it fails do ONE repair pass
        that re-states the schema and the parse error. After that, we raise and
        let the caller fall back (per the edge-case policy in section 5).
        """
        schema_json = schema_model.model_json_schema()
        format_instructions = (
            "Respond with a single JSON object matching this JSON Schema and nothing else. "
            "Do not wrap it in markdown fences. Do not include commentary.\n\n"
            f"Schema:\n{json.dumps(schema_json, indent=2)}"
        )
        full_system = f"{system}\n\n{format_instructions}"
        messages = [{"role": "user", "content": user_prompt}]

        resp = await self.complete(
            tier=tier,
            system=full_system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_system=cache_system,
        )

        parsed = _try_parse_json(resp.text)
        if parsed is not None:
            try:
                return schema_model.model_validate(parsed), resp
            except ValidationError as ve:
                last_error = str(ve)
        else:
            last_error = "Output was not valid JSON."

        # One repair pass with the error context.
        repair_messages = messages + [
            {"role": "assistant", "content": resp.text},
            {
                "role": "user",
                "content": (
                    "Your previous reply did not match the required schema. "
                    f"Validation error:\n{last_error}\n"
                    "Reply again with a single valid JSON object that satisfies the schema. "
                    "No prose, no fences."
                ),
            },
        ]
        repaired = await self.complete(
            tier=tier,
            system=full_system,
            messages=repair_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            cache_system=cache_system,
        )
        parsed = _try_parse_json(repaired.text)
        if parsed is None:
            raise StructuredOutputError(f"Could not parse JSON after repair: {repaired.text[:200]}")
        try:
            return schema_model.model_validate(parsed), repaired
        except ValidationError as ve:
            raise StructuredOutputError(f"Schema validation failed after repair: {ve}") from ve


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(text: str) -> Any | None:
    """Best-effort JSON extraction. Handles fenced output and stray prose."""
    text = text.strip()
    # Strip ``` fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


_client_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = LLMClient()
    return _client_singleton
