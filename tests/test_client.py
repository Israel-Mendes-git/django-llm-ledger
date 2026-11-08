from decimal import Decimal

import pytest
from django.contrib.auth.models import AnonymousUser, User

from llm_ledger import BudgetExceeded, LLMClient, LLMError, llm
from llm_ledger.models import LLMCall
from llm_ledger.providers import FakeProvider
from llm_ledger.signals import budget_exceeded

pytestmark = pytest.mark.django_db


def test_records_a_successful_call():
    result = llm.complete("hello there", feature="greeting")
    assert result.text == "ok"
    assert not result.cached and not result.fallback_used
    call = LLMCall.objects.get()
    assert call.status == "ok"
    assert call.feature == "greeting"
    assert call.provider == "fake"
    assert call.cost_usd == Decimal(0)  # local providers are free
    assert call.request_id == result.request_id


def test_prompts_are_not_stored_by_default(ledger):
    llm.complete("my CPF is 123")
    assert LLMCall.objects.get().prompt == ""
    ledger(STORE_PROMPTS=True)
    llm.complete("another prompt")
    assert LLMCall.objects.filter(prompt="another prompt").exists()


def test_paid_model_cost_uses_price_table(ledger):
    paid = FakeProvider("paid-model", reply="x" * 400)
    paid.is_local = False
    ledger(PRICES={"paid-model": {"input": 3, "output": 15}})
    result = LLMClient([paid]).complete("y" * 4000)
    # 1000 input tokens * 3/M + 100 output tokens * 15/M
    assert result.cost_usd == Decimal("0.004500")


def test_paid_model_without_price_has_no_cost():
    paid = FakeProvider("mystery")
    paid.is_local = False
    result = LLMClient([paid]).complete("hi")
    assert result.cost_usd is None
    assert LLMCall.objects.get().cost_usd is None


def test_retries_same_provider_on_retryable_error(ledger):
    ledger(MAX_RETRIES=2)
    flaky = FakeProvider(fail_times=2)
    result = LLMClient([flaky]).complete("hi")
    assert result.text == "ok"
    assert result.attempts == 3
    assert list(LLMCall.objects.order_by("id").values_list("status", "attempt")) == [
        ("error", 1), ("error", 2), ("ok", 3),
    ]


def test_falls_back_to_next_provider(ledger):
    ledger(MAX_RETRIES=1)
    broken = FakeProvider("primary", fail_times=99)
    backup = FakeProvider("backup", reply="from backup")
    result = LLMClient([broken, backup]).complete("hi")
    assert result.text == "from backup"
    assert result.fallback_used
    assert result.model == "backup"
    assert LLMCall.objects.filter(status="ok", is_fallback=True).count() == 1
    assert LLMCall.objects.filter(status="error", model="primary").count() == 2


def test_non_retryable_error_skips_straight_to_fallback(ledger):
    ledger(MAX_RETRIES=5)
    bad_request = FakeProvider("primary", fail_times=99, retryable=False)
    backup = FakeProvider("backup")
    LLMClient([bad_request, backup]).complete("hi")
    assert bad_request.calls == 1


def test_backoff_is_exponential(ledger):
    ledger(MAX_RETRIES=3, RETRY_BACKOFF=0.5)
    waits = []
    LLMClient([FakeProvider(fail_times=3)], sleep=waits.append).complete("hi")
    assert waits == [0.5, 1.0, 2.0]


def test_all_providers_failing_raises(ledger):
    ledger(MAX_RETRIES=0)
    with pytest.raises(LLMError) as info:
        LLMClient([FakeProvider("a", fail_times=9), FakeProvider("b", fail_times=9)]).complete("hi")
    assert len(info.value.errors) == 2


def test_cache_serves_identical_requests():
    provider = FakeProvider()
    client = LLMClient([provider])
    client.complete("same question")
    second = client.complete("same question")
    assert second.cached
    assert provider.calls == 1
    assert LLMCall.objects.filter(status="cached").count() == 1


def test_cache_can_be_skipped_and_disabled(ledger):
    provider = FakeProvider()
    client = LLMClient([provider])
    client.complete("q")
    client.complete("q", use_cache=False)
    assert provider.calls == 2
    ledger(CACHE_TTL=0)
    client.complete("q")
    assert provider.calls == 3


def test_cache_key_depends_on_system_prompt():
    provider = FakeProvider()
    client = LLMClient([provider])
    client.complete("q", system="A")
    client.complete("q", system="B")
    assert provider.calls == 2


def test_authenticated_user_is_recorded_anonymous_is_not():
    user = User.objects.create(username="ana")
    llm.complete("a", user=user)
    llm.complete("b", user=AnonymousUser())
    assert LLMCall.objects.filter(user=user).count() == 1
    assert LLMCall.objects.filter(user=None).count() == 1


def test_budget_warns_and_optionally_blocks(ledger):
    LLMCall.objects.create(provider="x", model="x", status="ok", cost_usd=Decimal("10"), prompt_hash="h")
    received = []

    def handler(sender, **kwargs):
        received.append(kwargs)

    budget_exceeded.connect(handler)
    try:
        ledger(MONTHLY_BUDGET_USD=5)
        llm.complete("still allowed")
        assert received and received[0]["spent"] == Decimal("10")

        ledger(BLOCK_OVER_BUDGET=True)
        with pytest.raises(BudgetExceeded):
            llm.complete("blocked")
    finally:
        budget_exceeded.disconnect(handler)


def test_settings_chain_is_read_per_call(ledger):
    ledger(PROVIDERS=[{"name": "fake", "model": "first", "reply": "1"}])
    assert llm.complete("x").text == "1"
    ledger(PROVIDERS=[{"name": "fake", "model": "second", "reply": "2"}])
    assert llm.complete("x").text == "2"


def test_empty_chain_is_an_error(ledger):
    ledger(PROVIDERS=[])
    with pytest.raises(LLMError):
        llm.complete("x")
