# Covers model selection in mongodb_agent_engine/llm.py: provider choice, the optional
# gateway wiring, and what happens when the key or the provider name is wrong.

from __future__ import annotations

import sys
import types

import pytest

from mongodb_agent_engine import llm


@pytest.fixture
def env(monkeypatch):
    """A clean LLM environment: no provider, no key, no model override."""
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_HEADER"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def fake_providers(monkeypatch):
    """Stand in for the langchain provider packages, which live in the image.

    ``build_llm`` imports them inside the function, so putting stubs in
    ``sys.modules`` is enough. Each stub records the kwargs it was built with.
    """
    built: dict[str, dict] = {}

    def _module(mod_name: str, cls_name: str):
        module = types.ModuleType(mod_name)

        class _Chat:
            def __init__(self, **kwargs):
                built[cls_name] = kwargs

        setattr(module, cls_name, _Chat)
        monkeypatch.setitem(sys.modules, mod_name, module)

    _module("langchain_anthropic", "ChatAnthropic")
    _module("langchain_openai", "ChatOpenAI")
    return built


def test_a_missing_key_is_an_error_not_a_silent_anonymous_call(env):
    env.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        llm.build_llm()


def test_an_unknown_provider_names_the_supported_ones(env):
    env.setenv("LLM_PROVIDER", "bedrock")
    env.setenv("LLM_API_KEY", "k")
    with pytest.raises(RuntimeError) as excinfo:
        llm.build_llm()
    message = str(excinfo.value)
    assert "bedrock" in message
    assert "anthropic" in message


def test_the_default_provider_is_anthropic_called_directly(env, fake_providers):
    env.setenv("LLM_API_KEY", "direct-key")
    llm.build_llm()
    kwargs = fake_providers["ChatAnthropic"]
    assert "base_url" not in kwargs
    assert kwargs["model_name"] == "claude-sonnet-5"


@pytest.mark.parametrize(
    ("provider", "cls_name"),
    [("anthropic", "ChatAnthropic"), ("openai", "ChatOpenAI")],
)
def test_a_gateway_is_configured_by_base_url_and_key_header(
    env, fake_providers, provider, cls_name
):
    env.setenv("LLM_PROVIDER", provider)
    env.setenv("LLM_API_KEY", "gateway-key")
    env.setenv("LLM_BASE_URL", "https://llm-gateway.example.com/v1")
    env.setenv("LLM_API_KEY_HEADER", "api-key")
    llm.build_llm()
    kwargs = fake_providers[cls_name]
    assert kwargs["base_url"] == "https://llm-gateway.example.com/v1"
    assert kwargs["default_headers"] == {"api-key": "gateway-key"}


def test_a_gateway_without_a_key_header_uses_the_provider_auth(env, fake_providers):
    env.setenv("LLM_API_KEY", "gateway-key")
    env.setenv("LLM_BASE_URL", "https://llm-gateway.example.com/v1")
    llm.build_llm()
    kwargs = fake_providers["ChatAnthropic"]
    assert kwargs["base_url"] == "https://llm-gateway.example.com/v1"
    assert "default_headers" not in kwargs


def test_gemini_rejects_a_gateway_base_url(env):
    env.setenv("LLM_PROVIDER", "gemini")
    env.setenv("LLM_API_KEY", "k")
    env.setenv("LLM_BASE_URL", "https://llm-gateway.example.com/v1")
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        llm.build_llm()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_direct_providers_send_no_gateway_base_url(env, fake_providers, provider):
    env.setenv("LLM_PROVIDER", provider)
    env.setenv("LLM_API_KEY", "direct-key")
    llm.build_llm()
    kwargs = next(iter(fake_providers.values()))
    assert "base_url" not in kwargs
    assert "default_headers" not in kwargs


def test_the_model_override_wins_over_the_provider_default(env, fake_providers):
    env.setenv("LLM_API_KEY", "direct-key")
    env.setenv("LLM_MODEL", "claude-haiku-4-5-20251001")
    llm.build_llm()
    assert fake_providers["ChatAnthropic"]["model_name"] == "claude-haiku-4-5-20251001"



def test_no_temperature_is_sent_unless_one_is_asked_for(env, fake_providers):
    """claude-sonnet-5 rejects `temperature` outright.

    The provider answers `invalid_request_error: \\`temperature\\` is deprecated
    for this model`, which fails the whole invocation, so the default build must
    not send the parameter at all.
    """
    env.setenv("LLM_API_KEY", "direct-key")
    llm.build_llm()
    assert "temperature" not in fake_providers["ChatAnthropic"]


def test_an_explicit_temperature_is_still_forwarded(env, fake_providers):
    env.setenv("LLM_API_KEY", "direct-key")
    llm.build_llm(temperature=0.7)
    assert fake_providers["ChatAnthropic"]["temperature"] == 0.7
