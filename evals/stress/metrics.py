"""Tres métricas del stress test (bloque 4): presupuestos como contrato y deriva de memoria.

Viven aquí y no en ``app/evaluation/properties.py`` porque operan sobre otra cosa: la
observación de un turno (``TurnObservation``) y el snapshot de ``GET /sessions/{id}``, no
sobre la vista del tier. ``properties.py`` es código de producción (puntuaciones online);
esto es instrumentación del ejercicio.

Determinismo antes que sofisticación: coincidencia exacta sin distinguir mayúsculas, sin
embeddings ni juez. Cuando dice que el hecho no está, no está.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from app.schemas.session import TurnObservation
from evals.stress.scenarios import Expect, Fact, Scenario, Where


@dataclass(frozen=True)
class MetricResult:
    name: str
    score: float
    passed: bool
    details: str = ""


def _field(observation: TurnObservation | dict, name: str) -> Any:
    return getattr(observation, name) if isinstance(observation, TurnObservation) else observation.get(name)


class LatencyBudgetMetric:
    """1.0 si ``latency_ms`` ≤ presupuesto. El SLA convertido en test."""

    name = "latency_budget"

    def __init__(self, budget_ms: float) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms debe ser positivo")
        self.budget_ms = budget_ms

    def evaluate(self, observation: TurnObservation | dict) -> MetricResult:
        value = _field(observation, "latency_ms")
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            return MetricResult(self.name, 0.0, False, "latencia desconocida")
        latency = float(value)
        passed = latency <= self.budget_ms
        return MetricResult(self.name, float(passed), passed, f"{latency:.0f} ms / {self.budget_ms:.0f} ms")


class CostBudgetMetric:
    """1.0 si ``cost_usd`` del turno ≤ presupuesto. Un coste desconocido no cumple: no es gratis."""

    name = "cost_budget"

    def __init__(self, budget_usd: float) -> None:
        if budget_usd <= 0:
            raise ValueError("budget_usd debe ser positivo")
        self.budget_usd = budget_usd

    def evaluate(self, observation: TurnObservation | dict) -> MetricResult:
        cost = _field(observation, "cost_usd")
        if cost is None:
            return MetricResult(self.name, 0.0, False, "coste desconocido")
        passed = float(cost) <= self.budget_usd
        return MetricResult(self.name, float(passed), passed, f"{float(cost):.4f} / {self.budget_usd:.4f} USD")


class MemoryDriftMetric:
    """1.0 si el hecho declarado en el turno k sigue (o, con ``absent``, ya no está) en el turno N ≥ k.

    Busca en el snapshot de la sesión (la memoria; ``/sessions`` no tiene anclas ni summary,
    ver ADR 0002) o, con ``where="answer"``, en el texto de la última respuesta.
    """

    name = "memory_drift"

    def __init__(self, fact: Fact | str, where: Where = "memory", expect: Expect = "present") -> None:
        if isinstance(fact, str):
            fact = Fact(key=fact, variants=(fact,), where=where, expect=expect)
        self.fact = fact

    def evaluate(self, snapshot: dict, answer: str = "") -> MetricResult:
        haystack = _haystack(snapshot, self.fact.where, answer)
        if self.fact.where == "team":
            found = haystack in {variant.strip() for variant in self.fact.variants}
        else:
            lowered = haystack.lower()
            found = any(variant.lower() in lowered for variant in self.fact.variants)
        passed = found if self.fact.expect == "present" else not found
        state = "está" if found else "no está"
        return MetricResult(self.name, float(passed), passed, f"{self.fact.key} ({self.fact.where}, {self.fact.expect}): {state}")


def _haystack(snapshot: dict, where: Where, answer: str) -> str:
    memory = snapshot.get("project_metadata") or {}
    if where == "answer":
        return answer
    if where == "project_name":
        return str(memory.get("project_name") or "")
    if where == "team":
        return str(memory.get("assumed_team_size") or "")
    if where == "scope":
        return str(memory.get("agreed_scope") or "")
    lists = {"technologies": "mentioned_technologies", "rejected": "rejected_options", "constraints": "explicit_constraints"}
    if where in lists:
        return " | ".join(memory.get(lists[where]) or [])
    # "memory": toda la memoria, sin el registro de cambios (allí siguen los valores retirados).
    return json.dumps(memory, ensure_ascii=False)


@dataclass(frozen=True)
class DriftSummary:
    checked: int
    passed: int
    first_fact_kept: bool | None  # El hecho del turno 1 (el nombre del proyecto), si lo hay.
    failures: tuple[str, ...]

    @property
    def recall(self) -> float | None:
        return None if self.checked == 0 else self.passed / self.checked


def drift_for_turn(scenario: Scenario, turn_index: int, snapshot: dict, answer: str = "") -> DriftSummary:
    """Evalúa todos los hechos vigentes tras ``turn_index`` (incluidos los del propio turno)."""
    results = [(fact, MemoryDriftMetric(fact).evaluate(snapshot, answer)) for fact in scenario.active_facts(turn_index)]
    first = scenario.turns[0].facts[0].key if scenario.turns and scenario.turns[0].facts else None
    first_kept = next((result.passed for fact, result in results if fact.key == first), None)
    return DriftSummary(
        checked=len(results),
        passed=sum(result.passed for _, result in results),
        first_fact_kept=first_kept,
        failures=tuple(result.details for _, result in results if not result.passed),
    )
