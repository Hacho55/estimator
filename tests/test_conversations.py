from datetime import datetime

import pytest

from app.schemas.conversation import ChatMessage
from app.services.errors import ConversationNotFoundError
from app.state.conversations import InMemoryConversationStore, select_history
from tests.llm_fakes import estimation_turn

TRANSCRIPTION = (
    "En la reunión el cliente pidió una landing con formulario, HubSpot y un blog. "
    "El diseño ya está en Figma y el plazo ideal es de cuatro semanas."
)


def _pair(index: int, kind: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="user", content=f"mensaje {index} " + "x" * 40, kind=kind, anchor=index == 0),
        ChatMessage(role="assistant", content=f"respuesta {index}", kind=kind, anchor=index == 0),
    ]


def test_window_keeps_transcription_and_last_estimation():
    messages: list[ChatMessage] = []
    kinds = ["estimation", "answer", "answer", "answer", "answer", "estimation_update"]
    for index, kind in enumerate(kinds):
        messages.extend(_pair(index, kind))
    kept, dropped = select_history(messages, max_turns=2, room_tokens=100_000)
    contents = [message.content for message in kept]
    assert "mensaje 0" in contents[0]
    assert "respuesta 5" in contents[-1]
    assert dropped == 3
    roles = [message.role for message in kept]
    assert roles[0] == "user"
    assert roles == ["user", "assistant"] * (len(roles) // 2)


def test_anchors_that_do_not_fit_raise():
    messages = _pair(0, "estimation")
    messages[0].content = "x" * 5000
    with pytest.raises(Exception, match="ventana"):
        select_history(messages, max_turns=10, room_tokens=5)


def test_store_expires_and_evicts():
    clock = {"now": datetime(2026, 1, 1, 9, 0)}
    store = InMemoryConversationStore(ttl_minutes=30, max_active=1, now=lambda: clock["now"])
    first = store.create()
    clock["now"] = datetime(2026, 1, 1, 10, 0)
    with pytest.raises(ConversationNotFoundError):
        store.get(first.id)
    clock["now"] = datetime(2026, 1, 1, 11, 0)
    created = store.create()
    second = store.create()
    with pytest.raises(ConversationNotFoundError):
        store.get(created.id)
    assert store.get(second.id).id == second.id


@pytest.mark.asyncio
async def test_lock_serializes_turns():
    store = InMemoryConversationStore(ttl_minutes=60, max_active=10)
    conversation = store.create()
    active = {"now": 0, "max": 0}

    async def enter():
        async with store.lock(conversation.id):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            import asyncio
            await asyncio.sleep(0.02)
            active["now"] -= 1

    import asyncio
    await asyncio.gather(enter(), enter())
    assert active["max"] == 1


def test_conversation_api_flow(client, fake_llm):
    fake_llm["scripts"][:] = [
        {"content": estimation_turn("estimation"), "finish_reason": "stop"},
        {"content": estimation_turn("estimation_update", message="Diseño subido a 60 horas."), "finish_reason": "stop"},
        {"content": estimation_turn("answer", message="Harían falta dos personas."), "finish_reason": "stop"},
    ]
    created = client.post("/api/v1/conversations", json={"transcription": TRANSCRIPTION})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["kind"] == "estimation"
    assert body["conversation_state"]["turns_total"] == 1
    conversation_id = body["conversation_id"]

    updated = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": "Sube diseño a 60 horas y añade testing."},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["kind"] == "estimation_update"

    answer = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": "¿Cuál sería el equipo ideal?"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["kind"] == "answer"
    assert answer.json()["estimation"] is None

    detail = client.get(f"/api/v1/conversations/{conversation_id}")
    stored = detail.json()["messages"]
    assert stored[1]["role"] == "assistant"
    assert "Landing con HubSpot" in stored[1]["content"]
    assert "no es json" not in stored[1]["content"]

    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    missing = client.get(f"/api/v1/conversations/{conversation_id}")
    assert missing.status_code == 404
    assert missing.json()["error"] == "conversation_not_found"
