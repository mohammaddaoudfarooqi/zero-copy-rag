# Builds the chat model for the deep agent. Provider, model and gateway live
# here rather than in agent.yaml, which is the platform's convention.

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

if TYPE_CHECKING:  # the provider packages ship with the agent image, not with the tests
    from langchain_core.language_models import BaseChatModel

# The Grove gateway host. It is also an entry in agent.yaml's egress allow-list;
# the two have to agree or every model call fails with a network error rather
# than an auth error, so keep them in sync.
GROVE_HOST = "grove-gateway-prod.azure-api.net"

_GROVE_BASE_URLS = {
    "grove-anthropic": f"https://{GROVE_HOST}/grove-foundry-prod/anthropic",
    "grove-openai": f"https://{GROVE_HOST}/grove-foundry-prod/openai/v1",
}

_DEFAULT_MODELS = {
    "grove-anthropic": "claude-sonnet-5",
    "grove-openai": "gpt-5.4",
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5.4",
    "gemini": "gemini-2.5-pro",
}

DEFAULT_PROVIDER = "grove-anthropic"


def _grove_kwargs(provider: str, api_key: str) -> dict[str, Any]:
    """Base URL and auth header for a Grove-routed model.

    Grove's own documentation ships these as ``ChatGroveOpenAI`` and
    ``ChatGroveAnthropic`` subclasses. Passing the same kwargs directly does the
    same thing with less code, and it keeps the provider imports lazy: the
    langchain provider packages are in the agent image, not in the test
    environment, so nothing here may import them at module scope.

    Grove authenticates on the ``api-key`` header. ``api_key`` is passed as well
    because both client libraries require one to construct.
    """
    return {
        "base_url": _GROVE_BASE_URLS[provider],
        "default_headers": {"api-key": api_key},
    }


def build_llm(temperature: float | None = None) -> "BaseChatModel":
    """Build the chat model named by LLM_PROVIDER, keyed by LLM_API_KEY.

    ``temperature`` is omitted from the request unless a caller passes one.
    The current frontier models reject it outright: Grove returns
    ``invalid_request_error: `temperature` is deprecated for this model`` for
    claude-sonnet-5, which fails every invocation rather than degrading. Sending
    a sampling parameter only when it was actually asked for keeps the default
    path working across model generations.

    Supported providers: ``grove-anthropic`` (the default) and ``grove-openai``
    route through the Grove gateway; ``anthropic``, ``openai`` and ``gemini``
    call the provider directly and need their host added to the agent.yaml
    egress allow-list first.

    Do not wrap the result in ``app.llm()`` before handing it to
    ``app.deep_agent()``: deep_agent applies ``SecureWrappedLLM`` itself, and
    double-wrapping registers the model twice under the same id. Grove's docs
    say to pass the model to ``app.llm()``, which is right for a plain agent and
    wrong for this one.
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
    extra = _grove_kwargs(provider, api_key) if provider.startswith("grove-") else {}
    if temperature is not None:
        extra["temperature"] = temperature

    if provider in ("grove-anthropic", "anthropic"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model_name=model, api_key=SecretStr(api_key), **extra)

    if provider in ("grove-openai", "openai"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=SecretStr(api_key), **extra)

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **extra)
