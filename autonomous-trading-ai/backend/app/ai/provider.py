"""LLM providers. The model only returns JSON analysis; it never executes orders."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


class AIUnavailable(Exception):
    pass


class LLMProvider(ABC):
    name: str = "none"
    model: str | None = None

    @abstractmethod
    async def complete_json(self, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4000) -> dict[str, Any]: ...


class NullProvider(LLMProvider):
    name = "none"

    async def complete_json(self, system, prompt, schema, max_tokens=4000):
        raise AIUnavailable("no AI provider configured")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "low"):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=120)
        self.model = model
        self.effort = effort

    async def complete_json(self, system, prompt, schema, max_tokens=4000):
        a = self._anthropic
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            try:
                # server-side refusal fallback (opt-in); retried without it if the account rejects the beta
                resp = await self.client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except a.BadRequestError as exc:
                log.info("fallbacks not accepted (%s); retrying plain request", exc)
                resp = await self.client.messages.create(**kwargs)
        except a.RateLimitError as exc:
            raise AIUnavailable(f"rate limited: {exc}") from exc
        except a.APIStatusError as exc:
            raise AIUnavailable(f"API error {exc.status_code}: {exc}") from exc
        except a.APIConnectionError as exc:
            raise AIUnavailable(f"connection error: {exc}") from exc
        if resp.stop_reason == "refusal":
            raise AIUnavailable("model declined the request")
        if resp.stop_reason == "max_tokens":
            raise AIUnavailable("response truncated (max_tokens)")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            raise AIUnavailable("empty response")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIUnavailable("invalid JSON from model") from exc


class OpenAICompatibleProvider(LLMProvider):
    """Any server exposing POST {base}/chat/completions (OpenAI-compatible)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete_json(self, system, prompt, schema, max_tokens=4000):
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system + "\nRespond ONLY with JSON matching this schema: " + json.dumps(schema)},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=body,
                                 headers={"authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            raise AIUnavailable(f"openai-compatible provider failed: {exc}") from exc


def build_provider(s: Settings) -> LLMProvider:
    if s.ai_provider == "anthropic" and s.anthropic_api_key:
        return AnthropicProvider(s.anthropic_api_key, s.ai_model, s.ai_effort)
    if s.ai_provider == "openai_compatible" and s.openai_compatible_base_url and s.openai_compatible_model:
        return OpenAICompatibleProvider(s.openai_compatible_base_url, s.openai_compatible_api_key, s.openai_compatible_model)
    return NullProvider()
