import json

import httpx
import pytest

from llm_ledger import LLMClient
from llm_ledger.models import LLMCall
from llm_ledger.providers import AnthropicProvider, FakeProvider

pytestmark = pytest.mark.django_db

CONVERSATION = [
    {"role": "user", "content": "How much is shipping?"},
    {"role": "assistant", "content": "R$ 19.90, free above R$ 150."},
    {"role": "user", "content": "And to Portugal?"},
]


def test_conversation_reaches_the_provider_whole():
    seen = []
    provider = FakeProvider(reply=lambda last: "EUR 15")
    original = provider.complete

    def spy(messages, **kwargs):
        seen.append(messages)
        return original(messages, **kwargs)

    provider.complete = spy
    result = LLMClient([provider]).complete(CONVERSATION, system="be brief")
    assert result.text == "EUR 15"
    assert seen[0] == CONVERSATION


def test_conversation_is_stored_as_a_readable_transcript(ledger):
    ledger(STORE_PROMPTS=True)
    LLMClient([FakeProvider()]).complete(CONVERSATION)
    prompt = LLMCall.objects.get().prompt
    assert prompt.startswith("user: How much is shipping?") and prompt.endswith("user: And to Portugal?")


def test_single_string_is_unchanged(ledger):
    ledger(STORE_PROMPTS=True)
    LLMClient([FakeProvider()]).complete("plain question")
    assert LLMCall.objects.get().prompt == "plain question"


def test_cache_tells_conversations_apart():
    provider = FakeProvider(reply=["first", "second"])
    client = LLMClient([provider])
    client.complete(CONVERSATION)
    other = CONVERSATION[:2] + [{"role": "user", "content": "And to Spain?"}]
    assert client.complete(other).text == "second" and provider.calls == 2
    assert client.complete(CONVERSATION).cached


def test_streams_take_conversations_too():
    stream = LLMClient([FakeProvider(reply="a b")]).stream(CONVERSATION)
    assert "".join(stream) == "a b"


@pytest.mark.parametrize("bad, message", [
    ([], "non-empty"),
    ([{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}], "system="),
    ([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "last message"),
    ([{"role": "bot", "content": "hi"}], "role"),
    ([{"role": "user", "content": 42}], "text content"),
])
def test_bad_conversations_are_rejected_before_any_call(bad, message):
    provider = FakeProvider()
    with pytest.raises(ValueError, match=message):
        LLMClient([provider]).complete(bad)
    assert provider.calls == 0 and not LLMCall.objects.exists()


def test_anthropic_receives_alternating_turns_and_separate_system():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}})

    provider = AnthropicProvider("claude", api_key="k", transport=httpx.MockTransport(handler))
    LLMClient([provider]).complete(CONVERSATION, system="be brief")
    assert seen[0]["messages"] == CONVERSATION and seen[0]["system"] == "be brief"
