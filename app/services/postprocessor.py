"""Convierte la respuesta del modelo en una estimación coherente y en Markdown.

Corrige lo que se puede recalcular (costes y totales) y señala el resto.
Lo que se guarda en el historial es esta versión, no la salida en bruto.
"""

from __future__ import annotations

import json
import re

from app.context.examples import RATE_CARD
from app.schemas.estimation import Estimation, EstimationDraft, TurnOutput, ValidationIssue
from app.services.errors import InvalidLLMOutputError

FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
COST_TOLERANCE = 0.01
MAX_TASK_HOURS = 300


def parse_turn(content: str) -> TurnOutput:
    text = FENCE_RE.sub("", content).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise InvalidLLMOutputError("La respuesta no contiene un objeto JSON.")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidLLMOutputError(f"JSON ilegible: {exc}") from exc
    try:
        turn = TurnOutput.model_validate(payload)
    except Exception as exc:
        raise InvalidLLMOutputError(f"El JSON no cumple el esquema: {exc}") from exc
    if turn.kind in {"estimation", "estimation_update"} and turn.estimation is None:
        raise InvalidLLMOutputError("El turno promete una estimación y no la trae.")
    if turn.kind == "answer" and not (turn.message and turn.message.strip()):
        raise InvalidLLMOutputError("Una respuesta de tipo answer necesita message.")
    return turn


def _round_to_five(hours: float) -> int:
    if hours <= 0:
        return 0
    return max(5, int(round(hours / 5.0) * 5))


def _issue(
    code: str,
    severity: str,
    message: str,
    field: str | None = None,
    original: object | None = None,
    corrected: object | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        field=field,
        original_value=None if original is None else str(original),
        corrected_value=None if corrected is None else str(corrected),
    )


def validate_and_fix(
    draft: EstimationDraft, rate_card: dict[str, dict[str, float]] | None = None
) -> tuple[Estimation, list[ValidationIssue]]:
    card = rate_card or RATE_CARD
    issues: list[ValidationIssue] = []
    data = draft.model_dump()

    if not draft.is_estimable:
        issues.append(
            _issue(
                "not_estimable",
                "warning",
                draft.not_estimable_reason or "El modelo marcó la petición como no estimable.",
            )
        )
        return Estimation.model_validate(data), issues

    for index, task in enumerate(data["tasks"]):
        hours = float(task["hours"])
        rate = float(task["rate_eur"])
        cost = float(task["cost_eur"])
        role = str(task["role"])
        prefix = f"tasks[{index}]"

        if hours <= 0 or hours > MAX_TASK_HOURS:
            issues.append(
                _issue(
                    "hours_out_of_bounds",
                    "warning",
                    f"La tarea '{task['name']}' tiene {hours} horas.",
                    f"{prefix}.hours",
                    hours,
                )
            )

        rounded = _round_to_five(hours)
        if rounded != hours and hours > 0:
            issues.append(
                _issue(
                    "hours_not_multiple_of_5",
                    "warning",
                    f"Horas de '{task['name']}' redondeadas al múltiplo de 5 más cercano.",
                    f"{prefix}.hours",
                    hours,
                    rounded,
                )
            )
            hours = float(rounded)
            task["hours"] = hours

        entry = card.get(role)
        if entry is None:
            issues.append(
                _issue(
                    "unknown_role",
                    "warning",
                    f"El rol '{role}' no está en la tabla de tarifas.",
                    f"{prefix}.role",
                    role,
                )
            )
        elif rate < entry["min_eur_hour"] or rate > entry["max_eur_hour"]:
            issues.append(
                _issue(
                    "rate_out_of_range",
                    "warning",
                    f"La tarifa de {role} ({rate} EUR/h) queda fuera del rango de la empresa.",
                    f"{prefix}.rate_eur",
                    rate,
                )
            )

        expected_cost = round(hours * rate, 2)
        if expected_cost and abs(cost - expected_cost) / expected_cost > COST_TOLERANCE:
            issues.append(
                _issue(
                    "cost_mismatch",
                    "warning",
                    f"Coste de '{task['name']}' corregido a horas por tarifa.",
                    f"{prefix}.cost_eur",
                    cost,
                    expected_cost,
                )
            )
            task["cost_eur"] = expected_cost
        elif expected_cost == 0 and cost != 0:
            task["cost_eur"] = 0

    total_hours = round(sum(float(task["hours"]) for task in data["tasks"]), 2)
    total_cost = round(sum(float(task["cost_eur"]) for task in data["tasks"]), 2)
    if abs(float(data["total_hours"]) - total_hours) > 0.01:
        issues.append(
            _issue(
                "total_hours_mismatch",
                "warning",
                "El total de horas no coincidía con la suma de las tareas.",
                "total_hours",
                data["total_hours"],
                total_hours,
            )
        )
    if total_cost and abs(float(data["total_cost_eur"]) - total_cost) / total_cost > COST_TOLERANCE:
        issues.append(
            _issue(
                "total_cost_mismatch",
                "warning",
                "El coste total no coincidía con la suma de las tareas.",
                "total_cost_eur",
                data["total_cost_eur"],
                total_cost,
            )
        )
    data["total_hours"] = total_hours
    data["total_cost_eur"] = total_cost

    if not data["tasks"]:
        issues.append(_issue("no_tasks", "warning", "La estimación no tiene tareas."))
    if not data["assumptions"]:
        issues.append(_issue("no_assumptions", "info", "La estimación no declara supuestos."))
    if not data["risks"]:
        issues.append(_issue("no_risks", "info", "La estimación no declara riesgos."))

    capacity = sum(float(member["count"]) * float(member["dedication"]) for member in data["team"])
    if capacity > 0 and total_hours > 0:
        implied_weeks = total_hours / (capacity * 40)
        low = float(data["duration_weeks_min"]) * 0.5
        high = float(data["duration_weeks_max"]) * 1.5
        if implied_weeks < low or implied_weeks > high:
            issues.append(
                _issue(
                    "duration_incoherent",
                    "warning",
                    (
                        f"La duración declarada no cuadra con {total_hours} horas y el equipo: "
                        f"salen unas {implied_weeks:.1f} semanas."
                    ),
                    "duration_weeks",
                    f"{data['duration_weeks_min']}-{data['duration_weeks_max']}",
                )
            )

    return Estimation.model_validate(data), issues


def render_markdown(estimation: Estimation) -> str:
    lines = [f"## Estimación: {estimation.project_title}", "", estimation.summary, ""]
    if not estimation.is_estimable:
        lines.append(f"No estimable: {estimation.not_estimable_reason or 'sin motivo.'}")
        return "\n".join(lines)

    lines.extend(["### Desglose de tareas", "", "| Tarea | Fase | Rol | Horas | EUR/h | Coste |", "| --- | --- | --- | --- | --- | --- |"])
    for task in estimation.tasks:
        lines.append(
            f"| {task.name} | {task.phase} | {task.role} | {task.hours:g} | {task.rate_eur:g} | {task.cost_eur:g} |"
        )
    lines.extend(
        [
            "",
            f"**Total estimado: {estimation.total_hours:g} horas, {estimation.total_cost_eur:g} EUR**",
            "",
            "### Equipo recomendado",
        ]
    )
    if estimation.team:
        for member in estimation.team:
            lines.append(f"- {member.count:g} {member.role} (dedicación {member.dedication:g})")
    else:
        lines.append("- Sin equipo declarado.")
    lines.extend(
        [
            "",
            "### Duración estimada",
            f"{estimation.duration_weeks_min:g}-{estimation.duration_weeks_max:g} semanas",
            "",
            "### Supuestos",
        ]
    )
    lines.extend(f"- {item}" for item in estimation.assumptions)
    lines.extend(["", "### Riesgos"])
    lines.extend(f"- {item}" for item in estimation.risks)
    return "\n".join(lines)


def to_history_content(turn: TurnOutput, estimation: Estimation | None) -> str:
    payload = {
        "kind": turn.kind,
        "message": turn.message,
        "estimation": estimation.model_dump() if estimation is not None else None,
    }
    return json.dumps(payload, ensure_ascii=False)
