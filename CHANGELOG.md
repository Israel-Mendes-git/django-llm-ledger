# Changelog

## Unreleased

- Budget checks reuse the month's spend for `BUDGET_CHECK_TTL` seconds (default 60) instead of
  summing the ledger on every call.
- `manage.py llm_ledger_prune`: delete old calls (`--days`) or only their prompt/response text
  (`--texts-older-than`), in batches, with `--dry-run`. Defaults from `RETENTION_DAYS` and
  `TEXT_RETENTION_DAYS`.
- Conversations: `complete`, `stream`, `acomplete` and `astream` accept a list of
  `{"role": "user" | "assistant", "content": ...}` messages. Evaluation cases accept them as `input`.
- `llm_evaluate --models provider:model,...` runs the suites once per model, with no fallback, and
  prints a comparison table. Runs record the model and the dashboard shows it.
- Evaluations no longer use the cache, so a stored answer cannot stand in for the model being tested.
- `await llm.acomplete()` and `await llm.astream()` (returns an `AsyncLLMStream`) for async views,
  with the same ledger, cache, budget, retries and fallback as the sync calls.
- Monthly budgets per feature (`FEATURE_BUDGETS_USD`). Blocking only stops the feature over its own
  limit; `BudgetExceeded.feature` and the `feature` argument of `budget_exceeded` say which one.
- Dashboard: a "Budgets per feature" table; budget bars turn amber at 80%.

## 0.1.0 (2026-11-08)

First release.

- `llm.complete()` and `llm.stream()`: one call that records provider, model, tokens, cost, latency, feature and user for every attempt.
- Fallback chain across Ollama, OpenAI-compatible servers and Anthropic over plain HTTP, with retries and exponential backoff. Streams only fall back before the first chunk.
- Cache for identical requests, monthly budget with a `budget_exceeded` signal and optional blocking.
- Cost dashboard in the Django admin.
- Evaluation suites (`manage.py llm_evaluate`) with score, cost, tokens and time per case; fails CI below `--min-score`.
- Demo store in `example/`.
