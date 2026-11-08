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
