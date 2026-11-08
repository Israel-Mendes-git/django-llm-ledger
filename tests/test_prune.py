from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from llm_ledger.management.commands import llm_ledger_prune
from llm_ledger.models import LLMCall

pytestmark = pytest.mark.django_db


def make(days_ago, text="secret question"):
    return LLMCall.objects.create(
        provider="x", model="x", status="ok", prompt_hash="h", cost_usd=Decimal("0.01"),
        prompt=text, response="answer", created_at=timezone.now() - timedelta(days=days_ago),
    )


def test_texts_only_keeps_rows_and_costs():
    old, recent = make(40), make(5)
    call_command("llm_ledger_prune", "--texts-older-than", "30")
    old.refresh_from_db()
    recent.refresh_from_db()
    assert (old.prompt, old.response, old.cost_usd) == ("", "", Decimal("0.01"))
    assert recent.prompt == "secret question"


def test_days_deletes_old_rows():
    make(400)
    keep = make(10)
    call_command("llm_ledger_prune", "--days", "365")
    assert list(LLMCall.objects.all()) == [keep]


def test_dry_run_changes_nothing(capsys):
    make(400)
    call_command("llm_ledger_prune", "--days", "365", "--texts-older-than", "30", "--dry-run")
    assert LLMCall.objects.get().prompt == "secret question"
    out = capsys.readouterr().out
    assert "Would delete 1 call(s)" in out and "Would blank the text of 1 call(s)" in out


def test_defaults_come_from_settings(ledger):
    make(100)
    ledger(TEXT_RETENTION_DAYS=30, RETENTION_DAYS=90)
    call_command("llm_ledger_prune")
    assert not LLMCall.objects.exists()


def test_needs_something_to_do():
    with pytest.raises(CommandError, match="Nothing to do"):
        call_command("llm_ledger_prune")
    with pytest.raises(CommandError, match="1 or more"):
        call_command("llm_ledger_prune", "--days", "0")


def test_works_in_batches(monkeypatch):
    monkeypatch.setattr(llm_ledger_prune, "BATCH", 3)
    for _ in range(7):
        make(400)
    call_command("llm_ledger_prune", "--days", "365")
    assert not LLMCall.objects.exists()
