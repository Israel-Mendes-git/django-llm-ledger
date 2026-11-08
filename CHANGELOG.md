# Changelog

## 0.1.0 (2026-11-08)

First release.

- `llm.complete()` and `llm.stream()`: one call that records provider, model, tokens, cost, latency, feature and user for every attempt.
- Fallback chain across Ollama, OpenAI-compatible servers and Anthropic over plain HTTP, with retries and exponential backoff. Streams only fall back before the first chunk.
- Cache for identical requests, monthly budget with a `budget_exceeded` signal and optional blocking.
- Cost dashboard in the Django admin.
- Evaluation suites (`manage.py llm_evaluate`) with score, cost, tokens and time per case; fails CI below `--min-score`.
- Demo store in `example/`.
