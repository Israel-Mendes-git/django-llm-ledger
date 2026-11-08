import json

import pytest
from django.core.management import CommandError, call_command

from llm_ledger.evals import SuiteError, check, load_suite
from llm_ledger.models import EvalRun

pytestmark = pytest.mark.django_db


def answer_store_question(question):
    """Target used by the suites below: a stand-in for an app's real AI feature."""
    if "return" in question.lower():
        return "You can return items within 30 days."
    return "I don't know."


def test_check_rules():
    case = {"must_contain": ["30 days"], "must_not_contain": ["sorry"], "any_of": ["return", "refund"],
            "regex": r"\d+ days"}
    assert check(case, "You can RETURN it in 30 Days.") == []
    failures = check(case, "sorry, no idea")
    assert len(failures) == 4


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def suite(**extra):
    return {
        "name": "store",
        "target": "tests.test_evals.answer_store_question",
        "cases": [
            {"id": "returns", "input": "How do returns work?", "must_contain": ["30 days"]},
            {"id": "shipping", "input": "Do you ship?", "must_not_contain": ["don't know"]},
        ],
        **extra,
    }


def test_load_suite_validates(tmp_path):
    with pytest.raises(SuiteError):
        load_suite(write(tmp_path, "empty.json", {"cases": []}))
    with pytest.raises(SuiteError):
        load_suite(write(tmp_path, "noinput.json", {"cases": [{"id": "a"}]}))
    loaded = load_suite(write(tmp_path, "ok.json", {"cases": [{"input": "x"}]}))
    assert loaded["name"] == "ok" and loaded["cases"][0]["id"] == "case-1"


def test_yaml_suites_load(tmp_path):
    path = tmp_path / "s.yaml"
    path.write_text("cases:\n  - input: hi\n    must_contain: [ok]\n", encoding="utf-8")
    assert load_suite(path)["cases"][0]["must_contain"] == ["ok"]


def test_command_fails_below_min_score_and_saves_run(tmp_path):
    path = write(tmp_path, "store.json", suite())
    with pytest.raises(CommandError):
        call_command("llm_evaluate", str(path))
    run = EvalRun.objects.get()
    assert (run.passed, run.total, run.score) == (1, 2, 0.5)
    assert run.details[1]["failures"] == ["should not contain: \"don't know\""]


def test_command_passes_with_lower_threshold(tmp_path):
    path = write(tmp_path, "store.json", suite())
    call_command("llm_evaluate", str(path), "--min-score", "0.5", "--no-save")
    assert not EvalRun.objects.exists()


def test_command_runs_every_suite_in_a_folder(tmp_path):
    write(tmp_path, "a.json", suite(name="a"))
    write(tmp_path, "b.json", suite(name="b"))
    (tmp_path / "notes.txt").write_text("ignored")
    call_command("llm_evaluate", str(tmp_path), "--min-score", "0")
    assert set(EvalRun.objects.values_list("suite", flat=True)) == {"a", "b"}


def test_raw_prompt_suite_goes_through_the_ledger(tmp_path, ledger):
    from llm_ledger.models import LLMCall

    ledger(PROVIDERS=[{"name": "fake", "model": "fake", "reply": "Paris"}])
    path = write(tmp_path, "geo.json", {"name": "geo", "cases": [{"input": "Capital of France?", "must_contain": ["paris"]}]})
    call_command("llm_evaluate", str(path))
    assert LLMCall.objects.get().feature == "eval:geo"


PAID = None


def paid_target(question):
    """A target that makes two paid calls per case, like a real feature with a rewrite step."""
    draft = PAID.complete(question).text
    return PAID.complete(draft).text


def test_report_sums_cost_tokens_and_time_per_case(tmp_path, ledger):
    from decimal import Decimal

    from llm_ledger.client import LLMClient
    from llm_ledger.providers import FakeProvider

    global PAID
    provider = FakeProvider("paid-model", reply="x" * 400)  # 100 output tokens
    provider.is_local = False
    ledger(PRICES={"paid-model": {"input": 3, "output": 15}})
    PAID = LLMClient([provider])
    path = write(tmp_path, "paid.json", {
        "name": "paid", "target": "tests.test_evals.paid_target",
        "cases": [{"input": "y" * 4000}, {"input": "z" * 4000}],
    })
    call_command("llm_evaluate", str(path), "--min-score", "0")
    run = EvalRun.objects.get()
    # Each case: 1000 in + 100 out, then 100 in + 100 out => 0.0045 + 0.0018 = 0.0063.
    # Case 2 repeats the second prompt ("x" * 400), but evals skip the cache, so it is paid again.
    assert run.details[0]["cost_usd"] == pytest.approx(0.0063)
    assert run.details[0]["tokens"] == 1300
    assert run.details[1]["cost_usd"] == pytest.approx(0.0063)
    assert run.cost_usd == Decimal("0.012600")
    assert run.tokens == 2 * 1300


def test_free_models_report_unknown_cost(tmp_path):
    call_command("llm_evaluate", str(write(tmp_path, "s.json", suite())), "--min-score", "0")
    run = EvalRun.objects.get()
    assert run.cost_usd is None and run.tokens == 0


def test_evals_skip_answers_cached_before_the_run(tmp_path, ledger):
    """A cached answer from production must not stand in for the model being evaluated."""
    from llm_ledger import llm
    from llm_ledger.models import LLMCall

    ledger(PROVIDERS=[{"name": "fake", "model": "fake", "reply": "Paris"}], CACHE_TTL=3600)
    llm.complete("Capital of France?", feature="eval:geo")  # warms the cache
    path = write(tmp_path, "geo.json", {"name": "geo", "cases": [{"input": "Capital of France?", "must_contain": ["paris"]}]})
    call_command("llm_evaluate", str(path))
    assert not LLMCall.objects.filter(status="cached").exists()


def test_models_option_runs_each_model_and_prints_a_table(tmp_path, ledger, capsys):
    ledger(PROVIDERS=[{"name": "fake", "model": "configured", "reply": "Paris"}])
    path = write(tmp_path, "geo.json", {"name": "geo", "cases": [{"input": "Capital of France?", "must_contain": ["paris"]}]})
    call_command("llm_evaluate", str(path), "--models", "fake:small,fake:large:v2", "--min-score", "0")
    runs = {run.model: run for run in EvalRun.objects.all()}
    assert set(runs) == {"fake:small", "fake:large:v2"}  # only the first colon splits provider/model
    assert all(run.score == 1.0 for run in runs.values())
    from llm_ledger.models import LLMCall
    assert set(LLMCall.objects.values_list("model", flat=True)) == {"small", "large:v2"}  # no fallback
    out = capsys.readouterr().out
    assert "Comparison" in out and "fake:small" in out and "fake:large:v2" in out


def test_models_option_rejects_bad_specs(tmp_path):
    with pytest.raises(CommandError, match="provider:model"):
        call_command("llm_evaluate", str(write(tmp_path, "s.json", suite())), "--models", "justamodel")


def test_models_option_keeps_configured_provider_settings(ledger):
    from llm_ledger.evals import _settings_for

    ledger(PROVIDERS=[{"name": "anthropic", "model": "claude-haiku-5-5", "api_key_env": "MY_KEY", "timeout": 9}])
    pinned = _settings_for({"name": "anthropic", "model": "claude-haiku-5-5"})["PROVIDERS"]
    assert pinned == [{"name": "anthropic", "model": "claude-haiku-5-5", "api_key_env": "MY_KEY", "timeout": 9}]


def test_models_option_reuses_the_provider_settings_for_other_models(ledger):
    """Comparing a smaller local model must keep options like running on CPU."""
    from llm_ledger.evals import _settings_for

    ledger(PROVIDERS=[{"name": "anthropic", "model": "claude-haiku-5-5"},
                      {"name": "ollama", "model": "qwen2.5:1.5b", "model_options": {"num_gpu": 0}}])
    pinned = _settings_for({"name": "ollama", "model": "qwen2.5:0.5b"})["PROVIDERS"]
    assert pinned == [{"name": "ollama", "model": "qwen2.5:0.5b", "model_options": {"num_gpu": 0}}]
