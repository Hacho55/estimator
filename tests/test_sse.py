"""Sesión 3 · sse: el POST largo sigue igual y el stream manda token y estimate."""

import json

from app.services.errors import LLMOutputTruncatedError
from tests.test_estimate import TRANSCRIPTION
from tests.test_streamlit_app import _FakeResult, _FakeStream


def _events(body: str) -> list[tuple[str, str]]:
    found = []
    event = "message"
    data = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif line == "" and data:
            found.append((event, "\n".join(data)))
            event = "message"
            data = []
    if data:
        found.append((event, "\n".join(data)))
    return found


def test_stream_sends_tokens_and_then_the_estimate(client, monkeypatch):
    result = _FakeResult(cache_hit=False, markdown="## Estimación: Hecha")
    result.finish_reason = "stop"
    result.provider = "openai"
    result.prompt_version = "v1-test"
    monkeypatch.setattr(
        "app.routers.estimations.stream_estimation",
        lambda transcription, history=None: _FakeStream(["hola ", "mundo"], result, from_cache=False),
    )
    with client.stream("POST", "/api/v1/estimate/stream", json={"transcription": TRANSCRIPTION}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    events = _events(body)
    assert [name for name, _data in events] == ["token", "token", "estimate"]
    assert json.loads(events[0][1]) == "hola "
    assert json.loads(events[1][1]) == "mundo"
    estimate = json.loads(events[2][1])
    assert estimate["estimation"] == "## Estimación: Hecha"
    assert estimate["cache_hit"] is False
    assert estimate["finish_reason"] == "stop"
    assert estimate["input_tokens"] == 8


def test_cache_hit_sends_only_the_estimate(client, monkeypatch):
    result = _FakeResult(cache_hit=True, markdown="## Estimación: Panel")
    result.finish_reason = "stop"
    result.provider = "openai"
    result.prompt_version = "v1-test"
    monkeypatch.setattr(
        "app.routers.estimations.stream_estimation",
        lambda transcription, history=None: _FakeStream(["{json}"], result, from_cache=True),
    )
    with client.stream("POST", "/api/v1/estimate/stream", json={"transcription": TRANSCRIPTION}) as response:
        body = "".join(response.iter_text())
    events = _events(body)
    assert [name for name, _data in events] == ["estimate"]
    assert json.loads(events[0][1])["cache_hit"] is True


def test_truncated_stream_sends_an_error_event(client, monkeypatch):
    class _Broken:
        from_cache = False
        result = None

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield "incompleto"
            raise LLMOutputTruncatedError()

    monkeypatch.setattr(
        "app.routers.estimations.stream_estimation",
        lambda transcription, history=None: _Broken(),
    )
    with client.stream("POST", "/api/v1/estimate/stream", json={"transcription": TRANSCRIPTION}) as response:
        body = "".join(response.iter_text())
    events = _events(body)
    assert events[0][0] == "token"
    assert events[-1][0] == "error"
    assert json.loads(events[-1][1])["error"] == "llm_output_truncated"
