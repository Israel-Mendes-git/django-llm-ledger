import json
import time

from django.db.models import Count, Q, Sum
from django.http import JsonResponse, StreamingHttpResponse
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


def _meta(result):
    attempts = list(
        LLMCall.objects.filter(request_id=result.request_id)
        .order_by("id")
        .values("provider", "model", "status", "attempt", "latency_ms", "error")
    )
    return {
        "provider": result.provider,
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": None if result.cost_usd is None else float(result.cost_usd),
        "latency_ms": result.latency_ms,
        "first_chunk_ms": result.first_chunk_ms,
        "cached": result.cached,
        "fallback_used": result.fallback_used,
        "attempts": attempts,
    }


def _event(**data):
    return json.dumps(data) + "\n"


@require_POST
def ask(request):
    """Answer a question, streaming newline-delimited JSON: many "chunk" events, then "done"."""
    try:
        body = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    question = (body.get("question") or "").strip()[:500]
    if not question:
        return JsonResponse({"error": "empty question"}, status=400)

    try:
        stream, sections = ai.answer_stream(
            question, user=request.user, simulate_outage=bool(body.get("simulate_outage")),
            history=body.get("history"))
    except LLMError as exc:  # over budget
        return JsonResponse({"error": str(exc)}, status=503)

    # The session is saved before the body streams, so remember the call now.
    ids = request.session.get("request_ids", [])
    request.session["request_ids"] = (ids + [str(stream.request_id)])[-200:]

    started = time.perf_counter()

    def events():
        with stream:  # closes the stream (and records it) if the browser goes away
            try:
                for chunk in stream:
                    yield _event(type="chunk", text=chunk)
            except LLMError as exc:
                yield _event(type="error", error="The assistant stopped halfway. Please try again.",
                             details=exc.errors, totals=_session_totals(request))
                return
        result = stream.result
        meta = _meta(result)
        meta["total_ms"] = int((time.perf_counter() - started) * 1000)  # what the reader waited
        yield _event(
            type="done",
            sources=[] if result.provider == "fake" else [s.label for s in sections],
            meta=meta,
            totals=_session_totals(request),
        )

    response = StreamingHttpResponse(events(), content_type="application/x-ndjson")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # tell nginx not to hold the chunks back
    return response


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
