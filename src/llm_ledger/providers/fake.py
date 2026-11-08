from .base import Completion, Provider, ProviderError


class FakeProvider(Provider):
    """Deterministic provider for tests and offline demos.

    ``reply`` can be a string, a list of strings (returned in order, last one repeats)
    or a callable ``reply(prompt) -> str``. ``fail_times`` makes the first N calls fail.
    """

    name = "fake"
    is_local = True

    def __init__(self, model="fake", *, reply="ok", fail_times=0, retryable=True, fail_mid_stream=False,
                 **kwargs):
        super().__init__(model, **kwargs)
        self.fail_mid_stream = fail_mid_stream
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
        # Rough token estimate: 1 token ~ 4 characters, over the whole conversation.
        sent = sum(len(m["content"]) for m in messages) + len(system or "")
        return Completion(text, max(1, sent // 4), max(1, len(text) // 4))

    def stream(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
        """Yield the reply word by word; ``fail_mid_stream`` breaks after the first word."""
        completion = self.complete(messages, system=system, max_tokens=max_tokens, temperature=temperature)
        for index, word in enumerate(completion.text.split(" ")):
            yield word if index == 0 else " " + word
            if self.fail_mid_stream:
                raise ProviderError("fake failure mid-stream", retryable=True)
        yield completion
