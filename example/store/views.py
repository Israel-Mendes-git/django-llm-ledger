import json

from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from llm_ledger import LLMError
from llm_ledger.models import EvalRun, LLMCall

from . import ai
from .models import HandoffRequest


def _session_totals(request):
    calls = LLMCall.objects.filter(request_id__in=request.session.get("request_ids", []))
    data = calls.aggregate(
        cost=Sum("cost_usd"),
        answers=Count("request_id", distinct=True),
        errors=Count("id", filter=Q(status="error")),
        cached=Count("id", filter=Q(status="cached")),
    )
    data["cost"] = float(data["cost"] or 0)
    return data


@ensure_csrf_cookie
def home(request):
    last_eval = EvalRun.objects.filter(suite="store-support").first()
    return render(request, "store/home.html", {"last_eval": last_eval, "totals": _session_totals(request)})


@require_POST
def ask(request):
    try:
        body = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    question = (body.get("question") or "").strip()[:500]
    if not question:
        return JsonResponse({"error": "empty question"}, status=400)

    try:
        result, sections = ai.answer(question, user=request.user, simulate_outage=bool(body.get("simulate_outage")))
    except LLMError as exc:
        return JsonResponse({"error": "All providers failed.", "details": exc.errors}, status=503)

    ids = request.session.get("request_ids", [])
    request.session["request_ids"] = (ids + [str(result.request_id)])[-200:]

    attempts = list(
        LLMCall.objects.filter(request_id=result.request_id)
        .order_by("id")
        .values("provider", "model", "status", "attempt", "latency_ms", "error")
    )
    return JsonResponse(
        {
            "answer": result.text,
            "sources": [s.label for s in sections],
            "meta": {
                "provider": result.provider,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": None if result.cost_usd is None else float(result.cost_usd),
                "latency_ms": result.latency_ms,
                "cached": result.cached,
                "fallback_used": result.fallback_used,
                "attempts": attempts,
            },
            "totals": _session_totals(request),
        }
    )


@require_POST
def handoff(request):
    try:
        body = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    email = (body.get("email") or "").strip()
    question = (body.get("question") or "").strip()
    if "@" not in email or not question:
        return JsonResponse({"error": "email and question are required"}, status=400)
    HandoffRequest.objects.create(email=email[:254], question=question[:2000])
    return JsonResponse({"ok": True, "when": timezone.now().isoformat()})
