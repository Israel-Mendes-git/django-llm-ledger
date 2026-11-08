"""Fill the ledger with 30 days of synthetic history so the dashboard has something to show."""

import random
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from llm_ledger.models import LLMCall
from llm_ledger.pricing import PER_MILLION

# (feature, model, provider, calls per day, avg input tokens, avg output tokens)
TRAFFIC = [
    ("support_chat", "claude-haiku-5-5", "anthropic", 60, 900, 120),
    ("support_chat", "qwen2.5:3b", "ollama", 8, 900, 140),  # fallbacks on bad days
    ("order_summary", "gpt-4.1-mini", "openai", 35, 600, 90),
    ("product_descriptions", "claude-sonnet-5-5", "anthropic", 6, 1500, 600),
]
PRICES = {
    "claude-haiku-5-5": (Decimal("1.0"), Decimal("5.0")),
    "claude-sonnet-5-5": (Decimal("3.0"), Decimal("15.0")),
    "gpt-4.1-mini": (Decimal("0.4"), Decimal("1.6")),
    "qwen2.5:3b": (Decimal(0), Decimal(0)),
}


class Command(BaseCommand):
    help = "Create synthetic LLM call history for the demo dashboard (deletes existing calls)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, *args, days, seed, **options):
        rng = random.Random(seed)
        LLMCall.objects.all().delete()
        User = get_user_model()
        users = [User.objects.get_or_create(username=name)[0] for name in ("ana", "bruno", "carla", "davi")]
        now = timezone.now()
        rows = []
        for offset in range(days):
            day = now - timedelta(days=offset)
            outage_day = offset in (4, 17)
            for feature, model, provider, per_day, avg_in, avg_out in TRAFFIC:
                volume = int(per_day * rng.uniform(0.6, 1.4) * (1.3 if day.weekday() < 5 else 0.7))
                if model == "qwen2.5:3b" and not outage_day:
                    volume //= 4
                for _ in range(volume):
                    created = day.replace(hour=rng.randint(8, 21), minute=rng.randint(0, 59))
                    if created > now:
                        created = now - timedelta(minutes=rng.randint(1, 600))
                    tin = int(avg_in * rng.uniform(0.5, 1.6))
                    tout = int(avg_out * rng.uniform(0.4, 1.8))
                    pin, pout = PRICES[model]
                    status = "ok"
                    if rng.random() < (0.25 if outage_day and provider != "ollama" else 0.015):
                        status = "error"
                    elif feature == "support_chat" and rng.random() < 0.12:
                        status = "cached"
                    cost = (pin * tin + pout * tout) / PER_MILLION if status == "ok" else Decimal(0)
                    rows.append(LLMCall(
                        request_id=uuid.uuid4(), created_at=created, feature=feature,
                        user=rng.choice(users) if feature != "product_descriptions" else users[0],
                        provider=provider, model=model, status=status,
                        is_fallback=provider == "ollama" and status == "ok",
                        input_tokens=tin if status == "ok" else 0, output_tokens=tout if status == "ok" else 0,
                        cost_usd=cost.quantize(Decimal("0.000001")),
                        latency_ms=int(rng.uniform(400, 2500) if status != "cached" else rng.uniform(2, 8)),
                        prompt_hash=uuid.uuid4().hex, error="HTTP 529: overloaded" if status == "error" else "",
                    ))
        LLMCall.objects.bulk_create(rows, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Created {len(rows)} synthetic calls over {days} days."))
