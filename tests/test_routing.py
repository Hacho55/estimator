"""Sesión 3 · routing: apagado por defecto, y conservador cuando se enciende."""

from app.config import get_settings
from app.services.llm_service import choose_model

PRIMARY = "openrouter/openai/gpt-4o-mini"
RESERVE = "openrouter/anthropic/claude-haiku-4.5"
SHORT = "Queremos una página sencilla con un panel para presentar el estudio. El texto ya está escrito."
SEVERAL = "Necesitamos login, pagos por API y un panel de usuarios para el equipo interno."


def _routing(monkeypatch, mode: str, reserve: str = RESERVE) -> None:
    monkeypatch.setenv("LLM_ROUTING", mode)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", reserve)
    get_settings.cache_clear()


def test_off_keeps_the_strong_model(monkeypatch):
    _routing(monkeypatch, "off")
    assert choose_model(SHORT, has_history=False) == PRIMARY


def test_short_text_with_one_requirement_uses_the_cheap_model(monkeypatch):
    _routing(monkeypatch, "complexity")
    assert choose_model(SHORT, has_history=False) == RESERVE


def test_several_requirements_stay_on_the_strong_model(monkeypatch):
    _routing(monkeypatch, "complexity")
    assert choose_model(SEVERAL, has_history=False) == PRIMARY
    assert choose_model(SHORT * 8, has_history=False) == PRIMARY
    assert choose_model(SHORT, has_history=True) == PRIMARY


def test_complexity_without_a_cheap_model_stays_put(monkeypatch):
    _routing(monkeypatch, "complexity", reserve="")
    assert choose_model(SHORT, has_history=False) == PRIMARY


def test_estimate_uses_the_routed_model(client, fake_llm, monkeypatch):
    _routing(monkeypatch, "complexity")
    response = client.post("/api/v1/estimate", json={"transcription": SHORT})
    assert response.status_code == 200, response.text
    assert fake_llm["calls"][0]["model"] == RESERVE
    assert response.json()["model"] == RESERVE
