import json
from decimal import Decimal

import httpx
import pytest

from llm_ledger import LLMClient, LLMError
from llm_ledger.models import LLMCall
from llm_ledger.providers import (
    AnthropicProvider,
    FakeProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def raw_transport(text, status=200, capture=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, content=text.encode("utf-8"))

    return httpx.MockTransport(handler)


def drain(stream):
    """Split a provider stream into its text chunks and the final Completion."""
    items = list(stream)
    return items[:-1], items[-1]


# ----------------------------------------------------------------- providers


def test_ollama_streams_ndjson_and_reads_usage_from_last_line():
    seen = []
    body = "\n".join(json.dumps(line) for line in [
        {"message": {"content": "Ol"}, "done": False},
        {"message": {"content": "á!"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 9, "eval_count": 2},
    ])
    chunks, completion = drain(OllamaProvider("m", transport=raw_transport(body, capture=seen)).stream(MESSAGES))
    assert chunks == ["Ol", "á!"]
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("Olá!", 9, 2)
    assert json.loads(seen[0].content)["stream"] is True


def test_ollama_stream_error_line_raises():
    body = json.dumps({"error": "model not found"})
    with pytest.raises(ProviderError, match="model not found"):
        list(OllamaProvider("m", transport=raw_transport(body)).stream(MESSAGES))


def test_openai_streams_sse_with_usage_chunk():
    seen = []
    events = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "He"}}]},
        {"choices": [{"delta": {"content": "llo"}}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    provider = OpenAICompatibleProvider("gpt", api_key="k", transport=raw_transport(body, capture=seen))
    chunks, completion = drain(provider.stream(MESSAGES))
    assert chunks == ["He", "llo"]
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("Hello", 4, 2)
    assert json.loads(seen[0].content)["stream_options"] == {"include_usage": True}


def test_anthropic_streams_events():
    events = [
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 12}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Oi"}}),
        ("ping", {"type": "ping"}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " você"}}),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 3}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    provider = AnthropicProvider("claude", api_key="k", transport=raw_transport(body))
    chunks, completion = drain(provider.stream(MESSAGES))
    assert chunks == ["Oi", " você"]
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("Oi você", 12, 3)


def test_anthropic_overloaded_event_is_retryable():
    body = 'event: error\ndata: {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}\n\n'
    with pytest.raises(ProviderError) as info:
        list(AnthropicProvider("claude", api_key="k", transport=raw_transport(body)).stream(MESSAGES))
    assert info.value.retryable


def test_stream_http_errors_map_like_complete():
    provider = OpenAICompatibleProvider("gpt", transport=raw_transport('{"error": "slow down"}', status=429))
    with pytest.raises(ProviderError) as info:
        list(provider.stream(MESSAGES))
    assert info.value.retryable and info.value.status_code == 429 and "slow down" in str(info.value)


def test_stream_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as info:
        list(OllamaProvider("m", transport=httpx.MockTransport(handler)).stream(MESSAGES))
    assert info.value.retryable


# ----------------------------------------------------------------- client

pytestmark_db = pytest.mark.django_db  # only the client tests touch the database


@pytestmark_db
def test_client_stream_yields_chunks_and_records_one_call(ledger):
    paid = FakeProvider("paid-model", reply="one two three")
    paid.is_local = False
    ledger(PRICES={"paid-model": {"input": 3, "output": 15}})
    stream = LLMClient([paid]).stream("x" * 4000, feature="haiku")
    assert list(stream) == ["one", " two", " three"]
    assert stream.text == "one two three"
    result = stream.result
    assert result.text == "one two three" and result.first_chunk_ms is not None
    call = LLMCall.objects.get()
    assert (call.status, call.feature, call.cost_usd) == ("ok", "haiku", result.cost_usd)
    assert call.request_id == stream.request_id == result.request_id
    assert result.cost_usd == Decimal("0.003045")  # 1000 in * 3/M + 3 out * 15/M


@pytestmark_db
def test_client_stream_falls_back_before_first_chunk(ledger):
    ledger(MAX_RETRIES=1, RETRY_BACKOFF=0)
    stream = LLMClient([FakeProvider("primary", fail_times=9), FakeProvider("backup", reply="from backup")]).stream("q")
    assert "".join(stream) == "from backup"
    assert stream.result.fallback_used and stream.result.attempts == 3
    assert list(LLMCall.objects.order_by("id").values_list("model", "status")) == [
        ("primary", "error"), ("primary", "error"), ("backup", "ok"),
    ]


@pytestmark_db
def test_client_stream_does_not_fall_back_after_text_was_sent(ledger):
    ledger(MAX_RETRIES=2, RETRY_BACKOFF=0, STORE_PROMPTS=True)
    broken = FakeProvider("primary", reply="half an answer", fail_mid_stream=True)
    backup = FakeProvider("backup", reply="a different answer")
    stream = LLMClient([broken, backup]).stream("q")
    with pytest.raises(LLMError, match="after part of the answer"):
        for _ in stream:
            pass
    assert stream.text == "half"
    assert backup.calls == 0
    call = LLMCall.objects.get()
    assert (call.status, call.response) == ("error", "half")


@pytestmark_db
def test_client_stream_closed_early_is_recorded(ledger):
    stream = LLMClient([FakeProvider(reply="a b c d")]).stream("q")
    assert next(stream) == "a"
    stream.close()
    call = LLMCall.objects.get()
    assert call.status == "error" and "closed by the caller" in call.error
    assert stream.result is None


@pytestmark_db
def test_client_stream_uses_and_fills_the_cache():
    provider = FakeProvider(reply="cached words")
    client = LLMClient([provider])
    assert "".join(client.stream("same")) == "cached words"
    second = client.stream("same")
    assert list(second) == ["cached words"]
    assert second.result.cached and provider.calls == 1
    # complete() shares the cache with stream()
    assert client.complete("same").cached


@pytestmark_db
def test_client_stream_all_failing_raises(ledger):
    ledger(MAX_RETRIES=0)
    with pytest.raises(LLMError, match="All LLM providers failed"):
        list(LLMClient([FakeProvider(fail_times=9)]).stream("q"))


@pytestmark_db
def test_first_chunk_time_counts_failed_attempts(ledger):
    """The reader waited through the failures too, so they belong in the time to first words."""
    import time

    class SlowFailure(FakeProvider):
        def complete(self, *args, **kwargs):
            time.sleep(0.05)
            return super().complete(*args, **kwargs)

    ledger(MAX_RETRIES=1, RETRY_BACKOFF=0)
    stream = LLMClient([SlowFailure("primary", fail_times=9), FakeProvider("backup")]).stream("q")
    list(stream)
    assert stream.result.first_chunk_ms >= 100
    assert stream.result.latency_ms < 50  # latency stays the time of the attempt that answered
