from .base import Completion, Provider, ProviderError


class FakeProvider(Provider):
    """Deterministic provider for tests and offline demos.

    ``reply`` can be a string, a list of strings (returned in order, last one repeats)
    or a callable ``reply(prompt) -> str``. ``fail_times`` makes the first N calls fail.
    """

    name = "fake"
    is_local = True

    def __init__(self, model="fake", *, reply="ok", fail_times=0, retryable=True, **kwargs):
        super().__init__(model, **kwargs)
        self.reply = reply
        self.fail_times = fail_times
        self.retryable = retryable
        self.calls = 0

    def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(f"fake failure #{self.calls}", retryable=self.retryable)
        prompt = messages[-1]["content"]
        if callable(self.reply):
            text = self.reply(prompt)
        elif isinstance(self.reply, (list, tuple)):
            text = self.reply[min(self.calls - self.fail_times, len(self.reply)) - 1]
        else:
            text = self.reply
        # Rough token estimate: 1 token ~ 4 characters.
        return Completion(text, max(1, len(prompt) // 4), max(1, len(text) // 4))
