"""Sesión 3 · streamlit: el puente al chat no llama al modelo en estos tests."""

from types import SimpleNamespace

from app.services.errors import ServiceError
from streamlit_app import open_reply, visible_text


class _FakeResult:
    def __init__(self, *, cache_hit: bool, markdown: str | None = None, message: str | None = None) -> None:
        self.cache_hit = cache_hit
        self.estimation_markdown = markdown
        self.message = message
        self.model = "openrouter/openai/gpt-4o-mini"
        self.latency_ms = 0 if cache_hit else 12
        self.usage = SimpleNamespace(input_tokens=0 if cache_hit else 8, output_tokens=0 if cache_hit else 3)


class _FakeStream:
    def __init__(self, pieces: list[str], result: _FakeResult, from_cache: bool) -> None:
        self._pieces = pieces
        self.result = None
        self._result = result
        self.from_cache = from_cache

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for piece in self._pieces:
            yield piece
        self.result = self._result


def test_cache_hit_returns_the_markdown_without_a_token_generator(monkeypatch):
    result = _FakeResult(cache_hit=True, markdown="## Estimación: Panel")
    monkeypatch.setattr(
        "streamlit_app.stream_estimation",
        lambda transcription, history: _FakeStream(["{json}"], result, from_cache=True),
    )
    generator, stream = open_reply("texto", [])
    assert generator is None
    assert visible_text(stream.result) == "## Estimación: Panel"
    assert stream.result.cache_hit is True


def test_miss_yields_the_pieces_and_then_the_result(monkeypatch):
    result = _FakeResult(cache_hit=False, markdown="## Estimación: Hecha")
    monkeypatch.setattr(
        "streamlit_app.stream_estimation",
        lambda transcription, history: _FakeStream(["hola ", "mundo"], result, from_cache=False),
    )
    generator, stream = open_reply("texto", [])
    assert generator is not None
    assert list(generator) == ["hola ", "mundo"]
    assert visible_text(stream.result) == "## Estimación: Hecha"


def test_a_service_error_closes_the_loop(monkeypatch):
    class _Broken:
        from_cache = False
        result = None

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            raise ServiceError("caída")
            yield ""

    monkeypatch.setattr("streamlit_app.stream_estimation", lambda transcription, history: _Broken())
    try:
        open_reply("texto", [])
    except ServiceError as exc:
        assert exc.message == "caída"
    else:
        raise AssertionError("tenía que propagar el error")
