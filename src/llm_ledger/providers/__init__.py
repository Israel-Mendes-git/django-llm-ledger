from django.utils.module_loading import import_string

from .base import Completion, Provider, ProviderError
from .fake import FakeProvider
from .http import AnthropicProvider, OllamaProvider, OpenAICompatibleProvider

REGISTRY = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "fake": FakeProvider,
}


def build_provider(spec):
    """Turn a settings entry into a Provider. Accepts a Provider instance unchanged."""
    if isinstance(spec, Provider):
        return spec
    options = dict(spec)
    name = options.pop("name")
    model = options.pop("model")
    cls = REGISTRY.get(name) or import_string(name)
    return cls(model, **options)


__all__ = [
    "Completion",
    "Provider",
    "ProviderError",
    "FakeProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "build_provider",
]
