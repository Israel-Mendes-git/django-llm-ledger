"""Settings for django-llm-ledger, read from ``settings.LLM_LEDGER``."""

from django.conf import settings

DEFAULTS = {
    # Ordered fallback chain. Each entry is a dict ({"name": "ollama", "model": "llama3.2", ...})
    # or a Provider instance.
    "PROVIDERS": [{"name": "ollama", "model": "llama3.2"}],
    # Prices in USD per 1 million tokens: {"model-name": {"input": 3.0, "output": 15.0}}.
    # Local providers (ollama, fake) cost 0. Paid models without a price are recorded with cost=None.
    "PRICES": {},
    # Retries per provider for retryable errors (timeouts, 429, 5xx) before moving to the next one.
    "MAX_RETRIES": 2,
    # Base delay in seconds for exponential backoff between retries.
    "RETRY_BACKOFF": 0.5,
    "TIMEOUT": 60,
    # Seconds to keep identical answers in Django's cache. 0 disables caching.
    "CACHE_TTL": 3600,
    "CACHE_ALIAS": "default",
    # Monthly budget in USD. None disables the check.
    "MONTHLY_BUDGET_USD": None,
    # When True, calls raise BudgetExceeded once the monthly budget is spent.
    "BLOCK_OVER_BUDGET": False,
    # Optional exchange rate to show costs in a second currency in the dashboard.
    "LOCAL_CURRENCY": None,  # e.g. {"code": "BRL", "rate": 5.4}
    # Store prompt and response text. Off by default: prompts often carry personal data.
    "STORE_PROMPTS": False,
}


def get(key):
    user_settings = getattr(settings, "LLM_LEDGER", {}) or {}
    if key in user_settings:
        return user_settings[key]
    return DEFAULTS[key]
