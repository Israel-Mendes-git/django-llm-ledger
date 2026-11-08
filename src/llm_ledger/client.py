import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.core.cache import caches
from django.db.models import Sum
from django.utils import timezone

from . import conf
from .pricing import cost_usd
from .providers import Completion, ProviderError, build_provider
from .signals import budget_exceeded

logger = logging.getLogger("llm_ledger")


class LLMError(Exception):
    """Every provider in the chain failed."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


class BudgetExceeded(LLMError):
    """The monthly budget is spent. ``feature`` is set when it was that feature's own budget."""

    def __init__(self, message, *, feature=None):
        super().__init__(message)
        self.feature = feature


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    cached: bool
    fallback_used: bool
    request_id: uuid.UUID
    attempts: int = 1
    errors: list = field(default_factory=list)
    #: Streaming only: time from the call until the reader got the first text, failed attempts included.
    first_chunk_ms: int | None = None

    def __str__(self):
        return self.text


class LLMStream:
    """What ``llm.stream()`` returns: iterate it for text chunks, then read ``.result``.

        stream = llm.stream("Write a haiku", feature="haiku")
        for chunk in stream:
            send_to_browser(chunk)
        print(stream.result.cost_usd)

    Retries and fallback only happen before the first chunk: once text has reached the reader,
    switching to another model would mix two different answers, so a failure after that point
    raises ``LLMError`` (the partial text is in ``.text``). Closing the stream early (the browser
    went away) is recorded in the ledger as an error with the partial response.
    """

    def __init__(self, generator_factory, request_id):
        #: Known before the first chunk, so a view can store it (in the session, say) up front.
        self.request_id = request_id
        self.result = None
        self.chunks = []
        self._generator = generator_factory(self)

    @property
    def text(self):
        return "".join(self.chunks)

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self._generator)
        self.chunks.append(chunk)
        return chunk

    def close(self):
        self._generator.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


_END = object()


class AsyncLLMStream:
    """What ``await llm.astream()`` returns: ``async for`` over it, then read ``.result``.

    It drives an ``LLMStream`` through ``sync_to_async``, so it has the same rules: fallback
    only before the first chunk, early close recorded in the ledger.
    """

    def __init__(self, stream):
        self._stream = stream

    @property
    def request_id(self):
        return self._stream.request_id

    @property
    def result(self):
        return self._stream.result

    @property
    def text(self):
        return self._stream.text

    def _next(self):
        # StopIteration cannot cross into a coroutine, so the end is signalled with a sentinel.
        return next(self._stream, _END)

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await sync_to_async(self._next)()
        if chunk is _END:
            raise StopAsyncIteration
        return chunk

    async def aclose(self):
        await sync_to_async(self._stream.close)()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        await self.aclose()


ROLES = ("user", "assistant")


def _normalize(prompt):
    """Accept a string or a conversation; return (messages, text for the ledger).

    A conversation is a list of {"role": "user" | "assistant", "content": str} ending with the
    user's turn. The system prompt goes in ``system=``, not in the list, because providers place
    it differently.
    """
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}], prompt
    if not isinstance(prompt, (list, tuple)) or not prompt:
        raise ValueError("prompt must be a string or a non-empty list of messages")
    messages = []
    for index, message in enumerate(prompt):
        role, content = (message or {}).get("role"), (message or {}).get("content")
        if role == "system":
            raise ValueError("pass the system prompt as system=..., not as a message")
        if role not in ROLES or not isinstance(content, str):
            raise ValueError(f"message {index} needs role 'user' or 'assistant' and text content")
        messages.append({"role": role, "content": content})
    if messages[-1]["role"] != "user":
        raise ValueError("the last message must be the user's")
    if len(messages) == 1:
        return messages, messages[0]["content"]
    return messages, "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)


def month_spend(feature=None):
    """Spend since the first day of the month, for every feature or for one."""
    from .models import LLMCall

    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    calls = LLMCall.objects.filter(created_at__gte=start)
    if feature is not None:
        calls = calls.filter(feature=feature)
    total = calls.aggregate(total=Sum("cost_usd"))["total"]
    return total or Decimal(0)


class LLMClient:
    """Calls a chain of providers with retries and fallback, and records every attempt.

    Without arguments the chain comes from ``settings.LLM_LEDGER["PROVIDERS"]`` and is read on
    every call, so ``override_settings`` and runtime changes just work.
    """

    def __init__(self, providers=None, *, sleep=time.sleep):
        self._providers = [build_provider(p) for p in providers] if providers is not None else None
        self._sleep = sleep

    def providers(self):
        if self._providers is not None:
            return self._providers
        return [build_provider(p) for p in conf.get("PROVIDERS")]

    # ----------------------------------------------------------------- public API

    def complete(
        self,
        prompt,
        *,
        feature="",
        user=None,
        system=None,
        max_tokens=1024,
        temperature=0.0,
        use_cache=True,
    ):
        messages, prompt = _normalize(prompt)
        call = self._prepare(prompt, messages, feature, user, system, max_tokens, temperature, use_cache)
        if isinstance(call, LLMResult):
            return call
        chain, common, cache, ttl, messages = call
        request_id = common["request_id"]

        errors = []
        attempts = 0
        max_retries = conf.get("MAX_RETRIES")
        backoff = conf.get("RETRY_BACKOFF")

        for position, provider in enumerate(chain):
            for attempt in range(1, max_retries + 2):
                attempts += 1
                started = time.perf_counter()
                try:
                    completion = provider.complete(
                        messages, system=system, max_tokens=max_tokens, temperature=temperature
                    )
                except ProviderError as exc:
                    latency = int((time.perf_counter() - started) * 1000)
                    errors.append(f"{provider.label} (attempt {attempt}): {exc}")
                    self._record(common, provider=provider.name, model=provider.model, status="error",
                                 attempt=attempt, is_fallback=position > 0, latency=latency,
                                 prompt=prompt, error=str(exc))
                    logger.warning("LLM call failed: %s attempt %s: %s", provider.label, attempt, exc)
                    if exc.retryable and attempt <= max_retries:
                        if backoff:
                            self._sleep(backoff * 2 ** (attempt - 1))
                        continue
                    break  # next provider

                latency = int((time.perf_counter() - started) * 1000)
                cost = cost_usd(provider, completion.input_tokens, completion.output_tokens)
                self._record(common, provider=provider.name, model=provider.model, status="ok",
                             attempt=attempt, is_fallback=position > 0, latency=latency,
                             input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
                             cost=cost, prompt=prompt, response=completion.text)
                if cache is not None:
                    cache.set(self._cache_key(common["prompt_hash"]),
                              {"text": completion.text, "provider": provider.name, "model": provider.model}, ttl)
                return LLMResult(
                    completion.text, provider.name, provider.model, completion.input_tokens,
                    completion.output_tokens, cost, latency, cached=False, fallback_used=position > 0,
                    request_id=request_id, attempts=attempts, errors=errors,
                )

        raise LLMError("All LLM providers failed:\n" + "\n".join(errors), errors)

    async def acomplete(self, prompt, **kwargs):
        """``complete`` for async views: same arguments, same ledger, retries and fallback.

        The call runs through ``sync_to_async``: the ledger is written with the ORM, which is
        synchronous, and keeping one code path means the async API cannot drift from the sync one.
        The event loop stays free while the provider answers.
        """
        return await sync_to_async(self.complete)(prompt, **kwargs)

    async def astream(self, prompt, **kwargs):
        """``stream`` for async views. Budget and cache are checked when you await it.

            stream = await llm.astream(question, feature="support")
            async for chunk in stream:
                ...
        """
        return AsyncLLMStream(await sync_to_async(self.stream)(prompt, **kwargs))

    def stream(
        self,
        prompt,
        *,
        feature="",
        user=None,
        system=None,
        max_tokens=1024,
        temperature=0.0,
        use_cache=True,
    ):
        """Like ``complete`` but returns an ``LLMStream`` that yields the text as it is generated.

        Budget and cache are checked right away, so ``BudgetExceeded`` is raised here and not
        in the middle of the loop. A cache hit yields the whole answer as a single chunk.
        """
        messages, prompt = _normalize(prompt)
        call = self._prepare(prompt, messages, feature, user, system, max_tokens, temperature, use_cache)
        if isinstance(call, LLMResult):
            def replay(holder):
                holder.result = call
                if call.text:
                    yield call.text
            return LLMStream(replay, call.request_id)

        chain, common, cache, ttl, messages = call
        options = {"system": system, "max_tokens": max_tokens, "temperature": temperature}
        return LLMStream(lambda holder: self._stream(holder, prompt, chain, common, cache, ttl, messages, options),
                         common["request_id"])

    def _stream(self, holder, prompt, chain, common, cache, ttl, messages, options):
        request_started = time.perf_counter()
        errors = []
        attempts = 0
        max_retries = conf.get("MAX_RETRIES")
        backoff = conf.get("RETRY_BACKOFF")

        for position, provider in enumerate(chain):
            for attempt in range(1, max_retries + 2):
                attempts += 1
                started = time.perf_counter()
                first_chunk_ms = None
                sent = []
                completion = None
                record = {"provider": provider.name, "model": provider.model, "attempt": attempt,
                          "is_fallback": position > 0, "prompt": prompt}
                try:
                    for item in provider.stream(messages, **options):
                        if isinstance(item, Completion):
                            completion = item
                            break
                        if first_chunk_ms is None:
                            first_chunk_ms = int((time.perf_counter() - request_started) * 1000)
                        sent.append(item)
                        yield item
                except ProviderError as exc:
                    latency = int((time.perf_counter() - started) * 1000)
                    errors.append(f"{provider.label} (attempt {attempt}): {exc}")
                    self._record(common, **record, status="error", latency=latency,
                                 response="".join(sent), error=str(exc))
                    logger.warning("LLM stream failed: %s attempt %s: %s", provider.label, attempt, exc)
                    if sent:
                        raise LLMError(f"Stream broke after part of the answer was sent: {exc}", errors) from exc
                    if exc.retryable and attempt <= max_retries:
                        if backoff:
                            self._sleep(backoff * 2 ** (attempt - 1))
                        continue
                    break  # next provider
                except GeneratorExit:
                    latency = int((time.perf_counter() - started) * 1000)
                    self._record(common, **record, status="error", latency=latency,
                                 response="".join(sent), error="stream closed by the caller before the end")
                    raise

                latency = int((time.perf_counter() - started) * 1000)
                completion = completion or Completion("".join(sent))
                cost = cost_usd(provider, completion.input_tokens, completion.output_tokens)
                self._record(common, **record, status="ok", latency=latency,
                             input_tokens=completion.input_tokens, output_tokens=completion.output_tokens,
                             cost=cost, response=completion.text)
                if cache is not None:
                    cache.set(self._cache_key(common["prompt_hash"]),
                              {"text": completion.text, "provider": provider.name, "model": provider.model}, ttl)
                holder.result = LLMResult(
                    completion.text, provider.name, provider.model, completion.input_tokens,
                    completion.output_tokens, cost, latency, cached=False, fallback_used=position > 0,
                    request_id=common["request_id"], attempts=attempts, errors=errors,
                    first_chunk_ms=first_chunk_ms,
                )
                return

        raise LLMError("All LLM providers failed:\n" + "\n".join(errors), errors)

    # ----------------------------------------------------------------- helpers

    def _prepare(self, prompt, messages, feature, user, system, max_tokens, temperature, use_cache):
        """Shared start of complete/stream: chain, ledger fields, budget and cache.

        Returns an ``LLMResult`` on a cache hit, otherwise (chain, common, cache, ttl, messages).
        """
        chain = self.providers()
        if not chain:
            raise LLMError("No providers configured (LLM_LEDGER['PROVIDERS'] is empty).")

        request_id = uuid.uuid4()
        prompt_hash = self._hash(chain, system, messages, max_tokens, temperature)
        user = user if getattr(user, "is_authenticated", False) else None
        common = {"request_id": request_id, "feature": feature, "user": user, "prompt_hash": prompt_hash}

        self._check_budget(feature)

        ttl = conf.get("CACHE_TTL")
        cache = caches[conf.get("CACHE_ALIAS")] if use_cache and ttl else None
        if cache is not None:
            hit = cache.get(self._cache_key(prompt_hash))
            if hit is not None:
                self._record(common, provider=hit["provider"], model=hit["model"], status="cached",
                             prompt=prompt, response=hit["text"], cost=Decimal(0))
                return LLMResult(hit["text"], hit["provider"], hit["model"], 0, 0, Decimal(0), 0,
                                 cached=True, fallback_used=False, request_id=request_id, attempts=0)
        return chain, common, cache, ttl, messages

    @staticmethod
    def _hash(chain, system, messages, max_tokens, temperature):
        key = json.dumps(
            [[p.label for p in chain], system, messages, max_tokens, temperature], ensure_ascii=False
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_key(prompt_hash):
        return f"llm_ledger:{prompt_hash}"

    @staticmethod
    def _cached_spend(scope):
        """month_spend, reused for BUDGET_CHECK_TTL seconds so budgets do not cost a query per call."""
        ttl = conf.get("BUDGET_CHECK_TTL")
        if not ttl:
            return month_spend(scope)
        cache = caches[conf.get("CACHE_ALIAS")]
        key = f"llm_ledger:spend:{timezone.now():%Y-%m}:{scope or ''}"
        spent = cache.get(key)
        if spent is None:
            spent = month_spend(scope)
            cache.set(key, spent, ttl)
        return spent

    def _check_budget(self, feature=""):
        checks = [(None, conf.get("MONTHLY_BUDGET_USD"))]
        if feature:
            checks.append((feature, (conf.get("FEATURE_BUDGETS_USD") or {}).get(feature)))
        for scope, budget in checks:
            if budget is None:
                continue
            budget = Decimal(str(budget))
            spent = self._cached_spend(scope)
            if spent < budget:
                continue
            what = f"budget of feature {scope!r}" if scope else "LLM budget"
            logger.warning("Monthly %s reached: spent %s of %s USD", what, spent, budget)
            budget_exceeded.send(sender=type(self), spent=spent, budget=budget, feature=scope)
            if conf.get("BLOCK_OVER_BUDGET"):
                raise BudgetExceeded(f"Monthly {what} of {budget} USD reached (spent {spent}).", feature=scope)

    @staticmethod
    def _record(common, *, provider, model, status, attempt=1, is_fallback=False, latency=0,
                input_tokens=0, output_tokens=0, cost=None, prompt="", response="", error=""):
        from .models import LLMCall

        store = conf.get("STORE_PROMPTS")
        LLMCall.objects.create(
            **common,
            provider=provider,
            model=model,
            status=status,
            attempt=attempt,
            is_fallback=is_fallback,
            latency_ms=latency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            prompt=prompt if store else "",
            response=response if store else "",
            error=error[:2000],
        )


#: Default client, configured from settings.
llm = LLMClient()
