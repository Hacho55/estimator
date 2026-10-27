"""Sesión 3 · fallback: la segunda llamada solo ocurre ante el fallo acordado."""

from types import SimpleNamespace

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from app.config import Settings, get_settings
from app.services import llm_client
from app.services.errors import LLMTimeoutError, LLMUnavailableError
from tests.llm_fakes import estimation_turn
from tests.test_estimate import TRANSCRIPTION

PRIMARY = "openrouter/openai/gpt-4o-mini"
RESERVE = "openrouter/anthropic/claude-haiku-4.5"
MESSAGES = [{"role": "user", "content": "hola"}]
RESPONSE_FORMAT = {"type": "json_object"}


class _Raw:
    def __init__(self, model: str, content: str = "ok") -> None:
        self.headers = {"x-litellm-response-cost": "0.01"}
        self._model = model
        self._content = content

    def parse(self):
        message = SimpleNamespace(content=self._content)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12, prompt_tokens_details=None)
        return SimpleNamespace(model=self._model, choices=[choice], usage=usage)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.invalid/chat")


def _status(cls, status: int, message: str):
    request = _request()
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body={"error": {"message": message}})


def _install(monkeypatch, steps: list):
    calls: list[dict] = []

    async def create(**payload):
        calls.append(payload)
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Raw(step)

    monkeypatch.setattr(llm_client.client.chat.completions.with_raw_response, "create", create)
    return calls


def _enable_reserve(monkeypatch, model: str = RESERVE) -> None:
    monkeypatch.setenv("LLM_FALLBACK_MODEL", model)
    get_settings.cache_clear()


async def test_primary_answers(monkeypatch):
    calls = _install(monkeypatch, [PRIMARY])
    result = await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
    assert result.model == PRIMARY
    assert [call["model"] for call in calls] == [PRIMARY]


async def test_timeout_rate_limit_server_and_format_use_the_reserve(monkeypatch):
    _enable_reserve(monkeypatch)
    monkeypatch.setattr(Settings, "langfuse_enabled", property(lambda self: True))
    failures = [
        APITimeoutError(request=_request()),
        _status(RateLimitError, 429, "rate limit"),
        _status(InternalServerError, 500, "upstream unavailable"),
        _status(BadRequestError, 400, "model rejected response_format json_schema"),
        APIConnectionError(message="connection reset", request=_request()),
    ]
    reasons = ["timeout", "rate_limit", "server_error", "response_format", "connection"]
    for failure, reason in zip(failures, reasons, strict=True):
        calls = _install(monkeypatch, [failure, RESERVE])
        result = await llm_client.complete(
            MESSAGES,
            RESPONSE_FORMAT,
            name="generate-estimate",
            metadata={"prompt_version": "v1"},
        )
        assert result.model == RESERVE
        assert calls[1]["model"] == RESERVE
        assert calls[1]["name"] == "generate-estimate-fallback"
        assert calls[1]["metadata"]["fallback"] is True
        assert calls[1]["metadata"]["error_type"] == reason
        assert calls[1]["metadata"]["prompt_version"] == "v1"


async def test_auth_and_plain_bad_request_stay_on_the_primary(monkeypatch):
    _enable_reserve(monkeypatch)
    failures = [
        _status(AuthenticationError, 401, "invalid key"),
        _status(BadRequestError, 400, "messages is required"),
    ]
    for failure in failures:
        calls = _install(monkeypatch, [failure, RESERVE])
        try:
            await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
        except LLMUnavailableError:
            pass
        else:
            raise AssertionError("tenía que fallar sin reserva")
        assert [call["model"] for call in calls] == [PRIMARY]


async def test_reserve_failure_surfaces_the_service_error(monkeypatch):
    _enable_reserve(monkeypatch)
    monkeypatch.setattr(Settings, "langfuse_enabled", property(lambda self: True))
    calls = _install(
        monkeypatch,
        [APITimeoutError(request=_request()), APITimeoutError(request=_request())],
    )
    try:
        await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="repair-estimate")
    except LLMTimeoutError:
        pass
    else:
        raise AssertionError("el reserva también tenía que fallar")
    assert [call["model"] for call in calls] == [PRIMARY, RESERVE]
    assert calls[1]["name"] == "repair-estimate-fallback"


async def test_empty_reserve_does_not_retry(monkeypatch):
    get_settings.cache_clear()
    calls = _install(monkeypatch, [APITimeoutError(request=_request()), RESERVE])
    try:
        await llm_client.complete(MESSAGES, RESPONSE_FORMAT, name="generate-estimate")
    except LLMTimeoutError:
        pass
    else:
        raise AssertionError("sin reserva tenía que salir el timeout")
    assert len(calls) == 1


async def test_estimate_publishes_the_model_that_answered(client, monkeypatch):
    _enable_reserve(monkeypatch)
    steps = [APITimeoutError(request=_request()), RESERVE]

    async def create(**payload):
        step = steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Raw(step, content=estimation_turn())

    monkeypatch.setattr(llm_client.client.chat.completions.with_raw_response, "create", create)
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == RESERVE
    assert body["provider"] == "anthropic"
