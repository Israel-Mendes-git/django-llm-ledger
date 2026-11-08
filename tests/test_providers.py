import json

import httpx
import pytest

from llm_ledger.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def transport(status=200, body=None, capture=None, exc=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        if exc:
            raise exc
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler)


def test_ollama_parses_text_and_tokens():
    seen = []
    provider = OllamaProvider(
        "llama3.2",
        transport=transport(body={"message": {"content": "olá"}, "prompt_eval_count": 7, "eval_count": 3}, capture=seen),
    )
    completion = provider.complete(MESSAGES, system="be nice", max_tokens=50)
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("olá", 7, 3)
    payload = json.loads(seen[0].content)
    assert payload["messages"][0] == {"role": "system", "content": "be nice"}
    assert payload["options"]["num_predict"] == 50
    assert str(seen[0].url) == "http://localhost:11434/api/chat"


def test_openai_compatible_sends_key_and_parses_usage():
    seen = []
    body = {"choices": [{"message": {"content": "hey"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
    provider = OpenAICompatibleProvider(
        "some-model", api_key="sk-test", base_url="http://local:1234/v1", transport=transport(body=body, capture=seen)
    )
    completion = provider.complete(MESSAGES)
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("hey", 5, 2)
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert str(seen[0].url) == "http://local:1234/v1/chat/completions"


def test_anthropic_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-env")
    seen = []
    body = {"content": [{"type": "text", "text": "hi "}, {"type": "text", "text": "there"}],
            "usage": {"input_tokens": 4, "output_tokens": 2}}
    provider = AnthropicProvider("claude-x", transport=transport(body=body, capture=seen))
    completion = provider.complete(MESSAGES, system="sys")
    assert completion.text == "hi there"
    assert seen[0].headers["x-api-key"] == "ak-env"
    assert json.loads(seen[0].content)["system"] == "sys"


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (400, False), (401, False)])
def test_http_errors_are_classified(status, retryable):
    provider = OllamaProvider("m", transport=transport(status=status, body={"error": "x"}))
    with pytest.raises(ProviderError) as info:
        provider.complete(MESSAGES)
    assert info.value.retryable is retryable
    assert info.value.status_code == status


def test_timeouts_and_connection_errors_are_retryable():
    for exc in (httpx.ReadTimeout("slow"), httpx.ConnectError("refused")):
        provider = OllamaProvider("m", transport=transport(exc=exc))
        with pytest.raises(ProviderError) as info:
            provider.complete(MESSAGES)
        assert info.value.retryable


def test_unexpected_shape_is_an_error():
    provider = OpenAICompatibleProvider("m", api_key="k", transport=transport(body={"weird": True}))
    with pytest.raises(ProviderError):
        provider.complete(MESSAGES)


def test_build_provider_from_settings_entry():
    provider = build_provider({"name": "ollama", "model": "qwen", "base_url": "http://box:11434/"})
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://box:11434"
    custom = build_provider({"name": "llm_ledger.providers.FakeProvider", "model": "z"})
    assert custom.model == "z"


def test_ollama_model_options_are_passed_through():
    seen = []
    provider = OllamaProvider("m", model_options={"num_gpu": 0},
                              transport=transport(body={"message": {"content": "x"}}, capture=seen))
    provider.complete(MESSAGES)
    assert json.loads(seen[0].content)["options"]["num_gpu"] == 0


def test_global_timeout_setting_is_used(settings):
    settings.LLM_LEDGER = {**settings.LLM_LEDGER, "TIMEOUT": 7}
    captured = {}

    def handler(request):
        captured.update(request.extensions["timeout"])
        return httpx.Response(200, json={"message": {"content": "x"}})

    OllamaProvider("m", transport=httpx.MockTransport(handler)).complete(MESSAGES)
    assert captured["read"] == 7
    OllamaProvider("m", timeout=3, transport=httpx.MockTransport(handler)).complete(MESSAGES)
    assert captured["read"] == 3
