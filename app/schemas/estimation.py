"""Contrato de la API de estimación y salida estructurada del modelo."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings

_settings = get_settings()


class ProjectType(StrEnum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(StrEnum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(StrEnum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class PromptRenderRequest(BaseModel):
    """Datos que rellenan la plantilla. El endpoint v2 los tomará del formulario."""

    description: str
    project_type: ProjectType | None = None
    detail_level: DetailLevel = DetailLevel.MEDIUM
    output_format: OutputFormat = OutputFormat.PHASES_TABLE


class EstimationRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=50,
        max_length=_settings.TRANSCRIPTION_MAX_CHARS,
        description="Transcripción de la reunión con el cliente",
    )


class FormEstimationRequest(BaseModel):
    """Contrato del formulario tipado (API v2)."""

    description: str = Field(
        ...,
        min_length=50,
        max_length=_settings.TRANSCRIPTION_MAX_CHARS,
        description="Descripción o transcripción del proyecto a estimar",
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat


class PromptVersionsResponse(BaseModel):
    versions: list[str]
    default: str


class DraftTask(BaseModel):
    name: str
    phase: str
    role: str
    hours: float = Field(ge=0)
    rate_eur: float = Field(ge=0)
    cost_eur: float = Field(ge=0)


class DraftTeamMember(BaseModel):
    role: str
    count: float = Field(ge=0)
    dedication: float = Field(ge=0, le=1)


class PhaseAssumptions(BaseModel):
    phase: str
    assumptions: list[str] = Field(default_factory=list)


class PhaseSummary(BaseModel):
    phase: str
    hours: float
    cost_eur: float
    weeks: float


class EstimationDraft(BaseModel):
    project_title: str
    summary: str
    is_estimable: bool = True
    not_estimable_reason: str | None = None
    tasks: list[DraftTask] = Field(default_factory=list)
    total_hours: float = 0
    total_cost_eur: float = 0
    team: list[DraftTeamMember] = Field(default_factory=list)
    duration_weeks_min: float = 0
    duration_weeks_max: float = 0
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    phase_assumptions: list[PhaseAssumptions] = Field(default_factory=list)


class Estimation(EstimationDraft):
    """Estimación ya validada. Los costes, totales y fases son los corregidos."""

    phases: list[PhaseSummary] = Field(default_factory=list)


class TurnOutput(BaseModel):
    kind: Literal["estimation", "estimation_update", "answer"]
    message: str | None = None
    estimation: EstimationDraft | None = None


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    field: str | None = None
    original_value: str | None = None
    corrected_value: str | None = None


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int | None = None


class ContextMeta(BaseModel):
    format: str
    detail_level: str
    examples: list[str]
    prompt_version: str


class EstimationResponse(BaseModel):
    estimation: str
    model: str
    provider: str
    gateway: str = "litellm"
    estimation_structured: Estimation
    warnings: list[ValidationIssue]
    prompt_version: str
    context: ContextMeta
    token_budget: "TokenBudget"
    usage: UsageInfo
    cost_usd: float | None = None
    latency_ms: float
    cache_hit: bool = False  # Sesión 3 · caché: true si no hubo llamada al modelo.
    trace_id: str | None = None  # Observabilidad. Vacío si Langfuse está apagado.
    created_at: datetime


class ErrorResponse(BaseModel):
    error: str
    detail: str


class PromptMessage(BaseModel):
    role: str
    content: str | list[dict]


class PromptPreview(BaseModel):
    messages: list[PromptMessage]
    prompt_version: str
    token_budget: "TokenBudget"


from app.schemas.context import TokenBudget  # noqa: E402

EstimationResponse.model_rebuild()
PromptPreview.model_rebuild()
