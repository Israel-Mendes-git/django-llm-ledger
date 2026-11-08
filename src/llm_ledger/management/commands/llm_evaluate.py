from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from llm_ledger.evals import SuiteError, load_suite, parse_model, run_suite

SUITE_SUFFIXES = {".json", ".yaml", ".yml"}


class Command(BaseCommand):
    help = "Run LLM evaluation suites and fail when the score is below --min-score (use it in CI)."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", help="Suite files or folders containing suites.")
        parser.add_argument("--min-score", type=float, default=1.0,
                            help="Minimum share of passing cases, 0 to 1 (default: 1.0).")
        parser.add_argument("--no-save", action="store_true", help="Do not store the run in the database.")
        parser.add_argument(
            "--models",
            help="Comma-separated provider:model list, e.g. ollama:qwen2.5:1.5b,anthropic:claude-haiku-5-5. "
                 "Runs every suite once per model, with no fallback, and prints a comparison table.",
        )

    def handle(self, *args, paths, min_score, no_save, models=None, **options):
        try:
            pinned = [parse_model(m) for m in models.split(",") if m.strip()] if models else [None]
        except SuiteError as exc:
            raise CommandError(str(exc)) from exc
        files = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                files += sorted(p for p in path.iterdir() if p.suffix in SUITE_SUFFIXES)
            elif path.exists():
                files.append(path)
            else:
                raise CommandError(f"Not found: {path}")
        if not files:
            raise CommandError("No suite files found.")

        suites = []
        for file in files:
            try:
                suites.append((file, load_suite(file)))
            except (SuiteError, ValueError) as exc:
                raise CommandError(str(exc)) from exc

        worst = 1.0
        rows = []
        for model in pinned:
            label = f"{model['name']}:{model['model']}" if model else ""
            for file, suite in suites:
                report = run_suite(suite, save=not no_save, model=model)
                worst = min(worst, report.score)
                rows.append((label, report))

                self.stdout.write(f"\n{report.suite}  ({file})" + (f"  [{label}]" if label else ""))
                for result in report.results:
                    mark = self.style.SUCCESS("PASS") if result.passed else self.style.ERROR("FAIL")
                    self.stdout.write(f"  {mark}  {result.id}")
                    for failure in result.failures:
                        self.stdout.write(f"        {failure}")
                style = self.style.SUCCESS if report.score >= min_score else self.style.ERROR
                self.stdout.write(style(f"  score {report.passed}/{report.total} = {report.score:.0%}"))
                self.stdout.write(f"  cost {_cost(report)}, {report.tokens} tokens, "
                                  f"{report.avg_latency_ms} ms per case")

        if models:
            self._comparison(rows)
        if worst < min_score:
            raise CommandError(f"Evaluation score below {min_score:.0%}.")

    def _comparison(self, rows):
        header = ("model", "suite", "score", "cost (USD)", "tokens", "ms/case")
        table = [header] + [
            (label, r.suite, f"{r.passed}/{r.total}", _cost(r), str(r.tokens), str(r.avg_latency_ms))
            for label, r in rows
        ]
        widths = [max(len(row[i]) for row in table) for i in range(len(header))]
        self.stdout.write("\nComparison")
        for index, row in enumerate(table):
            cells = [cell.ljust(w) if i < 2 else cell.rjust(w) for i, (cell, w) in enumerate(zip(row, widths))]
            self.stdout.write("  " + "  ".join(cells))
            if index == 0:
                self.stdout.write("  " + "  ".join("-" * w for w in widths))


def _cost(report):
    return "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
