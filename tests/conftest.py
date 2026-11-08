import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def ledger(settings):
    """Shortcut to tweak LLM_LEDGER for one test: ledger(MAX_RETRIES=0)."""

    def apply(**values):
        settings.LLM_LEDGER = {**settings.LLM_LEDGER, **values}

    return apply
