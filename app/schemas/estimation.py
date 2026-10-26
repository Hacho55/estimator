"""Contrato de la API de estimación y salida estructurada del modelo."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings

_settings = get_settings()


class EstimationRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=50,
        max_length=_settings.TRANSCRIPTION_MAX_CHARS,
        description="Transcripción de la reunión con el cliente",
    )


class DraftTask(BaseModel):
    name: str
    phase: str
    role: str
    hours: float
    rate_eur: float
    cost_eur: float


class DraftTeamMember(BaseModel):
    role: str
    count: float
    dedication: float


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


class Estimation(EstimationDraft):
    """Estimación ya validada. Los costes y los totales son los corregidos."""


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
