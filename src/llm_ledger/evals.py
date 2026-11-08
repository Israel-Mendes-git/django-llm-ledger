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
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from django.test.utils import override_settings
from django.utils.module_loading import import_string


@dataclass
class CaseResult:
    id: str
    passed: bool
    output: str
    failures: list = field(default_factory=list)
    cost_usd: float | None = None
    tokens: int = 0
    latency_ms: int = 0


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

    @property
    def cost_usd(self):
        """Total cost, or None when no call had a known price."""
        costs = [r.cost_usd for r in self.results if r.cost_usd is not None]
        return sum(costs) if costs else None

    @property
    def tokens(self):
        return sum(r.tokens for r in self.results)

    @property
    def avg_latency_ms(self):
        return round(sum(r.latency_ms for r in self.results) / self.total) if self.total else 0


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


def _usage_since(last_id):
    """Cost and tokens of the ledger calls recorded after ``last_id``.

    Reading the ledger (instead of the return value) also covers ``target`` functions,
    which may make any number of calls and return plain text.
    """
    from django.db.models import Sum

    from .models import LLMCall

    totals = LLMCall.objects.filter(id__gt=last_id).aggregate(
        cost=Sum("cost_usd"), inp=Sum("input_tokens"), out=Sum("output_tokens"))
    cost = float(totals["cost"]) if totals["cost"] is not None else None
    return cost, (totals["inp"] or 0) + (totals["out"] or 0)


def _last_call_id():
    from .models import LLMCall

    return LLMCall.objects.order_by("-id").values_list("id", flat=True).first() or 0


def parse_model(spec):
    """``"ollama:qwen2.5:1.5b"`` -> ``{"name": "ollama", "model": "qwen2.5:1.5b"}``.

    Only the first colon separates provider and model, since model names often contain colons.
    """
    name, sep, model = spec.strip().partition(":")
    if not sep or not name or not model:
        raise SuiteError(f"--models expects provider:model, got {spec!r}")
    return {"name": name, "model": model}


def _settings_for(model):
    """LLM_LEDGER for an eval run: never the cache, and optionally a single pinned model."""
    from django.conf import settings

    values = {**(getattr(settings, "LLM_LEDGER", {}) or {}), "CACHE_TTL": 0}
    if model is not None:
        # Reuse the settings of a configured provider (api_key_env, base_url, model_options...):
        # the same provider and model if there is one, otherwise the same provider.
        same_name = [dict(p) for p in values.get("PROVIDERS", [])
                     if isinstance(p, dict) and p.get("name") == model["name"]]
        exact = [p for p in same_name if p.get("model") == model["model"]]
        configured = (exact or same_name or [{}])[0]
        values["PROVIDERS"] = [{**configured, **model}]
    return values


def run_suite(suite, *, client=None, save=True, model=None):
    """Run every case and return an EvalReport.

    The cache is always skipped: an evaluation measures the model as it answers now, and a cached
    answer would report a stale text at zero cost. ``model`` (a ``{"name", "model"}`` dict) pins the
    whole run, target functions included, to that one provider with no fallback.
    """
    with override_settings(LLM_LEDGER=_settings_for(model)):
        return _run_suite(suite, client=client, save=save, model=model)


def _run_suite(suite, *, client, save, model):
    from .client import LLMError, llm

    client = client or llm
    target = import_string(suite["target"]) if suite.get("target") else None
    feature = suite.get("feature", f"eval:{suite['name']}")

    results = []
    for case in suite["cases"]:
        last_id = _last_call_id()
        started = time.perf_counter()
        try:
            if target:
                output = str(target(case["input"]))
            else:
                output = client.complete(
                    case["input"], feature=feature, system=suite.get("system"), use_cache=False
                ).text
            failures = check(case, output)
        except LLMError as exc:
            output, failures = "", [f"error: {exc}"]
        latency = round((time.perf_counter() - started) * 1000)
        cost, tokens = _usage_since(last_id)
        results.append(CaseResult(case["id"], not failures, output, failures, cost, tokens, latency))

    report = EvalReport(suite["name"], results)
    if save:
        from .models import EvalRun

        EvalRun.objects.create(
            suite=report.suite,
            total=report.total,
            passed=report.passed,
            score=report.score,
            details=[asdict(r) for r in results],
            cost_usd=report.cost_usd,
            tokens=report.tokens,
            latency_ms=report.avg_latency_ms,
            model=f"{model['name']}:{model['model']}" if model else "",
        )
    return report
