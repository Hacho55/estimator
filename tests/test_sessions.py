"""Sesiones: metadatos, adjuntos y ventana deslizante."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import BytesIO

import pytest

from app.config import get_settings
from app.context.preprocessing import get_context_block
from app.schemas.conversation import ChatMessage
from app.services.errors import SessionNotFoundError
from app.services.llm_client import LLMResult
from app.services.llm_service import ContextOverrides, _history_that_fits
from app.state.sessions import ConversationHistory, InMemorySessionStore
from tests.llm_fakes import estimation_turn

FORM = {
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}
TRANSCRIPT = "Quiero una aplicación web de reservas para un centro deportivo con login y pagos."


def _pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 18 Tf 40 120 Td ({safe}) Tj ET\n".encode("latin-1")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n",
        f"4 0 obj<< /Length {len(content)} >>stream\n".encode() + content + b"endstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    cursor = len(header)
    for obj in objects:
        offsets.append(cursor)
        body += obj
        cursor += len(obj)
    xref = [b"xref\n", f"0 {len(offsets) + 1}\n".encode(), b"0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode())
    trailer = f"trailer<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n".encode()
    return header + body + b"".join(xref) + trailer


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


def _install(monkeypatch, complete):
    monkeypatch.setattr("app.services.llm_client.complete", complete)


def _message_text(message: dict) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content if isinstance(block, dict))


def test_window_keeps_system_prompt_and_drops_oldest_pairs() -> None:
    history = ConversationHistory(max_turns=6)
    for index in range(8):
        history.append(f"user-{index}", f"assistant-{index}", "estimation")
    listed = history.to_messages_list("system regenerado")
    assert listed[0] == {"role": "system", "content": "system regenerado"}
    assert len([item for item in listed if item["role"] != "system"]) == 12
    assert listed[1]["content"] == "user-2"


def test_two_turns_update_project_metadata(client, monkeypatch) -> None:
    calls: list[str] = []

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        calls.append(name)
        if name == "extract-metadata":
            scope = "incluye la app móvil" if calls.count("extract-metadata") > 1 else None
            payload = {
                "project_name": "Portal Atlas",
                "assumed_team_size": 3,
                "mentioned_technologies": ["Next.js"],
                "agreed_scope": scope,
            }
            return _result(json.dumps(payload))
        return _result(estimation_turn())

    _install(monkeypatch, complete)
    created = client.post("/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    first = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    assert first.status_code == 200
    body = first.json()
    assert body["project_metadata"]["project_name"] == "Portal Atlas"
    assert body["usage"]["input_tokens"] == 40
    assert body["usage"]["output_tokens"] == 20
    assert body["latency_ms"] == 10
    assert body["cache_hit"] is False
    assert first.json()["project_metadata"]["agreed_scope"] is None

    follow = "Añade una app móvil al alcance ya hablado del portal."
    second = client.post(f"/sessions/{session_id}/estimate", data={"transcript": follow, **FORM})
    assert second.status_code == 200
    assert second.json()["project_metadata"]["agreed_scope"] == "incluye la app móvil"
    assert second.json()["project_metadata"]["project_name"] == "Portal Atlas"


def test_pdf_attachment_changes_project_title(client, monkeypatch) -> None:
    token = "CODIGO-ADJUNTO-91"

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result(
                json.dumps(
                    {
                        "project_name": None,
                        "assumed_team_size": None,
                        "mentioned_technologies": [],
                        "agreed_scope": None,
                    }
                )
            )
        blob = "\n".join(_message_text(message) for message in messages)
        title = token if token in blob else "Sin adjunto"
        return _result(estimation_turn(project_title=title))

    _install(monkeypatch, complete)
    created = client.post("/sessions")
    session_id = created.json()["session_id"]
    plain = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    assert plain.json()["estimation_structured"]["project_title"] == "Sin adjunto"

    other = client.post("/sessions").json()["session_id"]
    with_pdf = client.post(
        f"/sessions/{other}/estimate",
        data={"transcript": TRANSCRIPT, **FORM},
        files={"attachments": ("nota.pdf", BytesIO(_pdf(token)), "application/pdf")},
    )
    assert with_pdf.status_code == 200
    assert with_pdf.json()["estimation_structured"]["project_title"] == token


def test_eight_turns_never_exceed_session_window(client, monkeypatch) -> None:
    estimate_calls: list[list[dict]] = []

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "generate-estimate":
            estimate_calls.append(messages)
            return _result(estimation_turn())
        return _result(
            json.dumps(
                {
                    "project_name": "Ventana",
                    "assumed_team_size": None,
                    "mentioned_technologies": [],
                    "agreed_scope": None,
                }
            )
        )

    _install(monkeypatch, complete)
    session_id = client.post("/sessions").json()["session_id"]
    for index in range(8):
        transcript = f"{TRANSCRIPT} Turno número {index} con un alcance distinto."
        response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": transcript, **FORM})
        assert response.status_code == 200, response.text

    assert len(estimate_calls) == 8
    for messages in estimate_calls:
        assert messages[0]["role"] == "system"
        assert "<project_metadata>" in _message_text(messages[0])
        assistants = [message for message in messages if message["role"] == "assistant"]
        assert len(assistants) <= 6
    last_assistants = [message for message in estimate_calls[-1] if message["role"] == "assistant"]
    assert len(last_assistants) == 6


def test_session_window_drops_pairs_instead_of_keeping_anchors(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_CONTEXT_WINDOW", "200")
    monkeypatch.setenv("CONTEXT_SAFETY_MARGIN_TOKENS", "0")
    monkeypatch.setenv("LLM_MAX_TOKENS", "50")
    get_settings.cache_clear()
    history = [
        ChatMessage(role="user", content="primero, el ancla que no debe sobrevivir al recorte"),
        ChatMessage(role="assistant", content="estimacion ancla", kind="estimation"),
        ChatMessage(role="user", content="ultimo turno"),
        ChatMessage(role="assistant", content="estimacion ultima", kind="estimation"),
    ]
    kept, dropped = _history_that_fits(
        TRANSCRIPT,
        get_context_block(TRANSCRIPT),
        history,
        ContextOverrides(session_window=True, prompt_version="v1"),
        "openrouter/openai/gpt-4o-mini",
    )
    assert dropped == 2
    assert kept == []


def test_store_expires_and_caps_sessions() -> None:
    clock = {"now": datetime(2026, 1, 1, 12, 0, 0)}
    store = InMemorySessionStore(ttl_minutes=30, max_active=2, now=lambda: clock["now"])
    first = store.create()
    second = store.create()
    third = store.create()
    assert store.get(second.id).id == second.id
    with pytest.raises(SessionNotFoundError):
        store.get(first.id)
    clock["now"] = clock["now"] + timedelta(minutes=31)
    with pytest.raises(SessionNotFoundError):
        store.get(third.id)


def test_too_many_attachments_is_422(client, monkeypatch) -> None:
    async def complete(*_args, **_kwargs):
        raise AssertionError("no debería llamar al modelo")

    _install(monkeypatch, complete)
    session_id = client.post("/sessions").json()["session_id"]
    files = [("attachments", (f"n{index}.pdf", b"%PDF-1.4", "application/pdf")) for index in range(6)]
    response = client.post(
        f"/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, **FORM},
        files=files,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "attachment_rejected"
