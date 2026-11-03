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


def _phases_from_tasks(tasks: list[dict], *, weeks_min: float, weeks_max: float) -> list[dict]:
    order: list[str] = []
    buckets: dict[str, dict[str, float]] = {}
    for task in tasks:
        phase = str(task.get("phase") or "Sin fase")
        if phase not in buckets:
            order.append(phase)
            buckets[phase] = {"hours": 0.0, "cost_eur": 0.0}
        buckets[phase]["hours"] += float(task.get("hours") or 0)
        buckets[phase]["cost_eur"] += float(task.get("cost_eur") or 0)
    total_hours = sum(item["hours"] for item in buckets.values()) or 1.0
    mid_weeks = (float(weeks_min) + float(weeks_max)) / 2 if (weeks_min or weeks_max) else 0.0
    phases: list[dict] = []
    for phase in order:
        hours = round(buckets[phase]["hours"], 2)
        cost = round(buckets[phase]["cost_eur"], 2)
        weeks = round(mid_weeks * (hours / total_hours), 2) if mid_weeks else 0.0
        phases.append({"phase": phase, "hours": hours, "cost_eur": cost, "weeks": weeks})
    return phases


def validate_and_fix(
    draft: EstimationDraft,
    rate_card: dict[str, dict[str, float]] | None = None,
    *,
    require_phase_assumptions: bool = False,
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
        data["phases"] = []
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

    task_phases = {str(task["phase"]) for task in data["tasks"]}
    phase_assumptions = data.get("phase_assumptions") or []
    if require_phase_assumptions and not phase_assumptions:
        issues.append(
            _issue(
                "missing_phase_assumptions",
                "info",
                "El detalle detailed pedía supuestos por fase y no llegaron.",
                "phase_assumptions",
            )
        )
    for index, entry in enumerate(phase_assumptions):
        phase = str(entry.get("phase") or "")
        if phase and phase not in task_phases:
            issues.append(
                _issue(
                    "unknown_phase_assumption",
                    "info",
                    f"Los supuestos nombran la fase '{phase}', que no tiene tareas.",
                    f"phase_assumptions[{index}].phase",
                    phase,
                )
            )

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

    data["phases"] = _phases_from_tasks(
        data["tasks"],
        weeks_min=float(data["duration_weeks_min"]),
        weeks_max=float(data["duration_weeks_max"]),
    )
    return Estimation.model_validate(data), issues


def preview_markdown(raw: str) -> str:
    """Sesión 3 · streaming: el texto visible mientras el JSON del modelo aún no cerró."""
    data = _loads_partial(raw)
    if not isinstance(data, dict):
        return ""
    estimation = data.get("estimation")
    if data.get("kind") == "answer" or not isinstance(estimation, dict):
        message = data.get("message")
        return message if isinstance(message, str) else ""
    return _render_partial_estimation(estimation)


def _loads_partial(text: str) -> dict | None:
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    for drop in range(40):
        candidate = body if drop == 0 else body[:-drop]
        candidate = candidate.rstrip().rstrip(",")
        if not candidate:
            return None
        try:
            value = json.loads(_repair_json(candidate))
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else None
    return None


def _repair_json(text: str) -> str:
    trimmed = text.rstrip()
    stack: list[str] = []
    in_string = False
    escape = False
    for char in trimmed:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()
    if escape:
        trimmed += "\\"
    if in_string:
        trimmed += '"'
    elif trimmed.endswith(":"):
        trimmed += " null"
    elif trimmed.endswith(","):
        trimmed = trimmed[:-1]
    return trimmed + "".join(reversed(stack))


def _shown_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return f"{value:g}"


def _render_partial_estimation(estimation: dict) -> str:
    lines: list[str] = []
    title = estimation.get("project_title")
    if isinstance(title, str) and title:
        lines.extend([f"## Estimación: {title}", ""])
    summary = estimation.get("summary")
    if isinstance(summary, str) and summary:
        lines.extend([summary, ""])
    if estimation.get("is_estimable") is False:
        reason = estimation.get("not_estimable_reason") or "sin motivo."
        lines.append(f"No estimable: {reason}")
        return "\n".join(lines).rstrip()

    tasks = estimation.get("tasks")
    if isinstance(tasks, list):
        rows = [task for task in tasks if isinstance(task, dict) and isinstance(task.get("name"), str) and task.get("name")]
        if rows:
            lines.extend(
                [
                    "### Desglose de tareas",
                    "",
                    "| Tarea | Fase | Rol | Horas | EUR/h | Coste |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for task in rows:
                lines.append(
                    "| {name} | {phase} | {role} | {hours} | {rate} | {cost} |".format(
                        name=task.get("name") or "",
                        phase=task.get("phase") if isinstance(task.get("phase"), str) else "",
                        role=task.get("role") if isinstance(task.get("role"), str) else "",
                        hours=_shown_number(task.get("hours")),
                        rate=_shown_number(task.get("rate_eur")),
                        cost=_shown_number(task.get("cost_eur")),
                    )
                )
            lines.append("")

    hours = _shown_number(estimation.get("total_hours"))
    cost = _shown_number(estimation.get("total_cost_eur"))
    if hours or cost:
        lines.extend([f"**Total estimado: {hours} horas, {cost} EUR**", ""])

    team = estimation.get("team")
    if isinstance(team, list) and any(isinstance(member, dict) and member.get("role") for member in team):
        lines.append("### Equipo recomendado")
        for member in team:
            if not isinstance(member, dict) or not isinstance(member.get("role"), str):
                continue
            count = _shown_number(member.get("count"))
            dedication = _shown_number(member.get("dedication"))
            lines.append(f"- {count} {member['role']} (dedicación {dedication})".strip())
        lines.append("")

    low = _shown_number(estimation.get("duration_weeks_min"))
    high = _shown_number(estimation.get("duration_weeks_max"))
    if low or high:
        lines.extend(["### Duración estimada", f"{low}-{high} semanas", ""])

    assumptions = estimation.get("assumptions")
    if isinstance(assumptions, list) and any(isinstance(item, str) and item for item in assumptions):
        lines.append("### Supuestos")
        lines.extend(f"- {item}" for item in assumptions if isinstance(item, str) and item)
        lines.append("")

    phase_assumptions = estimation.get("phase_assumptions")
    if isinstance(phase_assumptions, list):
        shown = False
        for entry in phase_assumptions:
            if not isinstance(entry, dict):
                continue
            phase = entry.get("phase")
            items = entry.get("assumptions")
            if not isinstance(phase, str) or not isinstance(items, list):
                continue
            useful = [item for item in items if isinstance(item, str) and item]
            if not useful:
                continue
            if not shown:
                lines.append("### Supuestos por fase")
                shown = True
            lines.append(f"**{phase}**")
            lines.extend(f"- {item}" for item in useful)
        if shown:
            lines.append("")

    risks = estimation.get("risks")
    if isinstance(risks, list) and any(isinstance(item, str) and item for item in risks):
        lines.append("### Riesgos")
        lines.extend(f"- {item}" for item in risks if isinstance(item, str) and item)

    return "\n".join(lines).rstrip()


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
    if estimation.phases:
        lines.extend(["", "### Fases", "", "| Fase | Horas | Coste | Semanas |", "| --- | --- | --- | --- |"])
        for phase in estimation.phases:
            lines.append(f"| {phase.phase} | {phase.hours:g} | {phase.cost_eur:g} | {phase.weeks:g} |")
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
    if estimation.phase_assumptions:
        lines.extend(["", "### Supuestos por fase"])
        for entry in estimation.phase_assumptions:
            lines.append(f"**{entry.phase}**")
            lines.extend(f"- {item}" for item in entry.assumptions)
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
