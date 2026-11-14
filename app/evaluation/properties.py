"""Propiedades verificables de una estimación. Funciones puras, sin LLM.

Con LLMs no se testea igualdad («16 horas») sino propiedades («el rango es
plausible», «el schema es válido», «no propone lo que el usuario descartó»).
Cada función recibe un ``Subject`` y devuelve un ``PropertyResult``.

Las usan tres sitios con la misma definición:

- ``tests/test_properties.py``: casos buenos y malos fijos (prueban el código).
- ``evals/``: la suite contra el modelo real sobre el golden dataset.
- ``app/evaluation/metrics.py``: puntuaciones online en cada petición real.

Las que necesitan un caso esperado (``expected``) solo tienen sentido offline.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.context.examples import RATE_CARD
from app.schemas.estimation import Estimation
from app.schemas.tiers import DeveloperEstimate, ExecutiveEstimate, PmEstimate, phase_key
from app.tiers import resolve_tier_config

MAX_TOTAL_HOURS = 100_000  # Por encima, el modelo se ha desbocado.
EXPECTED_TOLERANCE = 0.5  # ±50 % sobre el rango esperado: primera pasada generosa.
MAX_COMPONENTS = 15


@dataclass(frozen=True)
class PropertyResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Subject:
    """Lo que se evalúa: la vista del tier y, si la hay, la estimación común validada."""

    tier: str
    view: BaseModel
    estimation: Estimation | None = None
    memory: dict | None = None
    expected: dict = field(default_factory=dict)


def _ok(name: str, passed: bool, detail: str = "") -> PropertyResult:
    return PropertyResult(name=name, passed=bool(passed), detail="" if passed else detail)


def _mentions(texts: list[str], term: str) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(term.strip())}(?!\w)", re.IGNORECASE)
    return any(pattern.search(text) for text in texts)


def _work_texts(subject: Subject) -> list[str]:
    """Lo que el sistema propone construir: tareas, grupos y, en developer, componentes y stack."""
    texts: list[str] = []
    if subject.estimation is not None and subject.estimation.is_estimable:
        texts += [task.name for task in subject.estimation.tasks]
        texts += [task.phase for task in subject.estimation.tasks]
    view = subject.view
    if isinstance(view, DeveloperEstimate):
        texts += [component.name for component in view.components]
        texts += [item for component in view.components for item in component.stack_assumptions]
        texts += [line.name for component in view.components for line in component.work]
    elif isinstance(view, PmEstimate):
        texts += [phase.name for phase in view.phases]
        texts += [item.name for item in view.milestones]
        texts += [item.deliverable for item in view.milestones]
    return texts


def _risk_texts(view: BaseModel) -> list[str]:
    """Las secciones de riesgo de cada vista, y nada más."""
    if isinstance(view, ExecutiveEstimate):
        return list(view.top_three_risks)
    if isinstance(view, DeveloperEstimate):
        technical = [item for component in view.components for item in component.technical_risks]
        return [*view.risks, *technical, *view.uncertainty_drivers]
    if isinstance(view, PmEstimate):
        return [*view.risks, *view.blockers]
    return []


def _total_hours(view: BaseModel) -> float | None:
    if isinstance(view, (DeveloperEstimate, PmEstimate)):
        return view.total_hours
    return None


# --- Comunes ------------------------------------------------------------------


def schema_valid(subject: Subject) -> PropertyResult:
    schema = resolve_tier_config(subject.tier).view_schema
    try:
        schema.model_validate(subject.view.model_dump())
    except ValidationError as exc:
        return _ok("schema_valid", False, str(exc.errors()[:2]))
    return _ok("schema_valid", isinstance(subject.view, schema), f"vista {type(subject.view).__name__}")


def required_text_present(subject: Subject) -> PropertyResult:
    view = subject.view
    missing = [name for name in ("project_title", "summary") if not getattr(view, name, "").strip()]
    if not view.is_estimable and not (view.not_estimable_reason or "").strip():
        missing.append("not_estimable_reason")
    return _ok("required_text_present", not missing, f"vacíos: {missing}")


def ranges_ordered(subject: Subject) -> PropertyResult:
    bad = [
        name
        for name, value in subject.view
        if isinstance(value, BaseModel) and {"min", "max"} <= set(type(value).model_fields)
        and not (0 <= value.min <= value.max)
    ]
    return _ok("ranges_ordered", not bad, f"rangos incoherentes: {bad}")


def hours_within_absolute_bounds(subject: Subject) -> PropertyResult:
    if not subject.view.is_estimable:
        return _ok("hours_within_absolute_bounds", True)
    total = _total_hours(subject.view)
    if total is None and subject.estimation is not None:
        total = subject.estimation.total_hours
    if total is None:
        return _ok("hours_within_absolute_bounds", True)
    return _ok("hours_within_absolute_bounds", 0 < total <= MAX_TOTAL_HOURS, f"total {total} h")


def _estimable_tasks(subject: Subject) -> list:
    """Tareas a juzgar: ninguna si no hay estimación o no es estimable (no hay desglose que valga)."""
    if subject.estimation is None or not subject.estimation.is_estimable:
        return []
    return subject.estimation.tasks


def hours_multiple_of_5(subject: Subject) -> PropertyResult:
    tasks = _estimable_tasks(subject)
    bad = [task.name for task in tasks if task.hours <= 0 or task.hours % 5]
    return _ok("hours_multiple_of_5", not bad, f"tareas: {bad}")


def rates_within_card(subject: Subject) -> PropertyResult:
    tasks = _estimable_tasks(subject)
    bad = []
    for task in tasks:
        entry = RATE_CARD.get(task.role)
        if entry is None or not entry["min_eur_hour"] <= task.rate_eur <= entry["max_eur_hour"]:
            bad.append(f"{task.role}={task.rate_eur}")
    return _ok("rates_within_card", not bad, f"fuera de tabla: {bad}")


# --- Por tier -----------------------------------------------------------------


def developer_components(subject: Subject) -> PropertyResult:
    view = subject.view
    if not isinstance(view, DeveloperEstimate) or not view.is_estimable:
        return _ok("developer_components", True)
    names_ok = all(component.name.strip() for component in view.components)
    count_ok = 1 <= len(view.components) <= MAX_COMPONENTS
    return _ok("developer_components", names_ok and count_ok, f"{len(view.components)} componentes")


def pm_milestones(subject: Subject) -> PropertyResult:
    view = subject.view
    if not isinstance(view, PmEstimate) or not view.is_estimable:
        return _ok("pm_milestones", True)
    phases = {phase_key(phase.name) for phase in view.phases}
    orphans = [item.name for item in view.milestones if phase_key(item.phase) not in phases]
    passed = bool(view.milestones) and bool(view.phases) and not orphans
    return _ok("pm_milestones", passed, f"hitos: {len(view.milestones)}, huérfanos: {orphans}")


def executive_headline(subject: Subject) -> PropertyResult:
    view = subject.view
    if not isinstance(view, ExecutiveEstimate) or not view.is_estimable:
        return _ok("executive_headline", True)
    passed = (
        len(view.top_three_risks) == 3
        and bool(view.go_no_go_recommendation.strip())
        and view.headline_cost_range.max > 0
    )
    return _ok("executive_headline", passed, f"riesgos: {len(view.top_three_risks)}")


# --- Contra la memoria --------------------------------------------------------


def respects_rejected_options(subject: Subject) -> PropertyResult:
    rejected = (subject.memory or {}).get("rejected_options") or []
    if not rejected or not subject.view.is_estimable:
        return _ok("respects_rejected_options", True)
    # Solo lo que se propone construir: un riesgo o supuesto que diga «sin Rails» no cuenta.
    texts = _work_texts(subject)
    proposed = [option for option in rejected if _mentions(texts, option)]
    return _ok("respects_rejected_options", not proposed, f"vuelve a proponer: {proposed}")


def respects_team_size(subject: Subject) -> PropertyResult:
    size = (subject.memory or {}).get("assumed_team_size")
    team = getattr(subject.view, "team", None) or getattr(subject.view, "team_composition", None)
    if not size or not team or not subject.view.is_estimable:
        return _ok("respects_team_size", True)
    headcount = sum(member.count for member in team)
    return _ok("respects_team_size", abs(headcount - size) <= 1, f"equipo {headcount:g} frente a {size}")


# --- Contra el caso esperado (offline) ------------------------------------------


def estimable_matches(subject: Subject) -> PropertyResult:
    expected = subject.expected.get("estimable")
    if expected is None:
        return _ok("estimable_matches", True)
    return _ok("estimable_matches", subject.view.is_estimable == bool(expected), f"esperado {expected}")


def hours_in_expected_range(subject: Subject) -> PropertyResult:
    rng = subject.expected.get("hours_range")
    total = _total_hours(subject.view)
    if total is None and subject.estimation is not None:
        total = subject.estimation.total_hours
    if not rng or total is None or not subject.view.is_estimable:
        return _ok("hours_in_expected_range", True)
    low, high = rng
    passed = low * (1 - EXPECTED_TOLERANCE) <= total <= high * (1 + EXPECTED_TOLERANCE)
    return _ok("hours_in_expected_range", passed, f"{total} h frente a {low}-{high}")


def _any_of(name: str, subject: Subject, key: str, texts: list[str]) -> PropertyResult:
    terms = subject.expected.get(key) or []
    if not terms or not subject.view.is_estimable:
        return _ok(name, True)
    found = [term for term in terms if _mentions(texts, term)]
    return _ok(name, bool(found), f"ninguno de {terms}")


def expected_components_present(subject: Subject) -> PropertyResult:
    return _any_of("expected_components_present", subject, "components_any_of", _work_texts(subject))


def expected_risks_present(subject: Subject) -> PropertyResult:
    return _any_of("expected_risks_present", subject, "risks_any_of", _risk_texts(subject.view))


ONLINE: tuple[Callable[[Subject], PropertyResult], ...] = (
    schema_valid,
    required_text_present,
    ranges_ordered,
    hours_within_absolute_bounds,
    hours_multiple_of_5,
    rates_within_card,
    developer_components,
    pm_milestones,
    executive_headline,
    respects_rejected_options,
    respects_team_size,
)

OFFLINE: tuple[Callable[[Subject], PropertyResult], ...] = (
    estimable_matches,
    hours_in_expected_range,
    expected_components_present,
    expected_risks_present,
)


def run_properties(subject: Subject, *, offline: bool = False) -> list[PropertyResult]:
    checks = ONLINE + (OFFLINE if offline else ())
    return [check(subject) for check in checks]
