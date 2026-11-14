"""Contrato por perfil (tier). Dos niveles por tier:

- **Borrador del modelo** (``*Draft``): lo propio de la audiencia más un desglose de
  trabajo común (``work``) con rol y horas. El modelo no calcula costes, totales
  ni rangos.
- **Vista para el cliente** (``*Estimate``): la calcula el código en
  ``app/services/tier_views.py`` a partir del borrador ya validado.

Un tier que no cambia el schema solo cambia el tono; aquí cambia la estructura.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.estimation import DraftTeamMember


class Tier(StrEnum):
    DEVELOPER = "developer"
    PM = "pm"
    EXECUTIVE = "executive"


# --- Borrador del modelo -----------------------------------------------------


class WorkItem(BaseModel):
    """Una unidad de trabajo. ``group`` es el componente (developer) o la fase (pm, executive)."""

    name: str
    group: str
    role: str = Field(description="Clave de la tabla de tarifas.")
    hours: float = Field(ge=0)


class TierDraftBase(BaseModel):
    project_title: str
    summary: str
    is_estimable: bool = True
    not_estimable_reason: str | None = None
    work: list[WorkItem] = Field(default_factory=list)
    team: list[DraftTeamMember] = Field(default_factory=list)
    duration_weeks_min: float = Field(default=0, ge=0)
    duration_weeks_max: float = Field(default=0, ge=0)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ComponentNote(BaseModel):
    name: str
    technical_risks: list[str] = Field(default_factory=list)
    stack_assumptions: list[str] = Field(default_factory=list)


class DeveloperDraft(TierDraftBase):
    # Obligatorios (aunque puedan ir vacíos): un borrador de otro tier no pasa por este.
    components: list[ComponentNote]
    uncertainty_drivers: list[str]


class Milestone(BaseModel):
    name: str
    phase: str
    deliverable: str


def phase_key(name: str) -> str:
    """Normalización única de nombres de fase: espacios y mayúsculas no cuentan."""
    return " ".join(name.split()).casefold()


class PmDraft(TierDraftBase):
    milestones: list[Milestone]
    blockers: list[str]

    @model_validator(mode="after")
    def _milestones_point_to_work_phases(self) -> PmDraft:
        phases = {phase_key(item.group) for item in self.work}
        unknown = [item.phase for item in self.milestones if phase_key(item.phase) not in phases]
        if self.is_estimable and unknown:
            raise ValueError(f"Hitos con fases que no están en work: {', '.join(unknown)}.")
        return self


ConfidenceLevel = Literal["low", "medium", "high"]


class ExecutiveDraft(TierDraftBase):
    confidence_level: ConfidenceLevel
    confidence_reason: str
    top_three_risks: list[str]
    go_no_go_recommendation: str

    @model_validator(mode="after")
    def _three_risks_when_estimable(self) -> ExecutiveDraft:
        if self.is_estimable and len(self.top_three_risks) != 3:
            raise ValueError("top_three_risks debe tener exactamente tres riesgos.")
        if self.is_estimable and not self.go_no_go_recommendation.strip():
            raise ValueError("Falta go_no_go_recommendation.")
        return self


class _TurnBase(BaseModel):
    kind: Literal["estimation", "estimation_update", "answer"]
    message: str | None = None


class DeveloperTurn(_TurnBase):
    estimation: DeveloperDraft | None = None


class PmTurn(_TurnBase):
    estimation: PmDraft | None = None


class ExecutiveTurn(_TurnBase):
    estimation: ExecutiveDraft | None = None


# --- Vista para el cliente ---------------------------------------------------


class Range(BaseModel):
    min: float
    max: float


class WorkLine(BaseModel):
    name: str
    role: str
    hours: float
    rate_eur: float
    cost_eur: float


class ComponentView(BaseModel):
    name: str
    hours: float
    cost_eur: float
    work: list[WorkLine]
    technical_risks: list[str] = Field(default_factory=list)
    stack_assumptions: list[str] = Field(default_factory=list)


class _ViewBase(BaseModel):
    project_title: str
    summary: str
    is_estimable: bool = True
    not_estimable_reason: str | None = None


class DeveloperEstimate(_ViewBase):
    tier: Literal["developer"] = "developer"
    components: list[ComponentView] = Field(default_factory=list)
    uncertainty_drivers: list[str] = Field(default_factory=list)
    total_hours: float = 0
    total_hours_range: Range = Field(default_factory=lambda: Range(min=0, max=0))
    total_cost_eur: float = 0
    team: list[DraftTeamMember] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class PhaseView(BaseModel):
    name: str
    hours: float
    cost_eur: float
    weeks: float


class MilestoneView(BaseModel):
    name: str
    phase: str
    deliverable: str
    cumulative_cost_eur: float = Field(description="Coste acumulado al cerrar la fase del hito.")


class PmEstimate(_ViewBase):
    tier: Literal["pm"] = "pm"
    phases: list[PhaseView] = Field(default_factory=list)
    milestones: list[MilestoneView] = Field(default_factory=list)
    team_composition: list[DraftTeamMember] = Field(default_factory=list)
    duration_weeks_range: Range = Field(default_factory=lambda: Range(min=0, max=0))
    total_hours: float = 0
    total_cost_eur: float = 0
    blockers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ExecutiveEstimate(_ViewBase):
    tier: Literal["executive"] = "executive"
    headline_cost_range: Range = Field(default_factory=lambda: Range(min=0, max=0))
    headline_duration_range: Range = Field(default_factory=lambda: Range(min=0, max=0))
    confidence_level: ConfidenceLevel = "medium"
    confidence_reason: str = ""
    top_three_risks: list[str] = Field(default_factory=list)
    go_no_go_recommendation: str = ""


TierEstimate = Annotated[
    DeveloperEstimate | PmEstimate | ExecutiveEstimate,
    Field(discriminator="tier"),
]
