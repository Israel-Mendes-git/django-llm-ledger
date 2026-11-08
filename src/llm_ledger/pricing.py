"""Cost calculation.

The package ships no price table for paid models on purpose: prices change often and a
stale table silently reports wrong numbers. Configure ``LLM_LEDGER["PRICES"]`` or pass
``price=`` in the provider entry. Calls to a paid model without a price get cost=None and
show up as "unpriced" in the dashboard.
"""

from decimal import Decimal

from . import conf

PER_MILLION = Decimal(1_000_000)


def price_for(provider):
    if provider.is_local:
        return {"input": 0, "output": 0}
    if provider.price is not None:
        return provider.price
    return (conf.get("PRICES") or {}).get(provider.model)


def cost_usd(provider, input_tokens, output_tokens):
    price = price_for(provider)
    if price is None:
        return None
    total = Decimal(str(price["input"])) * input_tokens + Decimal(str(price["output"])) * output_tokens
    return (total / PER_MILLION).quantize(Decimal("0.000001"))
