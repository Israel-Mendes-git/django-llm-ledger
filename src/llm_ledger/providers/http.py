"""Providers that talk HTTP. No vendor SDKs: just httpx, so the package stays small."""

import os

import httpx

from .base import Completion, Provider, ProviderError, error_from_status


class HTTPProvider(Provider):
    default_base_url = ""

    def __init__(self, model, *, base_url=None, api_key=None, api_key_env=None, transport=None, **kwargs):
        super().__init__(model, **kwargs)
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._api_key = api_key
        self.api_key_env = api_key_env
        # Tests inject an httpx.MockTransport here.
        self.transport = transport

    @property
    def api_key(self):
        if self._api_key:
            return self._api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    def _post(self, path, payload, headers=None):
        try:
            with httpx.Client(timeout=self.timeout or 60, transport=self.transport) as client:
                response = client.post(f"{self.base_url}{path}", json=payload, headers=headers or {})
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout: {exc}", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"connection error: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise error_from_status(response.status_code, response.text)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("invalid JSON in response", retryable=True) from exc


class OllamaProvider(HTTPProvider):
    name = "ollama"
    is_local = True
    default_base_url = "http://localhost:11434"

    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        chat = ([{"role": "system", "content": system}] if system else []) + list(messages)
        data = self._post(
            "/api/chat",
            {
                "model": self.model,
                "messages": chat,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": temperature},
            },
        )
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected Ollama response shape") from exc
        return Completion(text, data.get("prompt_eval_count", 0) or 0, data.get("eval_count", 0) or 0)


class OpenAICompatibleProvider(HTTPProvider):
    """OpenAI's chat completions API and the many servers that copy it (LM Studio, vLLM, Groq...)."""

    name = "openai"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, model, **kwargs):
        kwargs.setdefault("api_key_env", "OPENAI_API_KEY")
        super().__init__(model, **kwargs)

    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        chat = ([{"role": "system", "content": system}] if system else []) + list(messages)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = self._post(
            "/chat/completions",
            {"model": self.model, "messages": chat, "max_tokens": max_tokens, "temperature": temperature},
            headers,
        )
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("unexpected OpenAI-compatible response shape") from exc
        usage = data.get("usage") or {}
        return Completion(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))


class AnthropicProvider(HTTPProvider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"

    def __init__(self, model, **kwargs):
        kwargs.setdefault("api_key_env", "ANTHROPIC_API_KEY")
        super().__init__(model, **kwargs)

    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        payload = {"model": self.model, "messages": list(messages), "max_tokens": max_tokens, "temperature": temperature}
        if system:
            payload["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": self.api_version}
        data = self._post("/messages", payload, headers)
        try:
            text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected Anthropic response shape") from exc
        usage = data.get("usage") or {}
        return Completion(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
