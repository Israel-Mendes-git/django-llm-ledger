from decimal import Decimal

import pytest
from django.contrib.auth.models import User

from llm_ledger import stats
from llm_ledger.models import LLMCall

pytestmark = pytest.mark.django_db


def make(**kw):
    defaults = {"provider": "openai", "model": "m1", "status": "ok", "prompt_hash": "h", "feature": "chat"}
    return LLMCall.objects.create(**{**defaults, **kw})


def test_dashboard_numbers(ledger):
    ledger(MONTHLY_BUDGET_USD=1, LOCAL_CURRENCY={"code": "BRL", "rate": 5})
    make(cost_usd=Decimal("0.5"), latency_ms=100)
    make(cost_usd=Decimal("0.7"), feature="summary", model="m2", latency_ms=300, is_fallback=True)
    make(status="error")
    make(status="cached", cost_usd=Decimal(0))
    make(cost_usd=None)

    data = stats.dashboard()
    assert data["spent"] == Decimal("1.2")
    assert data["spent_local"] == Decimal("6.00")
    assert data["over_budget"] and data["budget_pct"] == 100
    assert data["totals"]["calls"] == 5
    assert data["totals"]["errors"] == 1
    assert data["totals"]["fallbacks"] == 1
    assert data["totals"]["unpriced"] == 1
    assert data["error_rate"] == 20
    assert [row["key"] for row in data["by_feature"]] == ["summary", "chat"]  # 0.7 vs 0.5
    assert data["by_feature"][1]["calls"] == 4
    assert len(data["per_day"]) == 30


def test_dashboard_page_renders(client):
    admin = User.objects.create_superuser("admin", "a@example.com", "pw")
    client.force_login(admin)
    make(cost_usd=Decimal("0.25"))
    response = client.get("/admin/llm_ledger/llmcall/dashboard/")
    assert response.status_code == 200
    assert b"Spent this month" in response.content
    changelist = client.get("/admin/llm_ledger/llmcall/")
    assert b"Cost dashboard" in changelist.content


def test_dashboard_requires_staff(client):
    response = client.get("/admin/llm_ledger/llmcall/dashboard/")
    assert response.status_code == 302


def test_dashboard_shows_feature_budgets(admin_client, ledger):
    from decimal import Decimal

    from llm_ledger.models import LLMCall

    LLMCall.objects.create(provider="x", model="x", status="ok", cost_usd=Decimal("15"), prompt_hash="h",
                           feature="support_chat")
    ledger(FEATURE_BUDGETS_USD={"support_chat": 20, "summaries": 5})
    rows = {r["feature"]: r for r in stats.dashboard()["feature_budgets"]}
    assert rows["support_chat"]["pct"] == 75 and not rows["support_chat"]["near"]
    ledger(FEATURE_BUDGETS_USD={"support_chat": 18, "summaries": 5})
    rows = {r["feature"]: r for r in stats.dashboard()["feature_budgets"]}
    assert rows["support_chat"]["near"] and not rows["summaries"]["near"]  # 15 of 18 = 83%
    page = admin_client.get("/admin/llm_ledger/llmcall/dashboard/").content.decode()
    assert "Budgets per feature" in page
    assert "support_chat" in page and "15.00" in page and "18.00" in page
    assert "ll-bar near" in page  # amber bar from 80% on
    assert "summaries" in page  # listed even with no spend yet
