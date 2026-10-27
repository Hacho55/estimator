"""Variables de prueba antes de importar la app, y un cliente LLM falso."""

import os

os.environ["LITELLM_API_KEY"] = "test-key"
os.environ["LITELLM_BASE_URL"] = "https://example.invalid"
os.environ["APP_ENV"] = "development"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
os.environ["LLM_MODEL"] = "openrouter/openai/gpt-4o-mini"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["LOG_PROMPTS"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.llm_client import LLMResult
from app.state.response_cache import clear_response_cache
from tests.llm_fakes import estimation_turn


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    clear_response_cache()
    yield
    get_settings.cache_clear()
    clear_response_cache()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_llm(monkeypatch):
    scripts: list[dict] = [{"content": estimation_turn(), "finish_reason": "stop"}]
    calls: list[dict] = []

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        calls.append({"name": name, "messages": messages, "model": model, "metadata": metadata})
        script = scripts[min(len(calls) - 1, len(scripts) - 1)]
        return LLMResult(
            content=script["content"],
            model=model or "openrouter/openai/gpt-4o-mini",
            finish_reason=script.get("finish_reason", "stop"),
            input_tokens=script.get("input_tokens", 1200),
            output_tokens=script.get("output_tokens", 400),
            total_tokens=script.get("input_tokens", 1200) + script.get("output_tokens", 400),
            cached_tokens=script.get("cached_tokens", 0),
            cache_creation_tokens=None,
            cost_usd=script.get("cost_usd", 0.001),
            latency_ms=script.get("latency_ms", 25),
        )

    monkeypatch.setattr("app.services.llm_client.complete", complete)
    return {"scripts": scripts, "calls": calls}
