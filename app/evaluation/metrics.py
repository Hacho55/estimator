"""Observabilidad: puntuaciones que no dependen de un juez.

No forman parte de la estimación. llm_service las manda a Langfuse con
record_scores, y el script de experimentos las guarda en el informe local.
"""

from app.context.examples import RATE_CARD
from app.evaluation.properties import Subject, run_properties
from app.schemas.estimation import Estimation, UsageInfo, ValidationIssue


def collect_metrics(
    *,
    first_try: bool,
    warnings: list[ValidationIssue],
    estimation: Estimation | None,
    usage: UsageInfo,
    cost_usd: float | None,
    latency_ms: float,
    expected: dict | None = None,
    view=None,
    tier: str | None = None,
    memory: dict | None = None,
) -> dict[str, float]:
    scores: dict[str, float] = {
        "json_valid_first_try": 1.0 if first_try else 0.0,
        "warnings_total": float(len(warnings)),
        "warnings_warning": float(sum(1 for issue in warnings if issue.severity == "warning")),
        "corrections_applied": float(sum(1 for issue in warnings if issue.corrected_value is not None)),
        "latency_ms": latency_ms,
        "input_tokens": float(usage.input_tokens),
        "output_tokens": float(usage.output_tokens),
    }
    if usage.input_tokens:
        scores["cached_ratio"] = usage.cached_tokens / usage.input_tokens
    if cost_usd is not None:
        scores["cost_usd"] = cost_usd
    if view is not None and tier:
        # Las mismas propiedades que la suite de evals, como puntuación online (0 o 1).
        subject = Subject(tier=tier, view=view, estimation=estimation, memory=memory)
        for result in run_properties(subject):
            scores[f"prop_{result.name}"] = 1.0 if result.passed else 0.0
    if estimation is None:
        return scores

    scores["hours_multiple_of_5"] = _hours_multiple_of_5(estimation)
    scores["rates_within_card"] = 0.0 if any(issue.code == "rate_out_of_range" for issue in warnings) else 1.0
    scores["sections_complete"] = _sections_complete(estimation)
    if expected:
        if "estimable" in expected and expected["estimable"] is not None:
            scores["is_estimable_matches_expected"] = (
                1.0 if estimation.is_estimable == bool(expected["estimable"]) else 0.0
            )
        low = expected.get("hours_min")
        high = expected.get("hours_max")
        if low is not None and high is not None and estimation.is_estimable:
            scores["hours_in_expected_range"] = (
                1.0 if low <= estimation.total_hours <= high else 0.0
            )
    return scores


def _hours_multiple_of_5(estimation: Estimation) -> float:
    if not estimation.tasks:
        return 1.0
    ok = all(task.hours > 0 and task.hours % 5 == 0 for task in estimation.tasks)
    return 1.0 if ok else 0.0


def _sections_complete(estimation: Estimation) -> float:
    if not estimation.is_estimable:
        return 1.0 if estimation.not_estimable_reason else 0.0
    checks = [
        bool(estimation.summary.strip()),
        bool(estimation.tasks),
        bool(estimation.team),
        estimation.duration_weeks_max > 0,
        bool(estimation.assumptions),
        bool(estimation.risks),
    ]
    return sum(1.0 for check in checks if check) / len(checks)


def rate_is_within_card(role: str, rate: float) -> bool:
    entry = RATE_CARD.get(role)
    if entry is None:
        return False
    return entry["min_eur_hour"] <= rate <= entry["max_eur_hour"]
