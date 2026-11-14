"""Actor-Critic-Boss: reglas del Boss, crítico en dos capas y el bucle con tope."""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.schemas.review import CriticFeedback, ReviewIssue
from app.services.boss import Candidate, best_candidate, decide, feedback_for_actor
from app.services.llm_client import LLMResult
from tests.llm_fakes import tier_turn

FORM = {"project_type": "web_saas", "detail_level": "medium"}
TRANSCRIPT = "Quiero una landing con formulario conectado a HubSpot y un blog sencillo para publicar noticias."


def _issue(severity: str, source: str = "critic", category: str = "missing_component") -> ReviewIssue:
    return ReviewIssue(source=source, category=category, severity=severity, field_path="work", description="Falta la pasarela.")


def _result(content: str, ms: float = 5) -> LLMResult:
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


def _feedback(verdict: str, *severities: str) -> str:
    issues = [
        {"category": "missing_component", "severity": severity, "field_path": "work", "description": "Falta la pasarela de pagos."}
        for severity in severities
    ]
    return json.dumps({"verdict": verdict, "issues": issues, "confidence_in_review": 80})


# --- Boss: reglas ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "severities", "left", "expected"),
    [
        ("accept", [], 1, "accept"),
        ("accept", ["minor"], 1, "accept"),
        ("needs_iteration", ["major"], 1, "iterate"),
        ("needs_iteration", ["major"], 0, "synthesize"),
        ("reject", ["critical"], 1, "synthesize"),
        ("accept", ["major"], 1, "iterate"),  # Un bloqueante (p. ej. de una propiedad) manda sobre el veredicto.
        ("unavailable", [], 1, "accept"),
    ],
)
def test_boss_rules(verdict, severities, left, expected) -> None:
    issues = [_issue(severity) for severity in severities]
    assert decide(verdict=verdict, issues=issues, iterations_left=left) == expected


def test_best_candidate_prefers_fewer_blocking_then_the_latest() -> None:
    first = Candidate(result="a", issues=[_issue("critical")])
    second = Candidate(result="b", issues=[_issue("major"), _issue("minor")])
    third = Candidate(result="c", issues=[_issue("major")])
    assert best_candidate([first, second, third]).result == "c"


def test_actor_only_receives_blocking_issues() -> None:
    fed = feedback_for_actor([_issue("minor"), _issue("major")])
    assert [item["severity"] for item in fed] == ["major"]


def test_critic_feedback_validators() -> None:
    with pytest.raises(ValueError, match="needs_iteration"):
        CriticFeedback.model_validate_json(_feedback("needs_iteration", "minor"))
    with pytest.raises(ValueError, match="reject"):
        CriticFeedback.model_validate_json(_feedback("reject"))


# --- Bucle en la sesión -----------------------------------------------------------


class Script:
    """Respuestas del LLM falso por nombre de llamada, y registro de lo que se pidió."""

    def __init__(self, monkeypatch, *, actor=None, critic=None) -> None:
        self.calls: list[tuple[str, list[dict]]] = []
        self.actor = actor or (lambda n: tier_turn("pm"))
        self.critic = critic or (lambda n: _feedback("accept"))
        self.counts: dict[str, int] = {}

        async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
            self.calls.append((name, messages))
            kind = "critic" if name.startswith("critic") else "actor" if name in {"generate-estimate"} or name.startswith("actor") else name
            self.counts[kind] = self.counts.get(kind, 0) + 1
            if name == "extract-metadata":
                return _result("{}")
            if kind == "critic":
                return _result(self.critic(self.counts[kind]))
            return _result(self.actor(self.counts[kind]))

        monkeypatch.setattr("app.services.llm_client.complete", complete)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls if name != "extract-metadata"]


@pytest.fixture
def pm_tier(monkeypatch):
    monkeypatch.setenv("DEFAULT_TIER", "pm")
    get_settings.cache_clear()


def _turn(client, transcript: str = TRANSCRIPT):
    session_id = client.post("/sessions").json()["session_id"]
    return client.post(f"/sessions/{session_id}/estimate", data={"transcript": transcript, **FORM})


def test_accepted_first_time_costs_one_actor_and_one_critic(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch)
    body = _turn(client).json()
    assert script.names() == ["generate-estimate", "critic-1"]
    assert body["review"]["decision"] == "accept" and body["review"]["actor_calls"] == 1
    assert body["usage"]["input_tokens"] == 20 * 3  # Actor, crítico y extractor.


def test_iteration_sends_critic_feedback_to_the_actor(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("needs_iteration", "major") if n == 1 else _feedback("accept"))
    body = _turn(client).json()
    assert script.names() == ["generate-estimate", "critic-1", "actor-2", "critic-2"]
    retry_user = script.calls[2][1][-1]["content"]
    assert "<critic_feedback>" in retry_user and "Falta la pasarela de pagos." in retry_user
    assert body["review"]["decision"] == "accept"
    assert [item["decision"] for item in body["review"]["iterations"]] == ["iterate", "accept"]


def test_budget_exhausted_synthesizes_with_open_issues(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("needs_iteration", "major"))
    body = _turn(client).json()
    assert script.names() == ["generate-estimate", "critic-1", "actor-2", "critic-2"]  # Tope: 2 actores.
    assert body["review"]["decision"] == "synthesize"
    assert any(item["code"] == "review_missing_component" for item in body["warnings"])
    assert body["estimation_structured"]["tier"] == "pm"  # Se entrega igual.


def test_reject_synthesizes_without_iterating(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("reject", "critical"))
    body = _turn(client).json()
    assert script.names() == ["generate-estimate", "critic-1"]
    assert body["review"]["decision"] == "synthesize"


def test_failed_critic_accepts_with_a_notice(client, pm_tier, monkeypatch) -> None:
    Script(monkeypatch, critic=lambda n: "no es json")
    body = _turn(client).json()
    assert body["review"]["decision"] == "accept"
    assert any(item["code"] == "critic_unavailable" for item in body["warnings"])


def test_deterministic_issue_forces_iteration_even_if_the_critic_accepts(client, pm_tier, monkeypatch) -> None:
    script = Script(
        monkeypatch,
        actor=lambda n: tier_turn("pm", milestones=[]) if n == 1 else tier_turn("pm"),
        critic=lambda n: _feedback("accept"),
    )
    body = _turn(client).json()
    assert script.names() == ["generate-estimate", "critic-1", "actor-2", "critic-2"]
    first_round = body["review"]["iterations"][0]["issues"]
    assert first_round[0]["source"] == "property" and first_round[0]["category"] == "property:pm_milestones"
    assert body["estimation_structured"]["milestones"]


def test_developer_tier_has_no_review(client, monkeypatch) -> None:
    script = Script(monkeypatch, actor=lambda n: tier_turn("developer"))
    body = _turn(client).json()
    assert script.names() == ["generate-estimate"]
    assert body["review"] is None


def test_answers_are_not_reviewed(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, actor=lambda n: tier_turn("pm", kind="answer", message="Son tres personas."))
    session_id = client.post("/sessions").json()["session_id"]
    url = f"/sessions/{session_id}/estimate"
    script.actor = lambda n: tier_turn("pm")
    client.post(url, data={"transcript": TRANSCRIPT, **FORM})
    script.calls.clear()
    script.actor = lambda n: tier_turn("pm", kind="answer", message="Son tres personas.")
    body = client.post(url, data={"transcript": "¿Cuántas personas?", **FORM}).json()
    assert body["kind"] == "answer" and body["review"] is None
    assert not any(name.startswith("critic") for name in script.names())


def test_only_the_final_reviewed_output_is_cached(client, pm_tier, monkeypatch) -> None:
    script = Script(
        monkeypatch,
        actor=lambda n: tier_turn("pm", summary=f"Versión {n}."),
        critic=lambda n: _feedback("needs_iteration", "major") if n == 1 else _feedback("accept"),
    )
    first = _turn(client).json()
    assert first["estimation_structured"]["summary"] == "Versión 2."
    script.calls.clear()
    second = _turn(client).json()  # Otra sesión, mismo primer turno.
    assert second["cache_hit"] is True and second["review"] is None
    assert second["estimation_structured"]["summary"] == "Versión 2."
    assert script.names() == []


def test_acb_can_be_switched_off(client, pm_tier, monkeypatch) -> None:
    monkeypatch.setenv("ACB_ENABLED", "false")
    get_settings.cache_clear()
    script = Script(monkeypatch)
    assert _turn(client).json()["review"] is None
    assert script.names() == ["generate-estimate"]


def test_synthesized_results_are_not_cached(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("needs_iteration", "major"))
    _turn(client)
    script.calls.clear()
    second = _turn(client).json()
    assert second["cache_hit"] is False and second["review"]["decision"] == "synthesize"


def test_retry_that_becomes_not_estimable_is_not_a_candidate(client, pm_tier, monkeypatch) -> None:
    Script(
        monkeypatch,
        actor=lambda n: tier_turn("pm") if n == 1 else tier_turn("pm", is_estimable=False, not_estimable_reason="No sé."),
        critic=lambda n: _feedback("needs_iteration", "major"),
    )
    body = _turn(client).json()
    assert body["estimation_structured"]["is_estimable"] is True
    assert body["review"]["decision"] == "synthesize" and body["review"]["actor_calls"] == 2


def test_failed_retry_still_counts_the_attempt(client, pm_tier, monkeypatch) -> None:
    from app.services.errors import LLMTimeoutError

    script = Script(monkeypatch, critic=lambda n: _feedback("needs_iteration", "major"))

    def actor(n):
        if n == 2:
            raise LLMTimeoutError("tiempo agotado")
        return tier_turn("pm")

    script.actor = actor
    body = _turn(client).json()
    assert body["review"]["actor_calls"] == 2 and body["review"]["decision"] == "synthesize"


def test_critic_sees_previous_turns_and_the_full_output_shape(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch)
    session_id = client.post("/sessions").json()["session_id"]
    url = f"/sessions/{session_id}/estimate"
    client.post(url, data={"transcript": TRANSCRIPT, **FORM})
    client.post(url, data={"transcript": "Quita el blog del alcance.", **FORM})
    critic_calls = [messages for name, messages in script.calls if name.startswith("critic")]
    assert "<previous_turns>" not in critic_calls[0][1]["content"]
    assert "<previous_turns>" in critic_calls[1][1]["content"]
    assert '"confidence_in_review": 0-100' in critic_calls[0][0]["content"]


def test_accepted_with_minor_issues_is_not_cached(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("accept", "minor"))
    _turn(client)
    script.calls.clear()
    assert _turn(client).json()["cache_hit"] is False


def test_critic_sees_the_internal_work_breakdown(client, monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_TIER", "executive")
    get_settings.cache_clear()
    script = Script(monkeypatch, actor=lambda n: tier_turn("executive"))
    _turn(client)
    critic_user = next(messages for name, messages in script.calls if name == "critic-1")[1]["content"]
    assert "internal_work_breakdown" in critic_user and "Integración HubSpot" in critic_user


def test_conversation_excerpt_keeps_user_requests_over_long_estimates() -> None:
    from app.schemas.conversation import ChatMessage
    from app.services.llm_service import _conversation_excerpt

    huge = json.dumps({"kind": "estimation", "message": "ok", "estimation": {"summary": "S", "work": ["x" * 50_000]}})
    history = [
        ChatMessage(role="user", content="Quita el blog del alcance."),
        ChatMessage(role="assistant", content=huge, kind="estimation"),
    ]
    excerpt = _conversation_excerpt(history)
    assert "Quita el blog del alcance." in excerpt and len(excerpt) < 2_000


def test_spend_of_a_failed_retry_is_counted(client, pm_tier, monkeypatch) -> None:
    script = Script(monkeypatch, critic=lambda n: _feedback("needs_iteration", "major"))
    produced = {"n": 0}

    def actor(_n):
        produced["n"] += 1  # Cuenta actor y reparaciones: solo la primera salida es válida.
        return tier_turn("pm") if produced["n"] == 1 else "no es json"

    script.actor = actor
    body = _turn(client).json()
    # Actor 1, crítico 1, actor 2 y su reparación (inválidos), y el extractor: 5 llamadas de 20 tokens.
    assert body["usage"]["input_tokens"] == 20 * 5
    assert body["review"]["decision"] == "synthesize"
