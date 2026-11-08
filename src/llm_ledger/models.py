import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class LLMCall(models.Model):
    """One attempt against one provider. A single ``llm.complete()`` may create several rows
    (retries and fallbacks), all sharing the same ``request_id``."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        CACHED = "cached", "Cached"

    request_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    feature = models.CharField(max_length=100, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=Status.choices)
    attempt = models.PositiveSmallIntegerField(default=1)
    is_fallback = models.BooleanField(default=False, help_text="Served by a provider other than the first one.")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    prompt_hash = models.CharField(max_length=64, db_index=True)
    prompt = models.TextField(blank=True)
    response = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "LLM call"

    def __str__(self):
        return f"{self.feature or '-'} {self.provider}:{self.model} {self.status}"


class EvalRun(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    suite = models.CharField(max_length=200)
    total = models.PositiveIntegerField()
    passed = models.PositiveIntegerField()
    score = models.FloatField()
    details = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "evaluation run"

    def __str__(self):
        return f"{self.suite}: {self.passed}/{self.total}"
