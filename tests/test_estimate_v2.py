"""Contrato del formulario tipado (/api/v2) y versión de prompt por petición."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.context.preprocessing import get_context_block
from app.schemas.estimation import (
    DetailLevel,
    DraftTask,
    DraftTeamMember,
    FormEstimationRequest,
    OutputFormat,
    ProjectType,
)
from app.services.prompt_builder import build_messages, prompt_version, system_text_of
from app.state.response_cache import cache_key
from tests.llm_fakes import estimation_turn

DESCRIPTION = (
    "En la reunión con el equipo de marketing, el cliente explicó que necesita una landing "
    "con formulario de contacto, integración con HubSpot y una sección de blog. "
    "El diseño ya existe en Figma y el plazo ideal es de cuatro semanas."
)

FORM = {
    "description": DESCRIPTION,
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}


def test_form_request_rejects_unknown_enum():
    with pytest.raises(ValidationError):
        FormEstimationRequest(**{**FORM, "project_type": "erp"})


def test_form_request_rejects_short_description():
    with pytest.raises(ValidationError):
        FormEstimationRequest(**{**FORM, "description": "corta"})


def test_draft_task_rejects_negative_hours():
    with pytest.raises(ValidationError):
        DraftTask(name="x", phase="Frontend", role="frontend", hours=-1, rate_eur=55, cost_eur=0)


def test_draft_task_rejects_dedication_above_one():
    with pytest.raises(ValidationError):
        DraftTeamMember(role="frontend", count=1, dedication=1.5)


def test_list_prompt_versions(client):
    response = client.get("/api/v2/prompts")
    assert response.status_code == 200
    body = response.json()
    assert "v1" in body["versions"]
    assert "v2" in body["versions"]
    assert body["default"] == "v1"


def test_estimate_v2_returns_prompt_version_and_phases(client, fake_llm):
    response = client.post("/api/v2/estimate", json=FORM)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt_version"].startswith("v1-")
    phases = body["estimation_structured"]["phases"]
    assert phases
    assert {p["phase"] for p in phases} == {"Frontend", "Backend", "Calidad"}
    frontend = next(p for p in phases if p["phase"] == "Frontend")
    assert frontend["hours"] == 60
    assert frontend["cost_eur"] == 3300
    user = fake_llm["calls"][0]["messages"][-1]["content"]
    assert "<project_type>web saas</project_type>" in user
    assert "<project_description>" in user


def test_estimate_v2_with_v2_prompt(client, fake_llm):
    response = client.post("/api/v2/estimate", params={"prompt_version": "v2"}, json=FORM)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt_version"].startswith("v2-")
    system = fake_llm["calls"][0]["messages"][0]["content"]
    if isinstance(system, list):
        system = " ".join(part.get("text", "") for part in system)
    assert "nombra el ejemplo de referencia" in system.lower() or "ejemplo de referencia" in system.lower()


def test_unknown_prompt_version_is_422(client):
    response = client.post("/api/v2/estimate", params={"prompt_version": "v999"}, json=FORM)
    assert response.status_code == 422
    assert "v999" in response.text


def test_v1_and_v2_have_distinct_hashes_and_cache_keys():
    block = get_context_block(DESCRIPTION)
    settings = get_settings()
    messages_v1 = build_messages(
        DESCRIPTION,
        block,
        prompt_label="v1",
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        project_type=ProjectType.WEB_SAAS,
    )
    messages_v2 = build_messages(
        DESCRIPTION,
        block,
        prompt_label="v2",
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
        project_type=ProjectType.WEB_SAAS,
    )
    version_v1 = prompt_version(system_text_of(messages_v1), "v1")
    version_v2 = prompt_version(system_text_of(messages_v2), "v2")
    assert version_v1 != version_v2
    assert version_v1.startswith("v1-")
    assert version_v2.startswith("v2-")
    key_v1 = cache_key(
        transcription=DESCRIPTION,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        prompt_version=version_v1,
        history=[],
    )
    key_v2 = cache_key(
        transcription=DESCRIPTION,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        prompt_version=version_v2,
        history=[],
    )
    assert key_v1 != key_v2


def test_conversation_v2_reuses_form_options(client, fake_llm):
    created = client.post(
        "/api/v2/conversations",
        params={"prompt_version": "v2"},
        json={**FORM, "detail_level": "detailed", "output_format": "narrative"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["prompt_version"].startswith("v2-")
    conversation_id = created.json()["conversation_id"]

    fake_llm["scripts"].append({"content": estimation_turn(), "finish_reason": "stop"})
    follow = client.post(
        f"/api/v2/conversations/{conversation_id}/messages",
        json={"message": "¿Puedes bajar un poco las horas de frontend?"},
    )
    assert follow.status_code == 200, follow.text
    assert follow.json()["prompt_version"].startswith("v2-")
    second_system = fake_llm["calls"][1]["messages"][0]["content"]
    if isinstance(second_system, list):
        second_system = " ".join(part.get("text", "") for part in second_system)
    assert "narrative" in second_system.lower() or "relato" in second_system.lower()


def test_v1_estimate_still_accepts_transcription(client, fake_llm):
    response = client.post("/api/v1/estimate", json={"transcription": DESCRIPTION})
    assert response.status_code == 200
    assert response.json()["prompt_version"].startswith("v1-")
