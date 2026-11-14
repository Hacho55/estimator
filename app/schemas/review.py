"""Contrato de la revisión Actor-Critic-Boss (fase 5).

El feedback del crítico es un schema, nunca prosa (antipatrón 2): categoría,
severidad y campo afectado. El Boss decide leyendo estos campos.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Severity = Literal["critical", "major", "minor"]
BossDecision = Literal["accept", "iterate", "synthesize"]

# Solo lo que el código no puede comprobar. Aritmética, tarifas y forma ya las cubren
# el postprocesador y las propiedades deterministas.
CriticCategory = Literal[
    "scope_mismatch",
    "missing_component",
    "hallucination",
    "inconsistency_with_metadata",
    "risk_incoherence",
    "tier_mismatch",
]

BLOCKING: frozenset[str] = frozenset({"critical", "major"})


class CriticIssue(BaseModel):
    """Un defecto señalado por el crítico LLM."""

    category: CriticCategory
    severity: Severity
    field_path: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=5, max_length=500)
    suggested_fix: str | None = Field(default=None, max_length=300)


class CriticFeedback(BaseModel):
    """Salida del crítico LLM. Validadores: no se pide iterar sin un defecto que bloquee."""

    verdict: Literal["accept", "needs_iteration", "reject"]
    issues: list[CriticIssue] = Field(default_factory=list, max_length=12)
    confidence_in_review: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _verdict_matches_issues(self) -> CriticFeedback:
        blocking = [issue for issue in self.issues if issue.severity in BLOCKING]
        if self.verdict == "needs_iteration" and not blocking:
            raise ValueError("needs_iteration exige al menos un issue critical o major.")
        if self.verdict == "reject" and not self.issues:
            raise ValueError("reject exige al menos un issue que explique por qué.")
        return self


class ReviewIssue(BaseModel):
    """Issue ya unificado: del crítico LLM (``source=critic``) o de una propiedad (``source=property``)."""

    source: Literal["critic", "property"]
    category: str
    severity: Severity
    field_path: str
    description: str
    suggested_fix: str | None = None

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING


class ReviewIteration(BaseModel):
    iteration: int
    verdict: str
    decision: BossDecision
    issues: list[ReviewIssue] = Field(default_factory=list)
    critic_ok: bool = True
    actor_repairs: int = 0  # Reparaciones de JSON dentro del intento (no cuentan en el tope).
    actor_ms: float = 0
    critic_ms: float = 0


class ReviewTrace(BaseModel):
    """Auditoría de la revisión: qué se decidió en cada vuelta y qué quedó abierto."""

    decision: BossDecision
    iterations: list[ReviewIteration] = Field(default_factory=list)
    actor_calls: int = 1  # Intentos del actor (el tope BOSS_MAX_ITERATIONS); sin contar reparaciones.
    open_issues: list[ReviewIssue] = Field(default_factory=list)
    review_ms: float = 0
