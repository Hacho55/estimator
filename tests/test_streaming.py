"""Sesión 3 · streaming: trozos del modelo, y la reserva solo si aún no salió texto."""

from types import SimpleNamespace

from openai import APIConnectionError, APITimeoutError

from app.config import Settings, get_settings
from app.services import llm_client
from app.services.errors import LLMOutputTruncatedError, LLMUnavailableError
from app.services.llm_service import stream_estimation
from tests.llm_fakes import estimation_turn
from tests.test_estimate import TRANSCRIPTION
from tests.test_fallback import RESERVE, _request

PRIMARY = "openrouter/openai/gpt-4o-mini"
MESSAGES = [{"role": "user", "content": "hola"}]
RESPONSE_FORMAT = {"type": "json_object"}


def _chunk(text, *, finish=None, usage=None, model=PRIMARY):
    delta = SimpleNamespace(content=text)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


def _usage(prompt_tokens=10, completion_tokens=4):
    return SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens_details=None,
    )


def _install(monkeypatch, steps: list):
    calls: list[dict] = []

    async def create(**payload):
        calls.append(payload)
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step

        async def chunks():
            for item in step:
                if isinstance(item, Exception):
                    raise item
                yield item

        return chunks()

    monkeypatch.setattr(llm_client.client.chat.completions, "create", create)
    return calls


async def test_stream_yields_pieces_and_keeps_the_result(monkeypatch):
    usage = _usage()
    calls = _install(
        monkeypatch,
        [[_chunk('{"a":'), _chunk("1}", finish="stop", usage=usage)]],
    )
    stream = llm_client.TokenStream(MESSAGES, RESPONSE_FORMAT, name="generate-estimate", model=PRIMARY)
    pieces = [piece async for piece in stream]
    assert pieces == ['{"a":', "1}"]
    assert stream.result is not None
    assert stream.result.content == '{"a":1}'
    assert stream.result.finish_reason == "stop"
    assert stream.result.output_tokens == 4
    assert calls[0]["stream"] is True
    assert calls[0]["model"] == PRIMARY


async def test_timeout_before_text_uses_the_reserve(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", RESERVE)
    get_settings.cache_clear()
    monkeypatch.setattr(Settings, "langfuse_enabled", property(lambda self: True))
    calls = _install(
        monkeypatch,
        [
            APITimeoutError(request=_request()),
            [_chunk("ok", finish="stop", usage=_usage(), model=RESERVE)],
        ],
    )
    stream = llm_client.TokenStream(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
    pieces = [piece async for piece in stream]
    assert pieces == ["ok"]
    assert stream.result is not None
    assert stream.result.model == RESERVE
    assert [call["model"] for call in calls] == [PRIMARY, RESERVE]
    assert calls[1]["name"] == "generate-estimate-fallback"


async def test_error_after_text_does_not_switch_model(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODEL", RESERVE)
    get_settings.cache_clear()
    calls = _install(
        monkeypatch,
        [[_chunk("hola"), APIConnectionError(message="reset", request=_request())]],
    )
    stream = llm_client.TokenStream(MESSAGES, RESPONSE_FORMAT, name="generate-estimate", model=PRIMARY)
    seen = []
    try:
        async for piece in stream:
            seen.append(piece)
    except LLMUnavailableError:
        pass
    else:
        raise AssertionError("tenía que fallar sin cambiar de modelo")
    assert seen == ["hola"]
    assert len(calls) == 1


async def test_cache_hit_yields_the_stored_text_once(client, fake_llm, monkeypatch):
    seeded = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert seeded.status_code == 200

    async def create(**payload):
        raise AssertionError(payload)

    monkeypatch.setattr(llm_client.client.chat.completions, "create", create)
    stream = stream_estimation(TRANSCRIPTION)
    pieces = [piece async for piece in stream]
    assert pieces == [estimation_turn()]
    assert stream.result is not None
    assert stream.result.cache_hit is True
    assert stream.result.estimation_markdown.startswith("## Estimación:")
    assert len(fake_llm["calls"]) == 1


async def test_streamed_estimate_is_cached_for_the_next_call(monkeypatch):
    body = estimation_turn()
    mid = len(body) // 2
    calls = _install(
        monkeypatch,
        [[_chunk(body[:mid], model=PRIMARY), _chunk(body[mid:], finish="stop", usage=_usage(), model=PRIMARY)]],
    )
    stream = stream_estimation(TRANSCRIPTION)
    pieces = [piece async for piece in stream]
    assert "".join(pieces) == body
    assert stream.result is not None
    assert stream.result.cache_hit is False
    assert stream.result.estimation.total_hours == 100

    again = stream_estimation(TRANSCRIPTION)
    cached_pieces = [piece async for piece in again]
    assert cached_pieces == [body]
    assert again.result is not None
    assert again.result.cache_hit is True
    assert len(calls) == 1


async def test_truncated_stream_is_not_cached(monkeypatch):
    calls = _install(
        monkeypatch,
        [[_chunk("{", finish="length")], [_chunk("{", finish="length")]],
    )
    stream = stream_estimation(TRANSCRIPTION)
    try:
        async for _piece in stream:
            pass
    except LLMOutputTruncatedError:
        pass
    else:
        raise AssertionError("una respuesta cortada tenía que avisar")
    follow = stream_estimation(TRANSCRIPTION)
    try:
        async for _piece in follow:
            pass
    except LLMOutputTruncatedError:
        pass
    assert len(calls) == 2
