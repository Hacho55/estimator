"""Transforma las estimaciones en bruto en un bloque de contexto listo para el prompt.

La fuente da igual: hoy es examples.py y mañana puede ser el resultado de una búsqueda.
get_context_block es la fuente CAG y, de momento, ignora la transcripción.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from app.config import get_settings
from app.context.examples import ESTIMATION_EXAMPLES, RATE_CARD
from app.schemas.context import ContextBlock, Exclusion, HistoricalEstimate
from app.tokens import count_tokens

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){8,}\d")

ROLE_ALIASES = {
    "frontend": "frontend",
    "front": "frontend",
    "front-end": "frontend",
    "backend": "backend",
    "back": "backend",
    "back-end": "backend",
    "fullstack": "fullstack",
    "full-stack": "fullstack",
    "full stack": "fullstack",
    "ux_ui": "ux_ui",
    "ux": "ux_ui",
    "ui": "ux_ui",
    "ux/ui": "ux_ui",
    "diseñador ux": "ux_ui",
    "diseñador de ux": "ux_ui",
    "diseñador ui": "ux_ui",
    "qa": "qa",
    "calidad": "qa",
    "devops": "devops",
    "pm": "pm",
    "product manager": "pm",
}

DETAIL_ORDER = ("full", "standard", "summary")


@dataclass
class PreparedTask:
    name: str
    phase: str
    role: str
    hours: float
    rate_eur: float | None
    cost_eur: float | None


@dataclass
class PreparedEstimate:
    id: str
    title: str
    sector: str
    year: int
    client_profile: str
    meeting_summary: str
    tasks: list[PreparedTask]
    team: list[dict]
    duration_weeks_min: int
    duration_weeks_max: int
    assumptions: list[str]
    risks: list[str]
    total_hours: float = 0
    total_cost_eur: float = 0
    hours_by_phase: dict[str, float] = field(default_factory=dict)


def load_estimates(raw_estimates: list[dict] | None = None) -> list[HistoricalEstimate]:
    source = ESTIMATION_EXAMPLES if raw_estimates is None else raw_estimates
    return [HistoricalEstimate.model_validate(item) for item in source]


def _parse_quantity(value: float | str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip().replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def canonical_role(role: str) -> str:
    key = role.strip().lower()
    return ROLE_ALIASES.get(key, key.replace(" ", "_"))


def anonymize(text: str, client_name: str | None) -> str:
    cleaned = EMAIL_RE.sub("[email]", text)
    cleaned = PHONE_RE.sub("[teléfono]", cleaned)
    if client_name:
        cleaned = re.sub(re.escape(client_name), "el cliente", cleaned, flags=re.IGNORECASE)
    return cleaned


def _format_number(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded:,}".replace(",", ".")
    return f"{value:.1f}".replace(".", ",")


def prepare(estimate: HistoricalEstimate) -> PreparedEstimate:
    summary = anonymize(estimate.meeting_summary, estimate.client_name)
    tasks: list[PreparedTask] = []
    for task in estimate.tasks:
        hours = _parse_quantity(task.quantity)
        if task.unit == "days":
            hours *= 8
        hours = round(hours, 2)
        role = canonical_role(task.role)
        rate = RATE_CARD.get(role, {}).get("rate_eur_hour")
        cost = round(hours * rate, 2) if rate is not None else None
        tasks.append(
            PreparedTask(
                name=task.name,
                phase=task.phase,
                role=role,
                hours=hours,
                rate_eur=rate,
                cost_eur=cost,
            )
        )
    hours_by_phase: dict[str, float] = {}
    for task in tasks:
        hours_by_phase[task.phase] = hours_by_phase.get(task.phase, 0) + task.hours
    total_hours = sum(task.hours for task in tasks)
    total_cost = sum(task.cost_eur or 0 for task in tasks)
    team = [
        {"role": canonical_role(member.role), "count": member.count, "dedication": member.dedication}
        for member in estimate.team
    ]
    return PreparedEstimate(
        id=estimate.id,
        title=estimate.title,
        sector=estimate.sector,
        year=estimate.year,
        client_profile=estimate.client_profile,
        meeting_summary=summary,
        tasks=tasks,
        team=team,
        duration_weeks_min=estimate.duration_weeks_min,
        duration_weeks_max=estimate.duration_weeks_max,
        assumptions=list(estimate.assumptions),
        risks=list(estimate.risks),
        total_hours=total_hours,
        total_cost_eur=total_cost,
        hours_by_phase=hours_by_phase,
    )


def render_rate_card() -> str:
    lines = ["Tabla de tarifas de la empresa (EUR por hora):"]
    for role, entry in RATE_CARD.items():
        lines.append(
            f"- {role}: { _format_number(entry['rate_eur_hour']) } EUR/hora "
            f"(rango { _format_number(entry['min_eur_hour']) }-{ _format_number(entry['max_eur_hour']) })"
        )
    return "\n".join(lines)


def _team_line(estimate: PreparedEstimate) -> str:
    parts = []
    for member in estimate.team:
        dedication = member["dedication"]
        label = "completa" if dedication == 1 else f"{int(dedication * 100)}%"
        parts.append(f"{_format_number(member['count'])} {member['role']} ({label})")
    return ", ".join(parts)


def _limit(items: list[str], detail_level: str) -> list[str]:
    if detail_level == "summary":
        return items[:2]
    return items


def render_estimate(estimate: PreparedEstimate, index: int, detail_level: str, fmt: str) -> str:
    assumptions = _limit(estimate.assumptions, detail_level)
    risks = _limit(estimate.risks, detail_level)
    if fmt == "json":
        payload = {
            "id": estimate.id,
            "proyecto": estimate.title,
            "sector": estimate.sector,
            "perfil": estimate.client_profile,
            "resumen": estimate.meeting_summary,
            "tareas": _tasks_payload(estimate, detail_level),
            "total_horas": estimate.total_hours,
            "total_coste_eur": estimate.total_cost_eur,
            "equipo": estimate.team,
            "duracion_semanas": [estimate.duration_weeks_min, estimate.duration_weeks_max],
            "supuestos": assumptions,
            "riesgos": risks,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    elif fmt == "plain":
        body = _render_plain(estimate, detail_level, assumptions, risks)
    else:
        body = _render_markdown(estimate, detail_level, assumptions, risks)
    return f"===== ESTIMACIÓN DE REFERENCIA {index} =====\n{body}"


def _tasks_payload(estimate: PreparedEstimate, detail_level: str) -> list[dict]:
    if detail_level == "summary":
        return [
            {"fase": phase, "horas": hours} for phase, hours in estimate.hours_by_phase.items()
        ]
    rows = []
    for task in estimate.tasks:
        row = {"nombre": task.name, "fase": task.phase, "rol": task.role, "horas": task.hours}
        if detail_level == "full":
            row["tarifa_eur_hora"] = task.rate_eur
            row["coste_eur"] = task.cost_eur
        rows.append(row)
    return rows


def _render_plain(
    estimate: PreparedEstimate, detail_level: str, assumptions: list[str], risks: list[str]
) -> str:
    lines = [
        f"Proyecto: {estimate.title}",
        f"Sector: {estimate.sector}. Perfil: {estimate.client_profile}",
        f"Resumen: {estimate.meeting_summary}",
        "Tareas:",
    ]
    if detail_level == "summary":
        for phase, hours in estimate.hours_by_phase.items():
            lines.append(f"- {phase}: {_format_number(hours)} horas")
    else:
        for task in estimate.tasks:
            if detail_level == "full" and task.rate_eur is not None:
                lines.append(
                    f"- {task.name}: {_format_number(task.hours)} horas de {task.role} "
                    f"a {_format_number(task.rate_eur)} EUR/hora -> {_format_number(task.cost_eur or 0)} EUR"
                )
            else:
                lines.append(
                    f"- {task.name} ({task.phase}, {task.role}): {_format_number(task.hours)} horas"
                )
    lines.append(
        f"Total: {_format_number(estimate.total_hours)} horas, "
        f"{_format_number(estimate.total_cost_eur)} EUR"
    )
    lines.append(f"Equipo: {_team_line(estimate)}")
    lines.append(
        f"Duración: {estimate.duration_weeks_min}-{estimate.duration_weeks_max} semanas"
    )
    lines.append("Supuestos: " + "; ".join(assumptions))
    lines.append("Riesgos: " + "; ".join(risks))
    return "\n".join(lines)


def _render_markdown(
    estimate: PreparedEstimate, detail_level: str, assumptions: list[str], risks: list[str]
) -> str:
    lines = [
        f"### {estimate.title}",
        f"Sector: {estimate.sector}. Perfil del cliente: {estimate.client_profile}.",
        "",
        estimate.meeting_summary,
        "",
        "#### Desglose",
    ]
    if detail_level == "summary":
        for phase, hours in estimate.hours_by_phase.items():
            lines.append(f"- {phase}: {_format_number(hours)} horas")
    elif detail_level == "full":
        lines.append("| Tarea | Fase | Rol | Horas | EUR/h | Coste |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for task in estimate.tasks:
            lines.append(
                f"| {task.name} | {task.phase} | {task.role} | {_format_number(task.hours)} "
                f"| {_format_number(task.rate_eur or 0)} | {_format_number(task.cost_eur or 0)} |"
            )
    else:
        lines.append("| Tarea | Fase | Rol | Horas |")
        lines.append("| --- | --- | --- | --- |")
        for task in estimate.tasks:
            lines.append(
                f"| {task.name} | {task.phase} | {task.role} | {_format_number(task.hours)} |"
            )
    lines.extend(
        [
            "",
            f"**Total: {_format_number(estimate.total_hours)} horas, "
            f"{_format_number(estimate.total_cost_eur)} EUR**",
            "",
            "#### Equipo",
            _team_line(estimate),
            "",
            "#### Duración",
            f"{estimate.duration_weeks_min}-{estimate.duration_weeks_max} semanas",
            "",
            "#### Supuestos",
            *[f"- {item}" for item in assumptions],
            "",
            "#### Riesgos",
            *[f"- {item}" for item in risks],
        ]
    )
    return "\n".join(lines)


def _lower_detail(detail_level: str) -> str | None:
    if detail_level not in DETAIL_ORDER:
        return "summary"
    index = DETAIL_ORDER.index(detail_level)
    if index + 1 >= len(DETAIL_ORDER):
        return None
    return DETAIL_ORDER[index + 1]


def _render_examples(prepared: list[PreparedEstimate], detail_level: str, fmt: str) -> str:
    bodies = [
        render_estimate(estimate, index, detail_level, fmt)
        for index, estimate in enumerate(prepared, start=1)
    ]
    closing = "===== FIN DE ESTIMACIONES DE REFERENCIA ====="
    return "\n\n".join([*bodies, closing]) if bodies else closing


def build_context_block(
    estimates: list[HistoricalEstimate] | None = None,
    *,
    fmt: str | None = None,
    detail_level: str | None = None,
    max_examples: int | None = None,
    max_tokens: int | None = None,
    max_age_years: int | None = None,
    examples_are_static: bool = True,
    today: date | None = None,
) -> ContextBlock:
    settings = get_settings()
    fmt = fmt or settings.CONTEXT_FORMAT
    detail_level = detail_level or settings.CONTEXT_DETAIL_LEVEL
    max_examples = settings.CONTEXT_MAX_EXAMPLES if max_examples is None else max_examples
    max_tokens = settings.CONTEXT_MAX_TOKENS if max_tokens is None else max_tokens
    max_age_years = settings.CONTEXT_MAX_AGE_YEARS if max_age_years is None else max_age_years
    current_year = (today or date.today()).year

    loaded = estimates if estimates is not None else load_estimates()
    excluded: list[Exclusion] = []
    fresh: list[HistoricalEstimate] = []
    for estimate in loaded:
        if current_year - estimate.year > max_age_years:
            excluded.append(Exclusion(id=estimate.id, reason="obsoleta"))
        else:
            fresh.append(estimate)

    prepared_all = [prepare(estimate) for estimate in fresh]
    notes: list[str] = []
    if len(prepared_all) > max_examples:
        for estimate in prepared_all[max_examples:]:
            excluded.append(Exclusion(id=estimate.id, reason="max_examples"))
        prepared_all = prepared_all[:max_examples]
        notes.append(f"Recortado a {max_examples} ejemplos.")

    static_text = render_rate_card()
    prepared = prepared_all
    while True:
        examples_text = _render_examples(prepared, detail_level, fmt)
        used = count_tokens(static_text) + count_tokens(examples_text)
        if used <= max_tokens or not prepared:
            break
        lowered = _lower_detail(detail_level)
        if lowered is not None and lowered != detail_level:
            notes.append(f"Detalle bajado de {detail_level} a {lowered} por presupuesto.")
            detail_level = lowered
            continue
        dropped = prepared[-1]
        prepared = prepared[:-1]
        excluded.append(Exclusion(id=dropped.id, reason="presupuesto"))
        notes.append(f"Ejemplo {dropped.id} fuera por presupuesto de tokens.")

    return ContextBlock(
        static_text=static_text,
        examples_text=_render_examples(prepared, detail_level, fmt),
        examples_are_static=examples_are_static,
        detail_level=detail_level,
        format=fmt,
        included_ids=[estimate.id for estimate in prepared],
        excluded=excluded,
        notes=notes,
    )


@lru_cache(maxsize=1)
def _cached_context() -> ContextBlock:
    return build_context_block()


def get_context_block(transcription: str = "") -> ContextBlock:
    """Fuente CAG. La transcripción no selecciona ejemplos; en RAG sí lo hará."""
    del transcription
    return _cached_context()


def validate_reference_data() -> None:
    load_estimates()


validate_reference_data()
