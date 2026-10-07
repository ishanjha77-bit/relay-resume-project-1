"""The LLM provider behind a model route (see config.ModelRoute)."""

from __future__ import annotations

from pydantic import SecretStr

from relay_agent.config import ModelRoute, Settings
from relay_agent.llm.types import LLMProvider


class MissingCredentials(RuntimeError):
    """The route's provider has no API key configured."""


def _key(secret: SecretStr | None) -> str | None:
    value = secret.get_secret_value().strip() if secret is not None else ""
    return value or None


def make_provider(route: ModelRoute, settings: Settings) -> LLMProvider:
    if route.provider == "gemini":
        from relay_agent.llm.gemini_provider import GeminiProvider

        key = _key(settings.gemini_api_key)
        if key is None:
            raise MissingCredentials(
                "GEMINI_API_KEY is not set: create a free key at https://aistudio.google.com/apikey "
                "and add it to relay/.env"
            )
        return GeminiProvider(api_key=key)
    if route.provider == "anthropic":
        from relay_agent.llm.anthropic_provider import AnthropicProvider

        key = _key(settings.anthropic_api_key)
        if key is None:
            raise MissingCredentials("ANTHROPIC_API_KEY is not set: add it to relay/.env")
        return AnthropicProvider(api_key=key)
    raise ValueError(f"unknown provider {route.provider!r}")
