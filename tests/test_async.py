from decimal import Decimal

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import connections

from llm_ledger import AsyncLLMStream, BudgetExceeded, LLMClient, LLMError
from llm_ledger.models import LLMCall
from llm_ledger.providers import FakeProvider

pytestmark = pytest.mark.django_db


def run(coroutine_function):
    """Run an async test body the way Django runs async views (sync work back on this thread)."""
    return async_to_sync(coroutine_function)()


def test_acomplete_records_and_falls_back(ledger):
    ledger(MAX_RETRIES=0)
    client = LLMClient([FakeProvider("primary", fail_times=9), FakeProvider("backup", reply="async ok")])

    async def body():
        return await client.acomplete("q", feature="f")

    result = run(body)
    assert result.text == "async ok" and result.fallback_used
    assert list(LLMCall.objects.order_by("id").values_list("model", "status", "feature")) == [
        ("primary", "error", "f"), ("backup", "ok", "f"),
    ]


def test_astream_yields_chunks_and_result():
    client = LLMClient([FakeProvider(reply="one two three")])

    async def body():
        stream = await client.astream("q")
        assert isinstance(stream, AsyncLLMStream) and stream.request_id
        return [chunk async for chunk in stream], stream

    chunks, stream = run(body)
    assert chunks == ["one", " two", " three"]
    assert stream.text == "one two three" and stream.result.request_id == stream.request_id
    assert LLMCall.objects.get().status == "ok"


def test_astream_closed_early_is_recorded():
    client = LLMClient([FakeProvider(reply="a b c d")])

    async def body():
        async with await client.astream("q") as stream:
            async for chunk in stream:
                break

    run(body)
    call = LLMCall.objects.get()
    assert call.status == "error" and "closed by the caller" in call.error


def test_astream_mid_stream_failure_raises(ledger):
    ledger(MAX_RETRIES=2, RETRY_BACKOFF=0)
    client = LLMClient([FakeProvider(reply="half answer", fail_mid_stream=True), FakeProvider("backup")])

    async def body():
        stream = await client.astream("q")
        async for _ in stream:
            pass

    with pytest.raises(LLMError, match="after part of the answer"):
        run(body)


def test_budget_is_checked_when_awaiting_astream(ledger):
    LLMCall.objects.create(provider="x", model="x", status="ok", cost_usd=Decimal("3"), prompt_hash="h",
                           feature="chat")
    ledger(FEATURE_BUDGETS_USD={"chat": 2}, BLOCK_OVER_BUDGET=True)

    async def body():
        await LLMClient([FakeProvider()]).astream("q", feature="chat")

    with pytest.raises(BudgetExceeded):
        run(body)


def test_async_views_end_to_end(async_client, ledger):
    ledger(PROVIDERS=[{"name": "fake", "model": "fake", "reply": "hello from an async view"}])

    async def body():
        answer = await async_client.get("/async/answer/", {"q": "hi"})
        streamed = await async_client.get("/async/stream/", {"q": "hi again"})
        text = b"".join([chunk async for chunk in streamed.streaming_content]).decode()
        return answer.json(), text

    data, text = run(body)
    assert data["text"] == "hello from an async view"
    assert text == "hello from an async view"
    assert set(LLMCall.objects.values_list("feature", flat=True)) == {"async_view", "async_stream"}


class SlowProvider(FakeProvider):
    def complete(self, *args, **kwargs):
        import time

        time.sleep(0.3)
        return super().complete(*args, **kwargs)


def test_event_loop_stays_free_while_waiting():
    """Other coroutines keep running while a provider takes its time."""
    import asyncio

    client = LLMClient([SlowProvider(reply="slow")])
    ticks = []

    async def ticker():
        for _ in range(100):
            ticks.append(1)
            await asyncio.sleep(0.005)

    async def body():
        await asyncio.gather(client.acomplete("q", use_cache=False), ticker())

    run(body)
    assert len(ticks) == 100  # the ticker was not frozen behind the 0.3 s call


class RecordingSlowProvider(FakeProvider):
    """Sleeps 0.3 s and records when each call waited, to check that waits overlap."""

    windows = []

    def complete(self, *args, **kwargs):
        import time

        began = time.perf_counter()
        time.sleep(0.3)
        RecordingSlowProvider.windows.append((began, time.perf_counter()))
        return super().complete(*args, **kwargs)


@pytest.mark.django_db(transaction=True)
def test_concurrent_requests_do_not_queue_behind_each_other():
    """Like two ASGI requests (each in its own context): both providers wait at the same time.

    Checked by overlapping waits, not total time, so a slow disk (the ledger writes) cannot make it
    flaky. Run with asyncio.run, as an ASGI server does: under async_to_sync every thread-sensitive
    call would go back to the test's own thread and queue up, which is not what happens in production.
    """
    import asyncio

    from asgiref.sync import ThreadSensitiveContext

    RecordingSlowProvider.windows = []
    client = LLMClient([RecordingSlowProvider(reply="slow")])

    async def request(n):
        async with ThreadSensitiveContext():
            try:
                return await client.acomplete(f"q{n}", use_cache=False)
            finally:
                # Django closes these at the end of each request; do the same so the
                # test database can be dropped afterwards.
                await sync_to_async(connections.close_all)()

    async def body():
        await asyncio.gather(request(1), request(2))

    asyncio.run(body())
    (start_a, end_a), (start_b, end_b) = sorted(RecordingSlowProvider.windows)
    assert start_b < end_a, "the second call only started waiting after the first one finished"
    assert LLMCall.objects.filter(status="ok").count() == 2
