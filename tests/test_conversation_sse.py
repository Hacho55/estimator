"""SSE de conversación en /api/v2: tokens, estimate final e historial solo tras éxito."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.errors import LLMUnavailableError
from app.state.conversations import reset_store
from tests.llm_fakes import FakeResult, FakeStream
from tests.test_estimate_v2 import DESCRIPTION, FORM
from tests.test_sse import _events


def _rich_result(*, cache_hit: bool = False, markdown: str = "## Estimación: Hecha") -> FakeResult:
    result = FakeResult(cache_hit=cache_hit, markdown=markdown, message="listo")
    result.finish_reason = "stop"
    result.provider = "openai"
    result.prompt_version = "v1-test"
    result.kind = "estimation"
    result.estimation = {"project_title": "Demo", "phases": []}
    result.history_content = json.dumps({"kind": "estimation", "estimation": result.estimation})
    result.dropped_turns = 0
    result.warnings = []
    result.token_budget = SimpleNamespace(fits=True)
    result.cost_usd = 0.0
    result.trace_id = None
    return result


def test_create_conversation_stream_sends_tokens_and_persists(client, monkeypatch):
    reset_store()
    result = _rich_result()

    def fake_stream(transcription, conversation_history=None, *, overrides=None, session_id=None):
        assert transcription == DESCRIPTION
        assert not conversation_history
        assert session_id
        return FakeStream(["hola ", "mundo"], result, from_cache=False)

    monkeypatch.setattr("app.routers.estimations_v2.stream_estimation", fake_stream)
    with client.stream("POST", "/api/v2/conversations/stream", json=FORM) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    events = _events(body)
    assert [name for name, _ in events] == ["token", "token", "estimate"]
    estimate = json.loads(events[-1][1])
    assert estimate["conversation_id"]
    assert estimate["turn"] == 1
    assert estimate["kind"] == "estimation"
    assert estimate["estimation_structured"]["project_title"] == "Demo"
    assert "conversation_state" in estimate

    detail = client.get(f"/api/v2/conversations/{estimate['conversation_id']}")
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 2


def test_message_stream_with_fake_llm(client, fake_llm, monkeypatch):
    reset_store()
    created = client.post("/api/v2/conversations", json=FORM)
    assert created.status_code == 200, created.text
    conversation_id = created.json()["conversation_id"]
    assert len(client.get(f"/api/v2/conversations/{conversation_id}").json()["messages"]) == 2

    result = _rich_result(markdown="## Estimación: Ajuste")
    result.kind = "estimation_update"

    def fake_stream(transcription, conversation_history=None, *, overrides=None, session_id=None):
        assert conversation_history is not None
        assert len(conversation_history) == 2
        return FakeStream(["ajuste"], result, from_cache=False)

    monkeypatch.setattr("app.routers.estimations_v2.stream_estimation", fake_stream)
    with client.stream(
        "POST",
        f"/api/v2/conversations/{conversation_id}/messages/stream",
        json={"message": "Baja las horas de frontend."},
    ) as response:
        body = "".join(response.iter_text())
    events = _events(body)
    assert [name for name, _ in events] == ["token", "estimate"]
    estimate = json.loads(events[-1][1])
    assert estimate["turn"] == 2
    assert estimate["kind"] == "estimation_update"
    messages = client.get(f"/api/v2/conversations/{conversation_id}").json()["messages"]
    assert len(messages) == 4


def test_message_stream_error_does_not_append(client, fake_llm, monkeypatch):
    reset_store()
    created = client.post("/api/v2/conversations", json=FORM)
    conversation_id = created.json()["conversation_id"]

    class _Broken:
        from_cache = False
        result = None

        def __aiter__(self):
            return self._iterate()

        async def _iterate(self):
            yield "parcial"
            raise LLMUnavailableError("proxy caído")

    monkeypatch.setattr(
        "app.routers.estimations_v2.stream_estimation",
        lambda *args, **kwargs: _Broken(),
    )
    with client.stream(
        "POST",
        f"/api/v2/conversations/{conversation_id}/messages/stream",
        json={"message": "¿Cuánto cuesta el QA?"},
    ) as response:
        body = "".join(response.iter_text())
    events = _events(body)
    assert events[-1][0] == "error"
    assert json.loads(events[-1][1])["error"] == "llm_unavailable"
    messages = client.get(f"/api/v2/conversations/{conversation_id}").json()["messages"]
    assert len(messages) == 2


def test_message_stream_unknown_conversation_is_404(client):
    reset_store()
    response = client.post(
        "/api/v2/conversations/does-not-exist/messages/stream",
        json={"message": "hola"},
    )
    assert response.status_code == 404
