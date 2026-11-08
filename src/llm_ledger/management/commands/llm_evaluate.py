from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from llm_ledger.evals import SuiteError, load_suite, run_suite

SUITE_SUFFIXES = {".json", ".yaml", ".yml"}


class Command(BaseCommand):
    help = "Run LLM evaluation suites and fail when the score is below --min-score (use it in CI)."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+", help="Suite files or folders containing suites.")
        parser.add_argument("--min-score", type=float, default=1.0,
                            help="Minimum share of passing cases, 0 to 1 (default: 1.0).")
        parser.add_argument("--no-save", action="store_true", help="Do not store the run in the database.")

    def handle(self, *args, paths, min_score, no_save, **options):
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

        worst = 1.0
        for file in files:
            try:
                suite = load_suite(file)
            except (SuiteError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            report = run_suite(suite, save=not no_save)
            worst = min(worst, report.score)

            self.stdout.write(f"\n{report.suite}  ({file})")
            for result in report.results:
                mark = self.style.SUCCESS("PASS") if result.passed else self.style.ERROR("FAIL")
                self.stdout.write(f"  {mark}  {result.id}")
                for failure in result.failures:
                    self.stdout.write(f"        {failure}")
            style = self.style.SUCCESS if report.score >= min_score else self.style.ERROR
            self.stdout.write(style(f"  score {report.passed}/{report.total} = {report.score:.0%}"))
            cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
            self.stdout.write(f"  cost {cost}, {report.tokens} tokens, {report.avg_latency_ms} ms per case")

        if worst < min_score:
            raise CommandError(f"Evaluation score below {min_score:.0%}.")
