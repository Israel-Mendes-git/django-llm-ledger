"""Aggregates for the admin dashboard."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from . import conf
from .models import EvalRun, LLMCall

ZERO = Decimal(0)


def _local(amount):
    local = conf.get("LOCAL_CURRENCY")
    if not local or amount is None:
        return None
    return (Decimal(str(amount)) * Decimal(str(local["rate"]))).quantize(Decimal("0.01"))


def _grouped(queryset, field, limit=10):
    rows = (
        queryset.values(field)
        .annotate(calls=Count("id"), cost=Sum("cost_usd"), tokens=Sum("input_tokens") + Sum("output_tokens"))
        .order_by("-cost", "-calls")[:limit]
    )
    return [{"key": r[field] or "—", "calls": r["calls"], "cost": r["cost"] or ZERO, "tokens": r["tokens"] or 0}
            for r in rows]


def dashboard(days=30):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since = now - timedelta(days=days)

    month = LLMCall.objects.filter(created_at__gte=month_start)
    recent = LLMCall.objects.filter(created_at__gte=since)

    spent = month.aggregate(t=Sum("cost_usd"))["t"] or ZERO
    budget = conf.get("MONTHLY_BUDGET_USD")
    budget = Decimal(str(budget)) if budget is not None else None

    totals = recent.aggregate(
        calls=Count("id"),
        errors=Count("id", filter=Q(status=LLMCall.Status.ERROR)),
        cached=Count("id", filter=Q(status=LLMCall.Status.CACHED)),
        fallbacks=Count("id", filter=Q(status=LLMCall.Status.OK, is_fallback=True)),
        unpriced=Count("id", filter=Q(status=LLMCall.Status.OK, cost_usd__isnull=True)),
        latency=Avg("latency_ms", filter=Q(status=LLMCall.Status.OK)),
    )

    per_day = {
        r["day"]: r["cost"] or ZERO
        for r in recent.annotate(day=TruncDate("created_at")).values("day").annotate(cost=Sum("cost_usd"))
    }
    days_list = []
    peak = max(per_day.values(), default=ZERO) or Decimal(1)
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        cost = per_day.get(day, ZERO)
        days_list.append({"day": day, "cost": cost, "pct": float(cost / peak * 100)})

    local = conf.get("LOCAL_CURRENCY")
    return {
        "days": days,
        "spent": spent,
        "spent_local": _local(spent),
        "local_code": local["code"] if local else None,
        "budget": budget,
        "budget_pct": float(min(spent / budget * 100, 100)) if budget else None,
        "over_budget": bool(budget is not None and spent >= budget),
        "totals": totals,
        "error_rate": (totals["errors"] / totals["calls"] * 100) if totals["calls"] else 0,
        "per_day": days_list,
        "by_feature": _grouped(recent, "feature"),
        "by_model": _grouped(recent, "model"),
        "by_user": _grouped(recent.exclude(user=None), "user__" + _username_field()),
        "last_evals": EvalRun.objects.all()[:5],
    }


def _username_field():
    from django.contrib.auth import get_user_model

    return get_user_model().USERNAME_FIELD
