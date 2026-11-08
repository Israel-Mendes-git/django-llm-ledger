"""Evaluation suites: a file of questions plus what a good answer must (not) contain.

Example (YAML or JSON):

    name: support
    feature: support-eval          # optional, tags the calls in the ledger
    system: "You answer for ACME Store."
    target: myapp.ai.answer        # optional: a function(input) -> str to test instead of a raw prompt
    cases:
      - id: return-window
        input: "How long do I have to return an item?"
        must_contain: ["30 days"]
        must_not_contain: ["I don't know"]
      - id: shipping
        input: "Do you ship to Brazil?"
        any_of: ["yes", "we ship"]
        regex: "\\b(Brazil|Brasil)\\b"
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from django.utils.module_loading import import_string


@dataclass
class CaseResult:
    id: str
    passed: bool
    output: str
    failures: list = field(default_factory=list)


@dataclass
class EvalReport:
    suite: str
    results: list

    @property
    def total(self):
        return len(self.results)

    @property
    def passed(self):
        return sum(r.passed for r in self.results)

    @property
    def score(self):
        return self.passed / self.total if self.total else 0.0


class SuiteError(Exception):
    pass


def load_suite(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise SuiteError("YAML suites need PyYAML: pip install django-llm-ledger[yaml]") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict) or not data.get("cases"):
        raise SuiteError(f"{path}: a suite needs a non-empty 'cases' list")
    data.setdefault("name", path.stem)
    for index, case in enumerate(data["cases"], 1):
        if "input" not in case:
            raise SuiteError(f"{path}: case {index} has no 'input'")
        case.setdefault("id", f"case-{index}")
    return data


def check(case, output):
    """Return the list of failed expectations (empty list = pass). Matching ignores case."""
    failures = []
    low = output.lower()
    for needle in case.get("must_contain", []):
        if needle.lower() not in low:
            failures.append(f"missing: {needle!r}")
    for needle in case.get("must_not_contain", []):
        if needle.lower() in low:
            failures.append(f"should not contain: {needle!r}")
    any_of = case.get("any_of")
    if any_of and not any(n.lower() in low for n in any_of):
        failures.append(f"none of: {any_of!r}")
    pattern = case.get("regex")
    if pattern and not re.search(pattern, output, re.IGNORECASE):
        failures.append(f"no match for /{pattern}/")
    return failures


def run_suite(suite, *, client=None, save=True):
    from .client import LLMError, llm

    client = client or llm
    target = import_string(suite["target"]) if suite.get("target") else None
    feature = suite.get("feature", f"eval:{suite['name']}")

    results = []
    for case in suite["cases"]:
        try:
            if target:
                output = str(target(case["input"]))
            else:
                output = client.complete(
                    case["input"], feature=feature, system=suite.get("system"), use_cache=False
                ).text
        except LLMError as exc:
            results.append(CaseResult(case["id"], False, "", [f"error: {exc}"]))
            continue
        failures = check(case, output)
        results.append(CaseResult(case["id"], not failures, output, failures))

    report = EvalReport(suite["name"], results)
    if save:
        from .models import EvalRun

        EvalRun.objects.create(
            suite=report.suite,
            total=report.total,
            passed=report.passed,
            score=report.score,
            details=[asdict(r) for r in results],
        )
    return report
