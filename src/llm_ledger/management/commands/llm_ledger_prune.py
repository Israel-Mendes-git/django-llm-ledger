from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from llm_ledger import conf
from llm_ledger.models import LLMCall

BATCH = 2000


class Command(BaseCommand):
    help = (
        "Delete old ledger rows, or only the prompt/response text of old rows (costs are kept). "
        "Without options it uses RETENTION_DAYS and TEXT_RETENTION_DAYS from LLM_LEDGER, so it can "
        "run from a daily cron job."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Delete calls older than this many days.")
        parser.add_argument("--texts-older-than", type=int, dest="text_days", metavar="DAYS",
                            help="Blank the prompt and response of calls older than this many days.")
        parser.add_argument("--dry-run", action="store_true", help="Only count what would change.")

    def handle(self, *args, days=None, text_days=None, dry_run=False, **options):
        days = days if days is not None else conf.get("RETENTION_DAYS")
        text_days = text_days if text_days is not None else conf.get("TEXT_RETENTION_DAYS")
        if days is None and text_days is None:
            raise CommandError("Nothing to do: pass --days and/or --texts-older-than, "
                               "or set RETENTION_DAYS / TEXT_RETENTION_DAYS in LLM_LEDGER.")
        for value in (days, text_days):
            if value is not None and value < 1:
                raise CommandError("Days must be 1 or more.")

        now = timezone.now()
        verb = "Would" if dry_run else "Did"
        if text_days is not None:
            old = LLMCall.objects.filter(created_at__lt=now - timedelta(days=text_days)).exclude(prompt="", response="")
            count = old.count() if dry_run else self._in_batches(old, blank=True)
            self.stdout.write(f"{verb} blank the text of {count} call(s) older than {text_days} days.")
        if days is not None:
            old = LLMCall.objects.filter(created_at__lt=now - timedelta(days=days))
            count = old.count() if dry_run else self._in_batches(old, blank=False)
            self.stdout.write(f"{verb} delete {count} call(s) older than {days} days.")

    @staticmethod
    def _in_batches(queryset, *, blank):
        """Work in slices of ids so a large table is not locked or loaded at once."""
        total = 0
        while True:
            ids = list(queryset.order_by("id").values_list("id", flat=True)[:BATCH])
            if not ids:
                return total
            batch = LLMCall.objects.filter(id__in=ids)
            if blank:
                batch.update(prompt="", response="")
            else:
                batch.delete()
            total += len(ids)
