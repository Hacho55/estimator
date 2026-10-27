"""Sesión 3 · logs: inicio, fin y fallo de cada llamada, sin sustituir el evento estimation."""

from openai import APITimeoutError, AuthenticationError
from structlog.testing import capture_logs

from app.config import get_settings
from app.observability import configure_call_logs
from app.services import llm_client
from app.services.errors import LLMUnavailableError
from app.services.llm_service import stream_estimation
from tests.test_estimate import TRANSCRIPTION
from tests.test_fallback import PRIMARY, RESERVE, _install, _request, _status
from tests.test_streaming import _chunk, _usage
from tests.test_streaming import _install as install_stream

MESSAGES = [{"role": "user", "content": "hola"}]
RESPONSE_FORMAT = {"type": "json_object"}


def _info_logs(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    configure_call_logs()


async def test_a_stream_logs_start_and_completion(monkeypatch):
    _info_logs(monkeypatch)
    install_stream(monkeypatch, [[_chunk("hola", finish="stop", usage=_usage())]])
    with capture_logs() as logs:
        pieces = [piece async for piece in llm_client.TokenStream(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")]
    assert pieces == ["hola"]
    assert [item["event"] for item in logs] == ["llm_call_started", "llm_call_completed"]
    assert logs[1]["fallback_used"] is False


async def test_a_successful_call_logs_start_and_completion(monkeypatch):
    _info_logs(monkeypatch)
    _install(monkeypatch, [PRIMARY])
    with capture_logs() as logs:
        await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate", model=PRIMARY)
    events = [item["event"] for item in logs]
    assert events == ["llm_call_started", "llm_call_completed"]
    assert logs[0]["cache_hit"] is False
    assert logs[0]["provider"] == "openai"
    assert logs[1]["finish_reason"] == "stop"
    assert logs[1]["fallback_used"] is False
    assert logs[1]["tokens_out"] == 2


async def test_fallback_logs_the_failure_and_the_reserve(monkeypatch):
    _info_logs(monkeypatch)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", RESERVE)
    get_settings.cache_clear()
    _install(monkeypatch, [APITimeoutError(request=_request()), RESERVE])
    with capture_logs() as logs:
        result = await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
    assert result.model == RESERVE
    failed = next(item for item in logs if item["event"] == "llm_call_failed")
    assert failed["error_type"] == "timeout"
    assert failed["will_fallback"] is True
    assert failed["attempt"] == 1
    assert failed["provider"] == "openai"
    completed = next(item for item in logs if item["event"] == "llm_call_completed")
    assert completed["fallback_used"] is True
    assert completed["model"] == RESERVE


async def test_auth_failure_does_not_promise_a_fallback(monkeypatch):
    _info_logs(monkeypatch)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", RESERVE)
    get_settings.cache_clear()
    _install(monkeypatch, [_status(AuthenticationError, 401, "invalid key")])
    with capture_logs() as logs:
        try:
            await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
        except LLMUnavailableError:
            pass
        else:
            raise AssertionError("un 401 no tiene reserva")
    failed = [item for item in logs if item["event"] == "llm_call_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "auth"
    assert failed[0]["will_fallback"] is False
    assert failed[0]["attempt"] == 1


async def test_cache_hit_is_logged_without_a_second_model_call(client, fake_llm, monkeypatch):
    _info_logs(monkeypatch)
    seeded = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert seeded.status_code == 200
    with capture_logs() as logs:
        stream = stream_estimation(TRANSCRIPTION)
        pieces = [piece async for piece in stream]
    assert pieces == [stream.result.estimation_markdown]
    assert len(fake_llm["calls"]) == 1
    completed = [item for item in logs if item["event"] == "llm_call_completed"]
    assert completed[-1]["cache_hit"] is True
    assert completed[-1]["tokens_out"] == 0
