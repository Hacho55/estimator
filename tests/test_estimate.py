from app.config import get_settings
from app.state.conversations import reset_store
from tests.llm_fakes import estimation_turn

TRANSCRIPTION = (
    "En la reunión con el equipo de marketing, el cliente explicó que necesita una landing "
    "con formulario de contacto, integración con HubSpot y una sección de blog. "
    "El diseño ya existe en Figma y el plazo ideal es de cuatro semanas."
)


def test_estimate_returns_markdown_and_metadata(client, fake_llm):
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["estimation"].startswith("## Estimación:")
    assert body["model"] == "openrouter/openai/gpt-4o-mini"
    assert body["provider"] == "openai"
    assert body["gateway"] == "litellm"
    assert body["estimation_structured"]["total_hours"] == 100
    assert body["prompt_version"].startswith("v1-")
    assert "inv-2024" in body["context"]["examples"]
    assert body["token_budget"]["fits"] is True
    assert body["usage"]["input_tokens"] == 1200
    assert response.headers["x-request-id"]
    assert fake_llm["calls"][0]["name"] == "generate-estimate"


def test_short_transcription_is_rejected(client):
    response = client.post("/api/v1/estimate", json={"transcription": "demasiado corta"})
    assert response.status_code == 422


def test_repair_then_success(client, fake_llm):
    fake_llm["scripts"][:] = [
        {"content": "esto no es json", "finish_reason": "stop"},
        {"content": estimation_turn(), "finish_reason": "stop", "input_tokens": 100, "output_tokens": 50},
    ]
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200, response.text
    assert response.json()["usage"]["input_tokens"] == 1300
    assert [call["name"] for call in fake_llm["calls"]] == ["generate-estimate", "repair-estimate"]


def test_answer_is_rejected_on_single_turn(client, fake_llm):
    fake_llm["scripts"][:] = [
        {"content": estimation_turn("answer"), "finish_reason": "stop"},
        {"content": estimation_turn("answer"), "finish_reason": "stop"},
    ]
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 502
    assert response.json()["error"] == "invalid_llm_output"


def test_truncated_output(client, fake_llm):
    fake_llm["scripts"][:] = [{"content": "{", "finish_reason": "length"}]
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 502
    assert response.json()["error"] == "llm_output_truncated"


def test_budget_overflow_returns_413(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "MODEL_CONTEXT_WINDOW", 100)
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 413
    assert response.json()["error"] == "context_budget_exceeded"
    assert fake_llm["calls"] == []


def test_preview_is_development_only(client, monkeypatch):
    ok = client.post("/api/v1/estimate/preview", json={"transcription": TRANSCRIPTION})
    assert ok.status_code == 200
        assert "<project_description>" in ok.json()["messages"][-1]["content"]
    monkeypatch.setattr(get_settings(), "APP_ENV", "production")
    hidden = client.post("/api/v1/estimate/preview", json={"transcription": TRANSCRIPTION})
    assert hidden.status_code == 404


def test_context_endpoint_lists_examples_and_cache(client):
    response = client.get("/api/v1/context")
    assert response.status_code == 200
    body = response.json()
    assert "web-2017" not in body["examples_included"]
    assert any(item["id"] == "web-2017" for item in body["examples_excluded"])
    assert body["prompt_cache_strategy"] == "implicit"
    assert body["text"] is None
    with_text = client.get("/api/v1/context", params={"include_text": True})
    assert "ESTIMACIÓN DE REFERENCIA" in with_text.json()["text"]


def test_llm_failure_is_a_service_error(client, monkeypatch):
    from app.services.errors import LLMUnavailableError

    async def explode(*args, **kwargs):
        raise LLMUnavailableError("proxy caído")

    monkeypatch.setattr("app.services.llm_client.complete", explode)
    reset_store()
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 502
    assert response.json()["error"] == "llm_unavailable"
