"""Bucle Actor → Critic → Boss (fase 5). Orquesta; no llama al modelo directamente.

El actor es una función que recibe el feedback del crítico y devuelve un nuevo
``TurnResult`` (la estimación de siempre, con ``<critic_feedback>`` en el mensaje
de usuario). El crítico y el Boss viven en sus módulos. Aquí solo se compone la
secuencia y se acota: como mucho ``max_actor_calls`` llamadas al actor.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from app.evaluation.properties import Subject
from app.observability import traced_span
from app.schemas.estimation import UsageInfo
from app.schemas.review import ReviewIteration, ReviewTrace
from app.services.boss import Candidate, best_candidate, decide, feedback_for_actor
from app.services.critic import review_candidate
from app.services.errors import ServiceError

log = structlog.get_logger(__name__)

ESTIMATION_KINDS = {"estimation", "estimation_update"}


@dataclass
class ReviewOutcome:
    result: object  # TurnResult elegido.
    trace: ReviewTrace
    extra_usage: list[UsageInfo] = field(default_factory=list)  # Críticos y actores adicionales.
    extra_cost: float | None = None
    extra_ms: float = 0


def _annotate(span, **fields) -> None:
    if span is not None:
        span.update(**fields)


async def run_review(
    first,
    *,
    actor: Callable[[list[dict], int], Awaitable[object]],
    subject_of: Callable[[object], Subject],
    transcript: str,
    attachments: list[dict],
    max_actor_calls: int,
    conversation: str = "",
) -> ReviewOutcome:
    started = time.perf_counter()
    current = first
    actor_calls = 1
    candidates: list[Candidate] = []
    iterations: list[ReviewIteration] = []
    extra_usage: list[UsageInfo] = []
    extra_cost: float | None = None
    extra_ms = 0.0
    round_number = 0
    while True:
        round_number += 1
        with traced_span(f"critic-{round_number}") as span:
            critic = await review_candidate(
                subject=subject_of(current),
                transcript=transcript,
                attachments=attachments,
                conversation=conversation,
                name=f"critic-{round_number}",
            )
            _annotate(span, output={"verdict": critic.verdict, "issues": [i.category for i in critic.issues]})
        if critic.usage is not None:
            extra_usage.append(critic.usage)
        if critic.cost_usd is not None:
            extra_cost = (extra_cost or 0) + critic.cost_usd
        extra_ms += critic.latency_ms
        candidates.append(Candidate(result=current, issues=critic.issues))

        decision = decide(
            verdict=critic.verdict,
            issues=critic.issues,
            iterations_left=max_actor_calls - actor_calls,
        )
        with traced_span(f"boss-{round_number}") as span:
            _annotate(span, output={"decision": decision, "actor_calls": actor_calls})
        iterations.append(
            ReviewIteration(
                iteration=round_number,
                verdict=critic.verdict,
                decision=decision,
                issues=critic.issues,
                critic_ok=critic.critic_ok,
                actor_repairs=getattr(current, "repairs", 0),
                actor_ms=getattr(current, "latency_ms", 0),
                critic_ms=critic.latency_ms,
            )
        )
        log.info("review_round", round=round_number, verdict=critic.verdict, decision=decision)

        if decision == "accept":
            chosen = candidates[-1]
            break
        if decision == "synthesize":
            chosen = best_candidate(candidates)
            break

        # iterate: el actor vuelve a intentarlo con los issues bloqueantes.
        actor_calls += 1  # Se cuenta el intento, salga bien o no.
        attempt_started = time.perf_counter()
        try:
            following = await actor(feedback_for_actor(critic.issues), actor_calls)
        except ServiceError as exc:
            log.warning("review_actor_failed", reason=exc.message)
            extra_ms += (time.perf_counter() - attempt_started) * 1000
            spent = getattr(exc, "spent_usage", None)
            if spent is not None:  # Respondió pero no valía: sus tokens y su coste cuentan.
                extra_usage.append(spent)
                if getattr(exc, "spent_cost", None) is not None:
                    extra_cost = (extra_cost or 0) + exc.spent_cost  # type: ignore[attr-defined]
            decision, chosen = "synthesize", best_candidate(candidates)
            iterations[-1].decision = "synthesize"
            break
        extra_usage.append(following.usage)
        if following.cost_usd is not None:
            extra_cost = (extra_cost or 0) + following.cost_usd
        extra_ms += following.latency_ms
        estimable = getattr(getattr(following, "estimation", None), "is_estimable", False)
        if following.kind not in ESTIMATION_KINDS or getattr(following, "view", None) is None or not estimable:
            # El actor dejó de estimar (otra cosa o no estimable): se entrega lo mejor que había.
            decision, chosen = "synthesize", best_candidate(candidates)
            iterations[-1].decision = "synthesize"
            break
        current = following

    trace = ReviewTrace(
        decision=decision,
        iterations=iterations,
        actor_calls=actor_calls,
        open_issues=chosen.issues,
        review_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return ReviewOutcome(result=chosen.result, trace=trace, extra_usage=extra_usage, extra_cost=extra_cost, extra_ms=extra_ms)
