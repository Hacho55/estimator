"""Sesión 3 · caché: un acierto no vuelve a llamar al modelo."""

from app.schemas.conversation import ChatMessage
from app.state.response_cache import MAX_ENTRIES, TTL_SECONDS, cache_key, cache_size, lookup, store
from tests.test_estimate import TRANSCRIPTION


def test_same_request_is_a_hit(client, fake_llm):
    first = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    second = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["usage"]["total_tokens"] == 0
    assert second.json()["cost_usd"] == 0
    assert second.json()["latency_ms"] == 0
    assert second.json()["estimation_structured"]["total_hours"] == 100
    assert len(fake_llm["calls"]) == 1


def test_another_transcription_misses(client, fake_llm):
    client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    other = TRANSCRIPTION + " Además quieren un aviso cuando el formulario falle."
    response = client.post("/api/v1/estimate", json={"transcription": other})
    assert response.status_code == 200, response.text
    assert response.json()["cache_hit"] is False
    assert len(fake_llm["calls"]) == 2


def test_history_changes_the_key():
    shared = {
        "transcription": TRANSCRIPTION,
        "model": "openrouter/openai/gpt-4o-mini",
        "temperature": 0.2,
        "prompt_version": "v1-test",
    }
    alone = cache_key(**shared, history=[])
    with_turn = cache_key(
        **shared,
        history=[ChatMessage(role="user", content="Sube el diseño a 60 horas.")],
    )
    assert alone != with_turn
    assert cache_key(**shared, history=[]) != cache_key(**{**shared, "model": "otro"}, history=[])
    assert cache_key(**shared, history=[]) != cache_key(**{**shared, "temperature": 0.9}, history=[])
    assert cache_key(**shared, history=[]) != cache_key(**{**shared, "prompt_version": "v2"}, history=[])
    assert cache_key(**shared, history=[], project_type="web_saas") != cache_key(
        **shared, history=[], project_type="mobile_app"
    )


def test_expired_entry_is_a_miss(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("app.state.response_cache.time.monotonic", lambda: clock["now"])
    key = cache_key(
        transcription=TRANSCRIPTION,
        model="openrouter/openai/gpt-4o-mini",
        temperature=0.2,
        prompt_version="v1-test",
        history=[],
    )
    store(key, content="{}", model="openrouter/openai/gpt-4o-mini", finish_reason="stop")
    assert lookup(key) is not None
    clock["now"] = 1_000.0 + TTL_SECONDS + 1
    assert lookup(key) is None


def test_store_drops_expired_entries_and_keeps_a_bound(monkeypatch):
    clock = {"now": 1_000.0}
    monkeypatch.setattr("app.state.response_cache.time.monotonic", lambda: clock["now"])
    old = cache_key(
        transcription="vieja",
        model="m",
        temperature=0.2,
        prompt_version="v1",
        history=[],
    )
    store(old, content="{}", model="m", finish_reason="stop")
    clock["now"] = 1_000.0 + TTL_SECONDS + 1
    for index in range(MAX_ENTRIES + 3):
        store(
            cache_key(
                transcription=f"nueva {index}",
                model="m",
                temperature=0.2,
                prompt_version="v1",
                history=[],
            ),
            content="{}",
            model="m",
            finish_reason="stop",
        )
    assert lookup(old) is None
    assert cache_size() == MAX_ENTRIES
