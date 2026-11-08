from dataclasses import dataclass


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(Exception):
    """Raised by a provider when a call fails.

    ``retryable`` tells the client whether trying the same provider again makes sense
    (timeouts, rate limits, server errors) or whether it should move on right away.
    """

    def __init__(self, message, *, retryable=False, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class Provider:
    name = "base"
    #: Local providers never cost money, so no price table is needed.
    is_local = False

    def __init__(self, model, *, price=None, timeout=None, **options):
        self.model = model
        self.price = price
        self.timeout = timeout
        self.options = options

    @property
    def label(self):
        return f"{self.name}:{self.model}"

    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        """Return a Completion or raise ProviderError."""
        raise NotImplementedError

    def __repr__(self):
        return f"<{type(self).__name__} {self.model}>"


def error_from_status(status_code, body):
    retryable = status_code == 429 or status_code >= 500
    snippet = (body or "")[:300]
    return ProviderError(f"HTTP {status_code}: {snippet}", retryable=retryable, status_code=status_code)
