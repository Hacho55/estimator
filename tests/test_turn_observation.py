"""Observación por turno (``turn_observed``): un evento con todo lo que el stress test necesita."""

from __future__ import annotations

import json
from io import BytesIO

from structlog.testing import capture_logs

from app.config import get_settings
from app.observability import configure_call_logs
from tests.llm_fakes import tier_turn
from tests.test_sessions import FORM, TRANSCRIPT, _install, _pdf, _result

FIELDS = {
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "cache_hit_kind",
    "last_resolved_tier",
}


def _fake(monkeypatch) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            payload = {"set_project_name": "Portal Atlas", "set_team_size": 3, "add_technologies": ["Next.js"]}
            return _result(json.dumps(payload))
        return _result(tier_turn("developer"))

    _install(monkeypatch, complete)


def test_every_session_turn_emits_turn_observed_with_the_exercise_fields(client, monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    configure_call_logs()
    _fake(monkeypatch)
    session_id = client.post("/sessions").json()["session_id"]
    url = f"/sessions/{session_id}/estimate"

    with capture_logs() as logs:
        first = client.post(url, data={"transcript": TRANSCRIPT, **FORM}).json()["observation"]
        second = client.post(url, data={"transcript": "Ajusta el QA del portal.", **FORM}).json()["observation"]

    events = [entry for entry in logs if entry["event"] == "turn_observed"]
    assert len(events) == 2
    assert FIELDS <= set(events[0])
    assert events[1]["turn_index"] == 2 and events[1]["session_id"] == session_id

    assert first["turn_index"] == 1
    assert first["messages_in_window"] == 0
    assert first["summary_chars"] == 0  # El primer prompt no lleva memoria.
    assert first["anchors_count"] == 3  # Nombre, equipo y una tecnología, ya fusionados.
    assert first["tokens_in"] == 40 and first["tokens_out"] == 20  # Actor + extractor.
    assert first["latency_ms"] == 10 and first["estimate_ms"] == 5 and first["extractor_ms"] == 5
    assert first["enriched_transcript_chars"] == len(TRANSCRIPT)
    assert first["attachments_total_chars"] == 0
    assert first["last_resolved_tier"] == "developer"

    assert second["messages_in_window"] == 2  # El par del turno 1.
    assert second["summary_chars"] > 0  # La memoria viajó en el system.


def test_observation_reports_truncated_attachment_chars(client, monkeypatch) -> None:
    monkeypatch.setenv("ATTACHMENT_MAX_CHARS", "60")
    get_settings.cache_clear()
    _fake(monkeypatch)
    session_id = client.post("/sessions").json()["session_id"]
    body = client.post(
        f"/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, **FORM},
        files={"attachments": ("largo.pdf", BytesIO(_pdf("palabra " * 40)), "application/pdf")},
    ).json()
    observation = body["observation"]
    assert observation["attachments_truncated"] is True
    assert observation["attachments_total_chars"] == body["attachments"][0]["chars"] <= 60
    assert observation["enriched_transcript_chars"] == len(TRANSCRIPT) + observation["attachments_total_chars"]


def test_repeated_first_turn_is_observed_as_exact_cache_hit(client, monkeypatch) -> None:
    _fake(monkeypatch)
    observed = []
    for _ in range(2):
        session_id = client.post("/sessions").json()["session_id"]
        response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
        observed.append(response.json()["observation"])
    assert [item["cache_hit_kind"] for item in observed] == ["none", "exact"]
    assert [item["actor_calls"] for item in observed] == [1, 0]  # El acierto no llama al actor.


def test_unknown_price_is_observed_as_unknown_not_free(client, monkeypatch) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        content = json.dumps({}) if name == "extract-metadata" else tier_turn("developer")
        result = _result(content)
        result.cost_usd = None
        return result

    _install(monkeypatch, complete)
    session_id = client.post("/sessions").json()["session_id"]
    body = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM}).json()
    assert body["cost_usd"] is None
    assert body["observation"]["cost_usd"] is None
