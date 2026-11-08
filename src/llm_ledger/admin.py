from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from . import stats
from .models import EvalRun, LLMCall


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LLMCall)
class LLMCallAdmin(ReadOnlyAdmin):
    change_list_template = "admin/llm_ledger/llmcall/change_list.html"
    list_display = ("created_at", "feature", "provider", "model", "status", "is_fallback",
                    "input_tokens", "output_tokens", "cost_usd", "latency_ms", "user")
    list_filter = ("status", "is_fallback", "provider", "model", "feature")
    search_fields = ("feature", "model", "request_id", "error")
    date_hierarchy = "created_at"
    list_select_related = ("user",)

    def get_urls(self):
        custom = [
            path("dashboard/", self.admin_site.admin_view(self.dashboard_view), name="llm_ledger_dashboard"),
        ]
        return custom + super().get_urls()

    def dashboard_view(self, request):
        try:
            days = max(1, min(int(request.GET.get("days", 30)), 365))
        except ValueError:
            days = 30
        context = {
            **self.admin_site.each_context(request),
            "title": "LLM costs",
            "opts": self.model._meta,
            **stats.dashboard(days),
        }
        return TemplateResponse(request, "admin/llm_ledger/dashboard.html", context)


@admin.register(EvalRun)
class EvalRunAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "suite", "passed", "total", "score_pct")
    list_filter = ("suite",)

    @admin.display(description="score")
    def score_pct(self, obj):
        return f"{obj.score:.0%}"
