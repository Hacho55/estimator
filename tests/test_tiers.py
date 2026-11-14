"""Tiers: plantilla, schema y vista por perfil; el perfil llega firmado en un JWT."""

from __future__ import annotations

import json
import time

import jwt
import pytest

from app.config import Settings, get_settings
from app.context.preprocessing import get_context_block
from app.schemas.estimation import DetailLevel
from app.schemas.tiers import DeveloperDraft, ExecutiveDraft, PmDraft, Tier
from app.security.caller import JWT_AUDIENCE
from app.services.llm_client import LLMResult
from app.services.postprocessor import validate_and_fix
from app.services.prompt_builder import build_messages, prompt_version, system_text_of
from app.services.tier_views import build_view, redact_view, to_estimation_draft
from app.tiers import TIER_CONFIG, resolve_tier_config
from tests.llm_fakes import tier_draft, tier_turn

SECRET = "s" * 40
FORM = {"project_type": "web_saas", "detail_level": "medium"}
TRANSCRIPT = "Quiero una landing con formulario conectado a HubSpot y un blog sencillo para publicar noticias."


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


def _token(tier: str = "pm", *, exp: int | None = None, aud: str = JWT_AUDIENCE, secret: str = SECRET) -> str:
    claims = {"sub": "u-1", "tier": tier, "aud": aud, "exp": exp or int(time.time()) + 600}
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture
def jwt_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AI_SERVICE_SECRET", SECRET)
    get_settings.cache_clear()


@pytest.fixture
def tier_llm(monkeypatch):
    seen: dict = {"systems": [], "formats": []}

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result("{}")
        system = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        seen["systems"].append(system)
        seen["formats"].append(response_format)
        tier = response_format["json_schema"]["name"].removesuffix("_turn")
        return _result(tier_turn(tier))

    monkeypatch.setattr("app.services.llm_client.complete", complete)
    return seen


def _auth(tier: str, sub: str = "u-1") -> dict:
    claims = {"sub": sub, "tier": tier, "aud": JWT_AUDIENCE, "exp": int(time.time()) + 600}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


def _estimate(client, tier: str | None = None, data: dict | None = None):
    headers = _auth(tier) if tier else {}
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    return client.post(
        f"/sessions/{session_id}/estimate",
        data={"transcript": TRANSCRIPT, **FORM, **(data or {})},
        headers=headers,
    )


# --- Capa 2: canal ------------------------------------------------------------


@pytest.mark.parametrize("tier", ["developer", "pm", "executive"])
def test_token_tier_selects_template_schema_and_view(client, jwt_mode, tier_llm, tier) -> None:
    response = _estimate(client, tier)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == tier
    assert body["estimation_structured"]["tier"] == tier
    assert body["prompt_version"].startswith(f"v4-{tier}-")
    assert tier_llm["formats"][0]["json_schema"]["name"] == f"{tier}_turn"
    assert f"/_audience_{tier}" not in tier_llm["systems"][0]  # Renderizado, no el nombre del parcial.


def test_tier_in_the_body_is_ignored(client, jwt_mode, tier_llm) -> None:
    response = _estimate(client, "developer", data={"tier": "executive"})
    assert response.json()["tier"] == "developer"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer not-a-token",
        f"Bearer {_token('pm', exp=int(time.time()) - 10)}",
        f"Bearer {_token('pm', aud='otro-servicio')}",
        f"Bearer {_token('pm', secret='x' * 40)}",
        f"Bearer {_token('ceo')}",
    ],
)
def test_bad_tokens_are_401(client, jwt_mode, tier_llm, header) -> None:
    session_id = client.post("/sessions", headers=_auth("pm")).json()["session_id"]
    headers = {"Authorization": header} if header else {}
    response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_auth_off_uses_the_default_tier(client, tier_llm, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_TIER", "executive")
    get_settings.cache_clear()
    assert _estimate(client).json()["tier"] == "executive"


def test_jwt_mode_requires_a_long_secret(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AI_SERVICE_SECRET", "corto")
    with pytest.raises(ValueError, match="32 caracteres"):
        Settings()
    monkeypatch.setenv("AUTH_MODE", "off")  # El teardown vuelve a construir Settings.


def test_tier_change_is_audited(client, jwt_mode, tier_llm) -> None:
    session_id = client.post("/sessions", headers=_auth("developer")).json()["session_id"]
    url = f"/sessions/{session_id}/estimate"
    client.post(url, data={"transcript": TRANSCRIPT, **FORM}, headers={"Authorization": f"Bearer {_token('developer')}"})
    second = client.post(url, data={"transcript": "Dámelo para dirección.", **FORM}, headers={"Authorization": f"Bearer {_token('executive')}"})
    changes = second.json()["memory_changes"]
    assert {"field": "tier", "before": "developer", "after": "executive", "turn": 2} == {
        key: changes[0][key] for key in ("field", "before", "after", "turn")
    }


# --- Capa 3: plantillas -------------------------------------------------------


def _tier_system(tier: Tier) -> str:
    block = get_context_block(TRANSCRIPT)
    messages = build_messages(TRANSCRIPT, block, prompt_label="v4", system_template=TIER_CONFIG[tier].template)
    return system_text_of(messages)


def test_templates_share_partials_and_differ_in_audience_and_contract() -> None:
    systems = {tier: _tier_system(tier) for tier in Tier}
    rates = {system[system.index("<rates>") : system.index("</rates>")] for system in systems.values()}
    assert len(rates) == 1  # Mismo parcial de tarifas en los tres.
    assert "componente técnico" in systems[Tier.DEVELOPER]
    assert "milestones" in systems[Tier.PM]
    assert "top_three_risks" in systems[Tier.EXECUTIVE]
    assert "milestones" not in systems[Tier.EXECUTIVE]
    order = ["<role_and_task>", "<audience>", "<output_format>", "<rates>", "<examples>", "<scope>", "<constraints>"]
    for system in systems.values():
        positions = [system.index(tag) for tag in order]
        assert positions == sorted(positions)
    versions = {prompt_version(system, f"v4-{tier.value}") for tier, system in systems.items()}
    assert len(versions) == 3


# --- Vistas -------------------------------------------------------------------


def _view(tier: str, **overrides):
    draft = resolve_tier_config(tier).turn_schema.model_fields["estimation"].annotation.__args__[0](
        **tier_draft(tier, **overrides)
    )
    estimation, _ = validate_and_fix(to_estimation_draft(draft))
    return build_view(tier, draft, estimation), estimation


def test_rates_and_totals_come_from_the_rate_card_not_the_model() -> None:
    _view_, estimation = _view("developer")
    assert [task.hours for task in estimation.tasks] == [40, 30, 20, 10]  # 42 → 40.
    assert estimation.tasks[0].rate_eur == 55 and estimation.tasks[0].cost_eur == 2200
    assert estimation.total_cost_eur == 2200 + 1800 + 1100 + 450


def test_developer_view_groups_by_component_with_hours_range() -> None:
    view, estimation = _view("developer")
    frontend = next(component for component in view.components if component.name == "Frontend")
    assert frontend.hours == 60 and frontend.technical_risks == ["SSR del blog"]
    # 2 fuentes de incertidumbre: margen del 20 %.
    assert (view.total_hours_range.min, view.total_hours_range.max) == (80, 120)


def test_pm_view_has_phases_and_milestone_costs() -> None:
    view, _ = _view("pm")
    assert [phase.name for phase in view.phases] == ["Desarrollo", "QA"]
    assert view.milestones[0].cumulative_cost_eur == 2200 + 1800 + 1100
    assert view.milestones[1].cumulative_cost_eur == 5550
    assert view.blockers == ["Acceso a la cuenta de HubSpot"]


def test_executive_view_ranges_follow_confidence() -> None:
    medium, _ = _view("executive")
    low, _ = _view("executive", confidence_level="low")
    assert (medium.headline_cost_range.min, medium.headline_cost_range.max) == (4000, 7000)
    assert low.headline_cost_range.max - low.headline_cost_range.min > (
        medium.headline_cost_range.max - medium.headline_cost_range.min
    )
    assert not hasattr(medium, "components")


def test_executive_draft_needs_exactly_three_risks() -> None:
    with pytest.raises(ValueError, match="tres riesgos"):
        ExecutiveDraft(**tier_draft("executive", top_three_risks=["uno", "dos"]))
    ExecutiveDraft(**tier_draft("executive", is_estimable=False, top_three_risks=[], go_no_go_recommendation=""))


def test_view_redaction_covers_tier_only_fields() -> None:
    view, _ = _view("pm", blockers=["Escribir a ana@cliente.es"])
    redacted = redact_view(view, lambda text: text.replace("ana@cliente.es", "<EMAIL>"))
    assert redacted.blockers == ["Escribir a <EMAIL>"]
    assert redacted.tier == "pm"


def test_cached_turn_is_reparsed_as_the_tier_draft(client, jwt_mode, tier_llm) -> None:
    first = _estimate(client, "developer")
    second = _estimate(client, "developer")  # Otra sesión, mismo primer turno: caché exacta (sin revisión).
    assert second.json()["cache_hit"] is True
    assert second.json()["estimation_structured"] == first.json()["estimation_structured"]


def test_pm_and_developer_drafts_are_distinct_contracts() -> None:
    assert "milestones" in PmDraft.model_json_schema()["properties"]
    assert "milestones" not in DeveloperDraft.model_json_schema()["properties"]
    assert json.loads(tier_turn("developer"))["estimation"]["components"]
    assert DetailLevel.MEDIUM.value == "medium"


def test_session_belongs_to_its_creator(client, jwt_mode, tier_llm) -> None:
    session_id = client.post("/sessions", headers=_auth("pm", sub="ana")).json()["session_id"]
    url = f"/sessions/{session_id}/estimate"
    other = client.post(url, data={"transcript": TRANSCRIPT, **FORM}, headers=_auth("pm", sub="luis"))
    assert other.status_code == 404  # Igual que una sesión inexistente.
    own = client.post(url, data={"transcript": TRANSCRIPT, **FORM}, headers=_auth("pm", sub="ana"))
    assert own.status_code == 200


def test_creating_a_session_needs_a_token_in_jwt_mode(client, jwt_mode) -> None:
    from app.state.sessions import get_session_store

    response = client.post("/sessions")
    assert response.status_code == 401
    assert get_session_store()._items == {}


def test_history_and_cache_store_the_redacted_draft(client, jwt_mode, monkeypatch) -> None:
    from app.state.sessions import get_session_store

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result("{}")
        return _result(tier_turn("pm", blockers=["Pedir acceso a ana.garcia@cliente.es"]))

    monkeypatch.setattr("app.services.llm_client.complete", complete)
    headers = _auth("pm")
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM}, headers=headers)
    assert "ana.garcia@cliente.es" not in response.text
    stored = get_session_store().get(session_id, owner="u-1").history.messages[-1].content
    assert "ana.garcia@cliente.es" not in stored
    assert json.loads(stored)["estimation"]["blockers"]


def test_milestone_with_unknown_phase_is_rejected_and_variants_match() -> None:
    with pytest.raises(ValueError, match="Hitos con fases"):
        PmDraft(**tier_draft("pm", milestones=[{"name": "X", "phase": "Marketing", "deliverable": "Y"}]))
    draft = PmDraft(**tier_draft("pm", milestones=[{"name": "X", "phase": "  desarrollo ", "deliverable": "Y"}]))
    estimation, _ = validate_and_fix(to_estimation_draft(draft))
    assert build_view("pm", draft, estimation).milestones[0].cumulative_cost_eur == 5100


def test_a_draft_from_another_tier_does_not_pass() -> None:
    from app.services.errors import InvalidLLMOutputError
    from app.services.tier_views import parse_tier_turn

    with pytest.raises(InvalidLLMOutputError):
        parse_tier_turn(tier_turn("developer"), "pm")
    with pytest.raises(InvalidLLMOutputError):
        parse_tier_turn(tier_turn("pm"), "developer")


def test_not_estimable_pm_draft_with_milestones_does_not_break(client, jwt_mode, monkeypatch) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result("{}")
        return _result(
            tier_turn(
                "pm",
                is_estimable=False,
                not_estimable_reason="No es un proyecto de software.",
                milestones=[{"name": "X", "phase": "Marketing", "deliverable": "Y"}],
            )
        )

    monkeypatch.setattr("app.services.llm_client.complete", complete)
    headers = _auth("pm")
    session_id = client.post("/sessions", headers=headers).json()["session_id"]
    response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM}, headers=headers)
    assert response.status_code == 200, response.text
    view = response.json()["estimation_structured"]
    assert view["is_estimable"] is False and view["milestones"] == []
