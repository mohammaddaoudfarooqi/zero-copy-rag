# Builds the chat model for the deep agent. Provider, model and gateway live
# here rather than in agent.yaml, which is the platform's convention.

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

if TYPE_CHECKING:  # the provider packages ship with the agent image, not with the tests
    from langchain_core.language_models import BaseChatModel

_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-pro",
}

DEFAULT_PROVIDER = "anthropic"

# The host each provider calls when LLM_BASE_URL is unset. agent.yaml's egress
# allow-list names the default provider's host; any other host, including a
# gateway's, has to be added there before a deploy can reach it.
PROVIDER_HOSTS = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "gemini": "generativelanguage.googleapis.com",
}


def _gateway_kwargs(provider: str, api_key: str) -> dict[str, Any]:
    """Base URL and optional auth header for a model reached through a gateway.

    LLM_BASE_URL points an Anthropic- or OpenAI-compatible client at the gateway.
    LLM_API_KEY_HEADER names a header to carry the key for gateways that read it
    from somewhere other than the provider's usual auth header; ``api_key`` is
    still passed because both client libraries require one to construct.
    """
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        return {}
    if provider == "gemini":
        raise RuntimeError("LLM_BASE_URL is supported for the anthropic and openai providers only")
    extra: dict[str, Any] = {"base_url": base_url}
    header = os.environ.get("LLM_API_KEY_HEADER", "").strip()
    if header:
        extra["default_headers"] = {header: api_key}
    return extra


def build_llm(temperature: float | None = None) -> "BaseChatModel":
    """Build the chat model named by LLM_PROVIDER, keyed by LLM_API_KEY.

    ``temperature`` is omitted from the request unless a caller passes one.
    The current frontier models reject it outright: claude-sonnet-5 answers
    ``invalid_request_error: `temperature` is deprecated for this model``, which
    fails every invocation rather than degrading. Sending a sampling parameter
    only when it was actually asked for keeps the default path working across
    model generations.

    Supported providers: ``anthropic`` (the default), ``openai`` and ``gemini``.
    Set LLM_BASE_URL (and LLM_API_KEY_HEADER if needed) to route anthropic or
    openai through a compatible gateway.

    Do not wrap the result in ``app.llm()`` before handing it to
    ``app.deep_agent()``: deep_agent applies ``SecureWrappedLLM`` itself, and
    double-wrapping registers the model twice under the same id.
    """
    provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider not in _DEFAULT_MODELS:
        raise RuntimeError(
            f"LLM_PROVIDER={provider!r} is not supported; "
            f"use one of {', '.join(sorted(_DEFAULT_MODELS))}"
        )

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is missing; add it to .env or project secrets")

    model = os.environ.get("LLM_MODEL") or _DEFAULT_MODELS[provider]
    extra = _gateway_kwargs(provider, api_key)
    if temperature is not None:
        extra["temperature"] = temperature

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model_name=model, api_key=SecretStr(api_key), **extra)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=SecretStr(api_key), **extra)

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **extra)
