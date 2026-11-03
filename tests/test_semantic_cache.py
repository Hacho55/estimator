"""Caché semántica: índice en memoria y embedding falso. Sin Redis en el job principal."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.state.conversations import reset_store
from app.state.semantic_cache import (
    bucket_id,
    lookup_semantic,
    reset_semantic_cache,
    store_semantic,
)
from tests.llm_fakes import estimation_turn
from tests.test_estimate_v2 import FORM


def _vector(seed: float) -> list[float]:
    return [seed, 1.0 - seed, 0.25, 0.5]


@pytest.fixture(autouse=True)
def _semantic_ready(monkeypatch):
    reset_store()
    reset_semantic_cache()
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "log")
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_THRESHOLD", 0.9)
    monkeypatch.setattr(get_settings(), "REDIS_URL", "")

    async def fake_embed(text: str, *, model: str | None = None):
        # Determinista: el primer carácter mueve un poco el vector.
        return _vector(0.1 if "HubSpot" in text else 0.95)

    monkeypatch.setattr("app.services.llm_client.embed", fake_embed)
    yield
    reset_semantic_cache()


def test_memory_hit_same_bucket_similar_text():
    bucket = bucket_id(
        prompt_version="v1-abc",
        project_type="web_saas",
        detail_level="medium",
        output_format="phases_table",
        model="m",
    )
    store_semantic(
        bucket=bucket,
        vector=_vector(0.1),
        prompt="a",
        content='{"kind":"estimation"}',
        model="m",
        finish_reason="stop",
    )
    hit = lookup_semantic(bucket=bucket, vector=_vector(0.11))
    assert hit is not None
    assert hit.similarity >= 0.9


def test_other_format_bucket_misses():
    store_semantic(
        bucket=bucket_id(
            prompt_version="v1-abc",
            project_type="web_saas",
            detail_level="medium",
            output_format="phases_table",
            model="m",
        ),
        vector=_vector(0.1),
        prompt="a",
        content="x",
        model="m",
        finish_reason="stop",
    )
    hit = lookup_semantic(
        bucket=bucket_id(
            prompt_version="v1-abc",
            project_type="web_saas",
            detail_level="medium",
            output_format="narrative",
            model="m",
        ),
        vector=_vector(0.1),
    )
    assert hit is None


def test_log_mode_still_calls_model(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "log")
    response = client.post("/api/v2/estimate", json=FORM)
    assert response.status_code == 200
    assert fake_llm["calls"]
    assert response.json()["cache_hit"] is False


def test_on_mode_serves_semantic_hit(client, fake_llm, monkeypatch):
    from app.state.response_cache import clear_response_cache

    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "on")
    first = client.post("/api/v2/estimate", json=FORM)
    assert first.status_code == 200
    assert fake_llm["calls"]
    calls_after_first = len(fake_llm["calls"])
    # Quita la caché exacta para forzar la vía semántica con el mismo vector.
    clear_response_cache()

    second = client.post("/api/v2/estimate", json=FORM)
    assert second.status_code == 200
    body = second.json()
    assert body["cache_hit"] is True
    assert body["cache_kind"] == "semantic"
    assert body["cache_similarity"] is not None
    assert len(fake_llm["calls"]) == calls_after_first


def test_below_threshold_does_not_hit():
    store_semantic(
        bucket="b",
        vector=_vector(0.0),
        prompt="a",
        content="x",
        model="m",
        finish_reason="stop",
    )
    hit = lookup_semantic(bucket="b", vector=_vector(1.0))
    assert hit is None or hit.similarity < 0.9


def test_not_estimable_is_not_written(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "on")
    fake_llm["scripts"][:] = [
        {
            "content": estimation_turn(
                is_estimable=False,
                not_estimable_reason="Campaña de prensa",
                tasks=[],
                total_hours=0,
                total_cost_eur=0,
            ),
            "finish_reason": "stop",
        }
    ]
    response = client.post("/api/v2/estimate", json=FORM)
    assert response.status_code == 200
    assert lookup_semantic(
        bucket=bucket_id(
            prompt_version=response.json()["prompt_version"],
            project_type="web_saas",
            detail_level="medium",
            output_format="phases_table",
            model=response.json()["model"],
        ),
        vector=_vector(0.1),
    ) is None


def test_conversation_skips_semantic(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "on")
    created = client.post("/api/v2/conversations", json=FORM)
    assert created.status_code == 200
    calls = len(fake_llm["calls"])
    follow = client.post(
        f"/api/v2/conversations/{created.json()['conversation_id']}/messages",
        json={"message": "¿Puedes bajar las horas de frontend un poco?"},
    )
    assert follow.status_code == 200
    assert len(fake_llm["calls"]) > calls


def test_embed_failure_does_not_break(client, fake_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "SEMANTIC_CACHE_MODE", "log")

    async def boom(text: str, *, model: str | None = None):
        raise RuntimeError("proxy embeddings caído")

    monkeypatch.setattr("app.services.llm_client.embed", boom)
    response = client.post("/api/v2/estimate", json=FORM)
    assert response.status_code == 200


@pytest.mark.redis
def test_redisvl_roundtrip(monkeypatch):
    settings = get_settings()
    if not settings.REDIS_URL:
        pytest.skip("REDIS_URL no configurada")
    reset_semantic_cache()
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_MODE", "on")
    bucket = "redis-test-bucket"
    vector = [0.01] * settings.EMBEDDING_DIMS
    store_semantic(
        bucket=bucket,
        vector=vector,
        prompt="demo",
        content='{"ok":true}',
        model="m",
        finish_reason="stop",
    )
    hit = lookup_semantic(bucket=bucket, vector=vector)
    assert hit is not None
