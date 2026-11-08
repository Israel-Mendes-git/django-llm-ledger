import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import caches
from django.db.models import Sum
from django.utils import timezone

from . import conf
from .pricing import cost_usd
from .providers import ProviderError, build_provider
from .signals import budget_exceeded

logger = logging.getLogger("llm_ledger")


class LLMError(Exception):
    """Every provider in the chain failed."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


class BudgetExceeded(LLMError):
    pass


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

    def __str__(self):
        return self.text


def month_spend():
    from .models import LLMCall

    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = LLMCall.objects.filter(created_at__gte=start).aggregate(total=Sum("cost_usd"))["total"]
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
        chain = self.providers()
        if not chain:
            raise LLMError("No providers configured (LLM_LEDGER['PROVIDERS'] is empty).")

        request_id = uuid.uuid4()
        messages = [{"role": "user", "content": prompt}]
        prompt_hash = self._hash(chain, system, prompt, max_tokens, temperature)
        user = user if getattr(user, "is_authenticated", False) else None
        common = {"request_id": request_id, "feature": feature, "user": user, "prompt_hash": prompt_hash}

        self._check_budget()

        ttl = conf.get("CACHE_TTL")
        cache = caches[conf.get("CACHE_ALIAS")] if use_cache and ttl else None
        if cache is not None:
            hit = cache.get(self._cache_key(prompt_hash))
            if hit is not None:
                self._record(common, provider=hit["provider"], model=hit["model"], status="cached",
                             prompt=prompt, response=hit["text"], cost=Decimal(0))
                return LLMResult(hit["text"], hit["provider"], hit["model"], 0, 0, Decimal(0), 0,
                                 cached=True, fallback_used=False, request_id=request_id, attempts=0)

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
                    cache.set(self._cache_key(prompt_hash),
                              {"text": completion.text, "provider": provider.name, "model": provider.model}, ttl)
                return LLMResult(
                    completion.text, provider.name, provider.model, completion.input_tokens,
                    completion.output_tokens, cost, latency, cached=False, fallback_used=position > 0,
                    request_id=request_id, attempts=attempts, errors=errors,
                )

        raise LLMError("All LLM providers failed:\n" + "\n".join(errors), errors)

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _hash(chain, system, prompt, max_tokens, temperature):
        key = json.dumps(
            [[p.label for p in chain], system, prompt, max_tokens, temperature], ensure_ascii=False
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_key(prompt_hash):
        return f"llm_ledger:{prompt_hash}"

    def _check_budget(self):
        budget = conf.get("MONTHLY_BUDGET_USD")
        if budget is None:
            return
        budget = Decimal(str(budget))
        spent = month_spend()
        if spent < budget:
            return
        logger.warning("LLM monthly budget reached: spent %s of %s USD", spent, budget)
        budget_exceeded.send(sender=type(self), spent=spent, budget=budget)
        if conf.get("BLOCK_OVER_BUDGET"):
            raise BudgetExceeded(f"Monthly LLM budget of {budget} USD reached (spent {spent}).")

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
