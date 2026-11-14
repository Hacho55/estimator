"""Herramientas (fase 1): política de activación, clientes HTTP, bucle de investigación e integración."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import get_settings
from app.schemas.tiers import Tier
from app.services import llm_client
from app.services.llm_client import LLMResult, ToolCall, ToolTurn
from app.services.research import run_research
from app.services.tool_policy import offered_tools
from app.tiers import TIER_CONFIG
from app.tools import net
from app.tools.base import ToolError
from app.tools.similar_projects import SimilarProjects
from app.tools.web_search import WebSearch
from tests.llm_fakes import tier_turn

FORM = {"project_type": "web_saas", "detail_level": "medium"}
TRANSCRIPT = "Queremos migrar el backend de Node a Bun, versión 1.2, y estimar la curva de aprendizaje del equipo."
INTERNAL = "i" * 40


def _result(content: str = "", ms: float = 5) -> LLMResult:
    return LLMResult(
        content=content,
        model="openrouter/openai/gpt-4o-mini",
        finish_reason="stop",
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        cached_tokens=0,
        cache_creation_tokens=None,
        cost_usd=0.01,
        latency_ms=ms,
    )


@pytest.fixture
def tools_on(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("BUSINESS_BACKEND_URL", "http://business.test")
    monkeypatch.setenv("BUSINESS_BACKEND_TOKEN", INTERNAL)
    get_settings.cache_clear()
    yield
    net.transport = None


def _mock(handler):
    net.transport = httpx.MockTransport(handler)


# --- Política -------------------------------------------------------------------


def test_policy_needs_tier_availability_and_a_reason(tools_on, monkeypatch) -> None:
    developer = TIER_CONFIG[Tier.DEVELOPER]
    pm = TIER_CONFIG[Tier.PM]
    assert offered_tools(developer, transcript=TRANSCRIPT, has_prior_turns=False, has_attachments=False) == [
        "web_search",
        "find_similar_projects",
    ]
    assert offered_tools(pm, transcript=TRANSCRIPT, has_prior_turns=False, has_attachments=False) == ["find_similar_projects"]
    # Sin señal temporal no se busca en la web; un ajuste corto no pide histórico.
    assert offered_tools(developer, transcript="Quita el blog.", has_prior_turns=True, has_attachments=False) == []
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("TOOLS_SIMILAR_PROJECTS", "false")
    get_settings.cache_clear()
    assert offered_tools(developer, transcript=TRANSCRIPT, has_prior_turns=False, has_attachments=False) == []


# --- Clientes HTTP ----------------------------------------------------------------


def test_web_search_calls_tavily_and_trims_snippets(tools_on) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"title": "Bun 1.2", "url": "https://bun.sh", "content": "x" * 900}]})

    _mock(handler)
    content = json.loads(asyncio.run(WebSearch().run({"query": "Bun 1.2 compatibilidad Node", "max_results": 9})))
    assert seen["auth"] == "Bearer tvly-test" and seen["body"]["max_results"] == 5
    assert len(content["results"][0]["snippet"]) == 500


def test_web_search_errors_are_tool_errors(tools_on) -> None:
    _mock(lambda request: httpx.Response(500))
    with pytest.raises(ToolError):
        asyncio.run(WebSearch().run({"query": "algo"}))
    with pytest.raises(ToolError):
        asyncio.run(WebSearch().run({"query": " "}))


def test_similar_projects_talks_to_the_real_backend_over_http(tools_on, monkeypatch, tmp_path) -> None:
    from business_backend import db
    from business_backend.config import get_business_settings
    from business_backend.main import app as business_app

    monkeypatch.setenv("BUSINESS_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("AI_SERVICE_SECRET", "k" * 40)
    monkeypatch.setenv("BUSINESS_INTERNAL_TOKEN", INTERNAL)
    get_business_settings.cache_clear()
    db.init_db(str(tmp_path / "b.db"))
    net.transport = httpx.ASGITransport(app=business_app)
    content = asyncio.run(
        SimilarProjects().run({"technologies": ["Next.js", "Stripe"], "scope_summary": "reservas con pagos", "team_size": 2})
    )
    projects = json.loads(content)["projects"]
    assert projects[0]["project_id"] == "p-102" and "client_name" not in projects[0]
    get_business_settings.cache_clear()


# --- Bucle de investigación -------------------------------------------------------


def _script_tools(monkeypatch, turns: list[list[ToolCall]]):
    calls = []

    async def complete_with_tools(messages, tools, *, name, model=None, max_tokens=800):
        calls.append({"name": name, "messages": [dict(m) for m in messages], "tools": [t["function"]["name"] for t in tools]})
        planned = turns[len(calls) - 1] if len(calls) <= len(turns) else []
        assistant = {"role": "assistant", "content": ""}
        if planned:
            assistant["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in planned]
        return ToolTurn(result=_result("listo"), tool_calls=planned, assistant_message=assistant)

    monkeypatch.setattr(llm_client, "complete_with_tools", complete_with_tools)
    return calls


def test_research_runs_tools_and_answers_the_model(tools_on, monkeypatch) -> None:
    _mock(lambda request: httpx.Response(200, json={"results": [{"title": "T", "url": "u", "content": "Bun 1.2 estable"}]}))
    calls = _script_tools(monkeypatch, [[ToolCall(id="c1", name="web_search", arguments='{"query": "Bun 1.2"}')]])
    outcome = asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert [r.tool for r in outcome.results] == ["web_search"] and outcome.results[0].ok
    assert outcome.calls == 2
    tool_message = calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1" and "Bun 1.2 estable" in tool_message["content"]


def test_research_turns_failures_into_results(tools_on, monkeypatch) -> None:
    _mock(lambda request: httpx.Response(503))
    _script_tools(
        monkeypatch,
        [[
            ToolCall(id="a", name="web_search", arguments="no-json"),
            ToolCall(id="b", name="web_search", arguments='{"query": "x"}'),
            ToolCall(id="c", name="find_similar_projects", arguments='{"technologies": [], "scope_summary": "algo"}'),
        ]],
    )
    outcome = asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert [r.ok for r in outcome.results] == [False, False]  # La no ofrecida no se ejecuta ni cuenta.


def test_research_respects_the_call_cap(tools_on, monkeypatch) -> None:
    monkeypatch.setenv("TOOLS_MAX_CALLS", "2")
    get_settings.cache_clear()
    _mock(lambda request: httpx.Response(200, json={"results": []}))
    many = [ToolCall(id=str(i), name="web_search", arguments='{"query": "q"}') for i in range(5)]
    _script_tools(monkeypatch, [many, many])
    outcome = asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert len(outcome.results) == 2 and outcome.calls == 1


def test_slow_tool_times_out(tools_on, monkeypatch) -> None:
    monkeypatch.setenv("TOOL_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()

    async def slow(self, args):
        await asyncio.sleep(1)
        return "tarde"

    monkeypatch.setattr(WebSearch, "run", slow)
    _script_tools(monkeypatch, [[ToolCall(id="a", name="web_search", arguments='{"query": "q"}')]])
    outcome = asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert outcome.results[0].error == "tiempo agotado"


# --- Integración en la sesión -------------------------------------------------------


def _install_actor(monkeypatch, store: list):
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        store.append((name, messages))
        if name == "extract-metadata":
            return _result("{}")
        return _result(tier_turn("developer"))

    monkeypatch.setattr("app.services.llm_client.complete", complete)


def test_tool_results_reach_the_actor_as_data_and_are_not_cached(client, tools_on, monkeypatch) -> None:
    _mock(lambda request: httpx.Response(200, json={"results": [{"title": "T", "url": "u", "content": "RESULTADO-WEB-42"}]}))
    _script_tools(monkeypatch, [[ToolCall(id="a", name="web_search", arguments='{"query": "Bun 1.2"}')]])
    calls: list = []
    _install_actor(monkeypatch, calls)
    session_id = client.post("/sessions").json()["session_id"]
    body = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM}).json()
    actor_user = calls[0][1][-1]["content"]
    assert '<result tool="web_search">' in actor_user and "RESULTADO-WEB-42" in actor_user
    assert body["tools"][0]["tool"] == "web_search" and body["tools"][0]["ok"] is True
    assert body["usage"]["input_tokens"] == 20 * 4  # Dos de investigación, actor y extractor.

    _script_tools(monkeypatch, [[ToolCall(id="a", name="web_search", arguments='{"query": "Bun 1.2"}')]])
    other = client.post("/sessions").json()["session_id"]
    again = client.post(f"/sessions/{other}/estimate", data={"transcript": TRANSCRIPT, **FORM}).json()
    assert again["cache_hit"] is False


def test_failed_research_does_not_break_the_estimate(client, tools_on, monkeypatch) -> None:
    from app.services.errors import LLMUnavailableError

    async def broken(*args, **kwargs):
        raise LLMUnavailableError("caído")

    monkeypatch.setattr(llm_client, "complete_with_tools", broken)
    calls: list = []
    _install_actor(monkeypatch, calls)
    session_id = client.post("/sessions").json()["session_id"]
    response = client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    assert response.status_code == 200 and response.json()["tools"] == []


def test_no_tools_offered_means_no_research_call(client, monkeypatch) -> None:
    called = []

    async def complete_with_tools(*args, **kwargs):
        called.append(1)

    monkeypatch.setattr(llm_client, "complete_with_tools", complete_with_tools)
    _install_actor(monkeypatch, [])
    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/estimate", data={"transcript": TRANSCRIPT, **FORM})
    assert called == []  # Sin claves configuradas, ninguna herramienta disponible.


def test_wrong_argument_types_and_malformed_responses_are_failed_results(tools_on, monkeypatch) -> None:
    _mock(lambda request: httpx.Response(200, json={"projects": "no-es-lista"}))
    _script_tools(
        monkeypatch,
        [[
            ToolCall(id="a", name="find_similar_projects", arguments='{"technologies": "React", "scope_summary": "algo"}'),
            ToolCall(id="b", name="find_similar_projects", arguments='{"technologies": [], "scope_summary": "algo"}'),
        ]],
    )
    outcome = asyncio.run(run_research(tools=["find_similar_projects"], transcript=TRANSCRIPT, memory=None))
    assert [r.ok for r in outcome.results] == [False, False]


def test_research_keeps_what_it_had_when_the_model_fails_later(tools_on, monkeypatch) -> None:
    from app.services.errors import LLMUnavailableError

    _mock(lambda request: httpx.Response(200, json={"results": [{"title": "T", "url": "u", "content": "ok"}]}))
    calls = {"n": 0}

    async def complete_with_tools(messages, tools, *, name, model=None, max_tokens=800):
        calls["n"] += 1
        if calls["n"] == 2:
            raise LLMUnavailableError("caído")
        planned = [ToolCall(id="a", name="web_search", arguments='{"query": "Bun"}')]
        return ToolTurn(result=_result(), tool_calls=planned, assistant_message={"role": "assistant", "content": ""})

    monkeypatch.setattr(llm_client, "complete_with_tools", complete_with_tools)
    outcome = asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert len(outcome.results) == 1 and outcome.results[0].ok and outcome.calls == 1


def test_tool_output_pii_is_redacted_before_the_next_research_call(tools_on, monkeypatch) -> None:
    _mock(lambda request: httpx.Response(200, json={"results": [{"title": "T", "url": "u", "content": "ana.garcia@cliente.es"}]}))
    calls = _script_tools(monkeypatch, [[ToolCall(id="a", name="web_search", arguments='{"query": "q"}')]])
    asyncio.run(run_research(tools=["web_search"], transcript=TRANSCRIPT, memory=None))
    assert "ana.garcia@cliente.es" not in calls[1]["messages"][-1]["content"]


def test_temporal_signal_in_an_attachment_offers_web_search(tools_on) -> None:
    developer = TIER_CONFIG[Tier.DEVELOPER]
    source = "Ver adjunto.\nEl cliente quiere comparar precios de Supabase y Firebase en 2026."
    assert "web_search" in offered_tools(developer, transcript=source, has_prior_turns=True, has_attachments=True)


def test_complete_with_tools_builds_the_payload_and_reads_tool_calls(monkeypatch) -> None:
    from types import SimpleNamespace

    sent = {}
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="web_search", arguments='{"query": "q"}'))
    parsed = SimpleNamespace(
        model="m",
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]), finish_reason="tool_calls")],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15, prompt_tokens_details=None),
    )

    class Raw:
        headers = {"x-litellm-response-cost": "0.002"}

        def parse(self):
            return parsed

    async def create(**payload):
        sent.update(payload)
        return Raw()

    monkeypatch.setattr(llm_client.client.chat.completions.with_raw_response, "create", create)
    messages = [{"role": "system", "content": "s", "segments": []}, {"role": "tool", "tool_call_id": "x", "content": "r", "kind": "z"}]
    turn = asyncio.run(llm_client.complete_with_tools(messages, [{"type": "function", "function": {"name": "web_search"}}], name="research-1"))
    assert sent["tool_choice"] == "auto" and "response_format" not in sent
    assert sent["messages"][0] == {"role": "system", "content": "s"}
    assert sent["messages"][1] == {"role": "tool", "tool_call_id": "x", "content": "r"}
    assert turn.tool_calls[0].name == "web_search" and turn.assistant_message["tool_calls"][0]["id"] == "c1"
    assert turn.result.input_tokens == 12
