"""Critic (fase 5): evalúa un candidato y devuelve issues estructurados. No reescribe.

Dos capas:

1. **Determinista**: las propiedades de ``app/evaluation/properties.py`` sobre la vista
   ya corregida. Sin llamada al LLM. Lo que el código puede comprobar no se le pide
   al crítico.
2. **LLM**: ``app/prompts/critic/v1/``, un auditor con criterios por categoría
   (alcance, componentes, invenciones, memoria, riesgos, audiencia). Modelo
   ``CRITIC_MODEL``, a ser posible de otra familia que el actor.

Si la llamada al LLM falla, se devuelven solo los issues deterministas con
``critic_ok=False``: el Boss no puede quedarse esperando a un crítico caído.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.evaluation.properties import Subject, run_properties
from app.prompts.loader import render_critic_prompt
from app.schemas.estimation import UsageInfo
from app.schemas.review import CriticFeedback, ReviewIssue, Severity
from app.services import llm_client

log = structlog.get_logger(__name__)

# Severidad de cada propiedad que falla, fijada en código (no la decide un LLM).
PROPERTY_SEVERITY: dict[str, Severity] = {
    "schema_valid": "critical",
    "hours_within_absolute_bounds": "critical",
    "required_text_present": "major",
    "ranges_ordered": "major",
    "developer_components": "major",
    "pm_milestones": "major",
    "executive_headline": "major",
    "respects_rejected_options": "major",
    "hours_multiple_of_5": "minor",
    "rates_within_card": "minor",
    "respects_team_size": "minor",
}
CRITIC_ATTACHMENT_CHARS = 8_000


@dataclass
class CriticResult:
    verdict: str
    issues: list[ReviewIssue] = field(default_factory=list)
    critic_ok: bool = True
    usage: UsageInfo | None = None
    cost_usd: float | None = None
    latency_ms: float = 0


def property_issues(subject: Subject) -> list[ReviewIssue]:
    """Capa determinista: cada propiedad que falla es un issue con severidad fija."""
    return [
        ReviewIssue(
            source="property",
            category=f"property:{result.name}",
            severity=PROPERTY_SEVERITY.get(result.name, "minor"),
            field_path=result.name,
            description=result.detail or f"Falla la propiedad {result.name}.",
        )
        for result in run_properties(subject)
        if not result.passed
    ]


def _candidate_payload(subject: Subject) -> str:
    """Lo que audita el crítico: la vista del cliente y el desglose interno de trabajo.

    En executive la vista no lleva desglose; sin él no se puede juzgar el alcance.
    """
    work = []
    if subject.estimation is not None and subject.estimation.is_estimable:
        work = [
            {"name": task.name, "group": task.phase, "role": task.role, "hours": task.hours}
            for task in subject.estimation.tasks
        ]
    return json.dumps(
        {"client_view": subject.view.model_dump(mode="json"), "internal_work_breakdown": work},
        ensure_ascii=False,
    )


def critic_response_format() -> dict:
    if get_settings().LLM_RESPONSE_FORMAT == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "critic_feedback", "schema": CriticFeedback.model_json_schema(), "strict": False},
    }


def _usage(result: llm_client.LLMResult) -> UsageInfo:
    return UsageInfo(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cached_tokens=result.cached_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )


async def review_candidate(
    *,
    subject: Subject,
    transcript: str,
    attachments: list[dict],
    conversation: str = "",
    name: str = "critic",
) -> CriticResult:
    """Revisa un candidato: primero las propiedades, después el auditor LLM."""
    deterministic = property_issues(subject)
    excerpt = "\n\n".join(f"[{item['name']}]\n{item['text']}" for item in attachments)[:CRITIC_ATTACHMENT_CHARS]
    system, user = render_critic_prompt(
        tier=subject.tier,
        transcript=transcript,
        attachments=excerpt,
        memory=json.dumps(subject.memory or {}, ensure_ascii=False),
        estimation=_candidate_payload(subject),
        conversation=conversation,
    )
    settings = get_settings()
    started = time.perf_counter()
    try:
        result = await llm_client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            critic_response_format(),
            name=name,
            model=settings.CRITIC_MODEL.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("critic_failed", reason=str(exc))
        return CriticResult(
            verdict="unavailable",
            issues=deterministic,
            critic_ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    usage = _usage(result)
    try:
        feedback = CriticFeedback.model_validate_json(result.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("critic_output_invalid", reason=str(exc))
        return CriticResult(
            verdict="unavailable",
            issues=deterministic,
            critic_ok=False,
            usage=usage,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    llm_issues = [ReviewIssue(source="critic", **issue.model_dump()) for issue in feedback.issues]
    return CriticResult(
        verdict=feedback.verdict,
        issues=[*deterministic, *llm_issues],
        usage=usage,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
