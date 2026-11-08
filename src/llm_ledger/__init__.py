"""Cost tracking, provider fallback and evaluation suites for LLM features in Django apps.

    from llm_ledger import llm
    answer = llm.complete("Summarize this order: ...", feature="order_summary", user=request.user)
"""

__version__ = "0.1.0"

_LAZY = {"llm", "LLMClient", "LLMResult", "LLMStream", "AsyncLLMStream", "LLMError", "BudgetExceeded"}


def __getattr__(name):
    # Imported lazily so that "llm_ledger" can sit in INSTALLED_APPS without touching models early.
    if name in _LAZY:
        from . import client

        return getattr(client, name)
    raise AttributeError(f"module 'llm_ledger' has no attribute {name!r}")
