"""Ciclo de vida de la sesión: archivo, purga, reset, herencia y auditoría (fase 2, olvido)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.schemas.session import ProjectMetadata
from app.services.errors import SessionArchivedError, SessionNotFoundError
from app.services.llm_client import LLMResult
from app.state.sessions import InMemorySessionStore, get_session_store
from tests.llm_fakes import tier_turn

FORM = {"project_type": "web_saas", "detail_level": "medium"}
TRANSCRIPT = "Quiero una aplicación web de reservas para un centro deportivo con login y pagos."


def _store(clock: dict, max_active: int = 5) -> InMemorySessionStore:
    return InMemorySessionStore(
        max_active=max_active,
        idle_archive=timedelta(hours=24),
        archive_retention=timedelta(hours=72),
        now=lambda: clock["now"],
    )


def _result(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        model="openrouter/openai/gpt-4o-mini",
        finish_reason="stop",
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        cached_tokens=0,
        cache_creation_tokens=None,
        cost_usd=0.0,
        latency_ms=5,
    )


@pytest.fixture
def fake(monkeypatch):
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result('{"set_project_name": "Atlas"}')
        return _result(tier_turn("developer"))

    monkeypatch.setattr("app.services.llm_client.complete", complete)


def test_idle_session_is_archived_not_deleted() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock)
    session = store.create()
    clock["now"] += timedelta(hours=25)
    with pytest.raises(SessionArchivedError):
        store.get(session.id)
    kept = store.get(session.id, allow_archived=True)
    assert kept.status == "archived" and kept.archived_reason == "inactividad"


def test_archived_session_is_purged_after_retention() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock)
    session = store.create()
    clock["now"] += timedelta(hours=25)
    store.get(session.id, allow_archived=True)
    clock["now"] += timedelta(hours=73)
    with pytest.raises(SessionNotFoundError):
        store.get(session.id, allow_archived=True)


def test_capacity_purges_archived_before_active() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock, max_active=2)
    old = store.create()
    clock["now"] += timedelta(minutes=1)
    active = store.create()
    store.archive(active, "reset")
    clock["now"] += timedelta(minutes=1)
    store.get(old.id)  # La más vieja sigue activa.
    store.create()
    assert store.get(old.id).id == old.id
    with pytest.raises(SessionNotFoundError):
        store.get(active.id, allow_archived=True)


def test_session_in_flight_is_never_archived_or_evicted() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock, max_active=1)
    busy = store.create()
    busy.lock._locked = True  # Simula un turno en vuelo.
    try:
        clock["now"] += timedelta(hours=30)
        store.create()
        assert store.get(busy.id) is busy
    finally:
        busy.lock._locked = False


def test_get_refreshes_the_idle_clock() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock)
    session = store.create()
    clock["now"] += timedelta(hours=20)
    store.get(session.id)
    clock["now"] += timedelta(hours=20)
    assert store.get(session.id) is session


def test_turn_on_an_archived_session_is_409(client, fake) -> None:
    session_id = client.post("/sessions").json()["session_id"]
    get_session_store().archive(get_session_store().get(session_id), "inactividad")
    response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    assert response.status_code == 409
    assert response.json()["error"] == "session_archived"
    assert "inherit_memory" in response.json()["detail"]


def test_reset_archives_the_previous_and_can_inherit_memory(client, fake) -> None:
    first = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{first}/estimate", data={"transcript": TRANSCRIPT, **FORM})

    clean = client.post("/sessions", json={"previous_session_id": first}).json()
    assert clean["inherited_from"] is None
    assert get_session_store().get(clean["session_id"]).metadata == ProjectMetadata()
    assert client.get(f"/sessions/{first}").json()["status"] == "archived"

    heir = client.post("/sessions", json={"previous_session_id": first, "inherit_memory": True}).json()
    state = client.get(f"/sessions/{heir['session_id']}").json()
    assert heir["inherited_from"] == first
    assert state["project_metadata"]["project_name"] == "Atlas"
    assert state["turns"] == 0 and state["window_turns"] == 0
    assert state["memory_log"][0]["field"] == "inherited_from"


def test_get_session_is_auditable_without_history_text(client, fake) -> None:
    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    response = client.get(f"/sessions/{session_id}")
    body = response.json()
    assert body["turns"] == 1 and body["status"] == "active" and body["tier"] == "developer"
    assert body["memory_log"][0]["field"] == "project_name"
    assert TRANSCRIPT not in response.text


def test_record_serializes_history_and_memory_separately(client, fake) -> None:
    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    record = get_session_store().get(session_id).to_record()
    assert set(record) == {"session", "history", "memory", "memory_log"}
    assert record["memory"]["project_name"] == "Atlas"
    assert len(record["history"]) == 2
    assert "project_name" not in str(record["history"][0])


def test_inactivity_archive_counts_from_the_deadline() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0)}
    store = _store(clock)
    session = store.create()
    clock["now"] += timedelta(hours=24 + 72 + 1)  # Sin ninguna consulta intermedia.
    with pytest.raises(SessionNotFoundError):
        store.get(session.id, allow_archived=True)
