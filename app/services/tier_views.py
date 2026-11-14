"""Del borrador del modelo a la vista de cada tier. Sin LLM.

El recorrido de un turno con tier:

1. ``parse_tier_turn``: JSON del modelo → ``*Turn`` del tier (schema = guardrail).
2. ``to_estimation_draft``: el desglose común (``work``) pasa al motor de siempre
   (``postprocessor.validate_and_fix``), que redondea horas, pone la tarifa de la
   tabla y calcula costes y totales. El modelo no hace esa aritmética.
3. ``corrected_draft``: el borrador del tier con las horas ya corregidas. Es lo
   que se guarda en el historial y en las cachés (se vuelve a leer con el paso 1).
4. ``build_view``: la vista del cliente, con rangos calculados en código.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from app.context.examples import RATE_CARD
from app.schemas.estimation import DraftTask, Estimation, EstimationDraft
from app.schemas.tiers import (
    ComponentView,
    DeveloperDraft,
    DeveloperEstimate,
    ExecutiveDraft,
    ExecutiveEstimate,
    MilestoneView,
    PhaseView,
    PmDraft,
    PmEstimate,
    Range,
    Tier,
    TierDraftBase,
    WorkLine,
    phase_key,
)
from app.services.errors import InvalidLLMOutputError
from app.services.postprocessor import FENCE_RE
from app.tiers import resolve_tier_config

# Margen del rango ejecutivo según la confianza declarada.
CONFIDENCE_MARGIN = {"high": 0.10, "medium": 0.20, "low": 0.35}
# Margen de horas del developer: base más un tramo por cada fuente de incertidumbre.
DEV_BASE_MARGIN = 0.10
DEV_MARGIN_PER_DRIVER = 0.05
DEV_MAX_MARGIN = 0.40


def parse_tier_turn(content: str, tier: Tier | str):
    """JSON del modelo → ``*Turn`` del tier. Errores como ``InvalidLLMOutputError`` (dispara la reparación)."""
    schema = resolve_tier_config(tier).turn_schema
    text = FENCE_RE.sub("", content).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise InvalidLLMOutputError("La respuesta no contiene un objeto JSON.")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidLLMOutputError(f"JSON ilegible: {exc}") from exc
    try:
        turn = schema.model_validate(payload)
    except Exception as exc:
        raise InvalidLLMOutputError(f"El JSON no cumple el esquema del tier {Tier(tier).value}: {exc}") from exc
    if turn.kind in {"estimation", "estimation_update"} and turn.estimation is None:
        raise InvalidLLMOutputError("El turno promete una estimación y no la trae.")
    if turn.kind == "answer" and not (turn.message and turn.message.strip()):
        raise InvalidLLMOutputError("Una respuesta de tipo answer necesita message.")
    return turn


def _rate(role: str) -> float:
    entry = RATE_CARD.get(role)
    return float(entry["rate_eur_hour"]) if entry else 0.0


def _round_to_five(hours: float) -> int:
    if hours <= 0:
        return 0
    return max(5, int(round(hours / 5.0) * 5))


def to_estimation_draft(draft: TierDraftBase) -> EstimationDraft:
    """El desglose del tier en el formato del motor común. La tarifa sale de la tabla, no del modelo."""
    tasks = [
        DraftTask(
            name=item.name,
            phase=item.group,
            role=item.role,
            hours=item.hours,
            rate_eur=_rate(item.role),
            cost_eur=round(item.hours * _rate(item.role), 2),
        )
        for item in draft.work
    ]
    # Totales ya sobre horas redondeadas: el motor no debe avisar de un descuadre que no es del modelo.
    rounded = [_round_to_five(task.hours) for task in tasks]
    return EstimationDraft(
        project_title=draft.project_title,
        summary=draft.summary,
        is_estimable=draft.is_estimable,
        not_estimable_reason=draft.not_estimable_reason,
        tasks=tasks,
        total_hours=float(sum(rounded)),
        total_cost_eur=round(sum(hours * task.rate_eur for hours, task in zip(rounded, tasks, strict=True)), 2),
        team=draft.team,
        duration_weeks_min=draft.duration_weeks_min,
        duration_weeks_max=draft.duration_weeks_max,
        assumptions=draft.assumptions,
        risks=draft.risks,
    )


def corrected_draft(draft: TierDraftBase, estimation: Estimation) -> TierDraftBase:
    """El borrador del tier con lo que corrigió el motor (horas y textos tachados)."""
    update: dict = {
        "project_title": estimation.project_title,
        "summary": estimation.summary,
        "not_estimable_reason": estimation.not_estimable_reason,
        "assumptions": estimation.assumptions,
        "risks": estimation.risks,
        "team": estimation.team,
    }
    if estimation.is_estimable and len(estimation.tasks) == len(draft.work):
        update["work"] = [
            item.model_copy(update={"name": task.name, "group": task.phase, "hours": task.hours})
            for item, task in zip(draft.work, estimation.tasks, strict=True)
        ]
    elif not estimation.is_estimable:
        # No estimable: sin desglose ni nada que dependa de él (hitos, componentes).
        update["work"] = []
        update["team"] = []
        for field in ("milestones", "components"):
            if field in type(draft).model_fields:
                update[field] = []
    return draft.model_copy(update=update)


def _round_range(low: float, high: float, step: float) -> Range:
    def down(value: float) -> float:
        return max(0.0, (value // step) * step)

    def up(value: float) -> float:
        return -(-value // step) * step

    return Range(min=down(low), max=up(high))


def _lines_by_group(estimation: Estimation) -> dict[str, list[WorkLine]]:
    groups: dict[str, list[WorkLine]] = {}
    for task in estimation.tasks:
        groups.setdefault(task.phase, []).append(
            WorkLine(name=task.name, role=task.role, hours=task.hours, rate_eur=task.rate_eur, cost_eur=task.cost_eur)
        )
    return groups


def _developer_view(draft: DeveloperDraft, estimation: Estimation) -> DeveloperEstimate:
    notes = {note.name.casefold(): note for note in draft.components}
    components = []
    for name, lines in _lines_by_group(estimation).items():
        note = notes.get(name.casefold())
        components.append(
            ComponentView(
                name=name,
                hours=round(sum(line.hours for line in lines), 2),
                cost_eur=round(sum(line.cost_eur for line in lines), 2),
                work=lines,
                technical_risks=note.technical_risks if note else [],
                stack_assumptions=note.stack_assumptions if note else [],
            )
        )
    margin = min(DEV_MAX_MARGIN, DEV_BASE_MARGIN + DEV_MARGIN_PER_DRIVER * len(draft.uncertainty_drivers))
    total = estimation.total_hours
    return DeveloperEstimate(
        project_title=estimation.project_title,
        summary=estimation.summary,
        is_estimable=estimation.is_estimable,
        not_estimable_reason=estimation.not_estimable_reason,
        components=components,
        uncertainty_drivers=draft.uncertainty_drivers,
        total_hours=total,
        total_hours_range=_round_range(total * (1 - margin), total * (1 + margin), 5),
        total_cost_eur=estimation.total_cost_eur,
        team=estimation.team,
        assumptions=estimation.assumptions,
        risks=estimation.risks,
    )


def _pm_view(draft: PmDraft, estimation: Estimation) -> PmEstimate:
    phases = [
        PhaseView(name=phase.phase, hours=phase.hours, cost_eur=phase.cost_eur, weeks=phase.weeks)
        for phase in estimation.phases
    ]
    cumulative: dict[str, float] = {}
    running = 0.0
    for phase in phases:
        running += phase.cost_eur
        cumulative[phase_key(phase.name)] = round(running, 2)
    if not estimation.is_estimable:
        return PmEstimate(
            project_title=estimation.project_title,
            summary=estimation.summary,
            is_estimable=False,
            not_estimable_reason=estimation.not_estimable_reason,
        )
    milestones = [
        MilestoneView(
            name=item.name,
            phase=item.phase,
            deliverable=item.deliverable,
            cumulative_cost_eur=cumulative[phase_key(item.phase)],
        )
        for item in draft.milestones
    ]
    low, high = sorted((estimation.duration_weeks_min, estimation.duration_weeks_max))
    return PmEstimate(
        project_title=estimation.project_title,
        summary=estimation.summary,
        is_estimable=estimation.is_estimable,
        not_estimable_reason=estimation.not_estimable_reason,
        phases=phases,
        milestones=milestones,
        team_composition=estimation.team,
        duration_weeks_range=Range(min=low, max=high),
        total_hours=estimation.total_hours,
        total_cost_eur=estimation.total_cost_eur,
        blockers=draft.blockers,
        risks=estimation.risks,
        assumptions=estimation.assumptions,
    )


def _executive_view(draft: ExecutiveDraft, estimation: Estimation) -> ExecutiveEstimate:
    margin = CONFIDENCE_MARGIN[draft.confidence_level]
    cost = estimation.total_cost_eur
    low, high = sorted((estimation.duration_weeks_min, estimation.duration_weeks_max))
    return ExecutiveEstimate(
        project_title=estimation.project_title,
        summary=estimation.summary,
        is_estimable=estimation.is_estimable,
        not_estimable_reason=estimation.not_estimable_reason,
        headline_cost_range=_round_range(cost * (1 - margin), cost * (1 + margin), 500),
        headline_duration_range=_round_range(low * (1 - margin), high * (1 + margin), 1),
        confidence_level=draft.confidence_level,
        confidence_reason=draft.confidence_reason,
        top_three_risks=draft.top_three_risks,
        go_no_go_recommendation=draft.go_no_go_recommendation,
    )


def build_view(tier: Tier | str, draft: TierDraftBase, estimation: Estimation) -> BaseModel:
    """Vista del cliente. Rangos y totales salen del motor común, no del modelo."""
    tier = Tier(tier)
    if tier is Tier.DEVELOPER:
        return _developer_view(draft, estimation)  # type: ignore[arg-type]
    if tier is Tier.PM:
        return _pm_view(draft, estimation)  # type: ignore[arg-type]
    return _executive_view(draft, estimation)  # type: ignore[arg-type]


# Campos que son claves de un vocabulario cerrado, no texto libre: el filtro no los toca.
_CLOSED_FIELDS = {"tier", "role", "confidence_level", "kind"}


def redact_model(model: BaseModel, redact) -> BaseModel:
    """Aplica el filtro de PII a todos los textos libres del modelo, los propios del tier incluidos.

    Sirve para la vista del cliente y para el borrador que se guarda en historial y cachés.
    """

    def walk(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: (item if key in _CLOSED_FIELDS else walk(item)) for key, item in value.items()}
        return value

    return type(model).model_validate(walk(model.model_dump()))


redact_view = redact_model


def history_content(turn, draft: TierDraftBase | None) -> str:
    """Lo que se guarda en historial y cachés: el turno con el borrador ya corregido."""
    payload = {
        "kind": turn.kind,
        "message": turn.message,
        "estimation": draft.model_dump() if draft is not None else None,
    }
    return json.dumps(payload, ensure_ascii=False)


def _fmt(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def render_view_markdown(view: BaseModel) -> str:
    """Markdown de cada vista, para clientes que no pintan la estructura."""
    lines = [f"## {view.project_title}", "", view.summary, ""]
    if not view.is_estimable:
        lines.append(f"No estimable: {view.not_estimable_reason or 'sin motivo.'}")
        return "\n".join(lines)
    if isinstance(view, ExecutiveEstimate):
        lines += [
            f"**Coste:** {_fmt(view.headline_cost_range.min)}–{_fmt(view.headline_cost_range.max)} EUR",
            f"**Duración:** {view.headline_duration_range.min:g}–{view.headline_duration_range.max:g} semanas",
            f"**Confianza:** {view.confidence_level}. {view.confidence_reason}",
            "",
            "### Riesgos principales",
            *[f"- {item}" for item in view.top_three_risks],
            "",
            f"**Recomendación:** {view.go_no_go_recommendation}",
        ]
    elif isinstance(view, PmEstimate):
        lines += ["| Fase | Semanas | Coste |", "| --- | --- | --- |"]
        lines += [f"| {phase.name} | {phase.weeks:g} | {_fmt(phase.cost_eur)} |" for phase in view.phases]
        lines += ["", "### Hitos"]
        lines += [f"- {item.name} ({item.phase}): {item.deliverable}" for item in view.milestones]
        rng = view.duration_weeks_range
        lines += ["", f"**Total:** {_fmt(view.total_cost_eur)} EUR, {rng.min:g}–{rng.max:g} semanas"]
        if view.blockers:
            lines += ["", "### Bloqueos", *[f"- {item}" for item in view.blockers]]
    elif isinstance(view, DeveloperEstimate):
        for component in view.components:
            lines += [f"### {component.name} ({component.hours:g} h)"]
            lines += [f"- {line.name} · {line.role} · {line.hours:g} h" for line in component.work]
            lines += [f"- Riesgo: {item}" for item in component.technical_risks]
            lines.append("")
        rng = view.total_hours_range
        lines += [f"**Horas:** {view.total_hours:g} ({rng.min:g}–{rng.max:g}), {_fmt(view.total_cost_eur)} EUR"]
        if view.uncertainty_drivers:
            lines += ["", "### Fuentes de incertidumbre", *[f"- {item}" for item in view.uncertainty_drivers]]
    return "\n".join(lines).rstrip()
