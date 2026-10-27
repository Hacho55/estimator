"""Sesión 3 · chainlit: un acierto no entrega trozos; un fallo del modelo se propaga."""

from app.services.errors import ServiceError
from chainlit_app import drive_estimation
from tests.test_streamlit_app import _FakeResult, _FakeStream


async def test_cache_hit_does_not_emit_tokens(monkeypatch):
    result = _FakeResult(cache_hit=True, markdown="## Estimación: Panel")
    monkeypatch.setattr(
        "chainlit_app.stream_estimation",
        lambda transcription, history: _FakeStream(["{json}"], result, from_cache=True),
    )
    seen: list[str] = []

    async def on_token(piece: str) -> None:
        seen.append(piece)

    got = await drive_estimation("texto", [], on_token)
    assert seen == []
    assert got.cache_hit is True
    assert got.estimation_markdown == "## Estimación: Panel"


async def test_miss_emits_each_piece(monkeypatch):
    result = _FakeResult(cache_hit=False, markdown="## Estimación: Hecha")
    monkeypatch.setattr(
        "chainlit_app.stream_estimation",
        lambda transcription, history: _FakeStream(["hola ", "mundo"], result, from_cache=False),
    )
    seen: list[str] = []

    async def on_token(piece: str) -> None:
        seen.append(piece)

    got = await drive_estimation("texto", [], on_token)
    assert seen == ["hola ", "mundo"]
    assert got.estimation_markdown == "## Estimación: Hecha"


async def test_service_error_propagates(monkeypatch):
    class _Broken:
        from_cache = False

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            raise ServiceError("caída")
            yield ""

    monkeypatch.setattr("chainlit_app.stream_estimation", lambda transcription, history: _Broken())

    async def on_token(piece: str) -> None:
        return None

    try:
        await drive_estimation("texto", [], on_token)
    except ServiceError as exc:
        assert exc.message == "caída"
    else:
        raise AssertionError("tenía que propagar el error")
