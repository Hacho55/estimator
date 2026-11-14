"""Contrato de la sesión: hechos del proyecto, aparte del historial."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.estimation import UsageInfo, ValidationIssue
from app.schemas.tiers import TierEstimate

# Límites de la memoria. Un valor fuera de ellos es una extracción sospechosa.
MAX_NAME_CHARS = 120
MAX_SCOPE_CHARS = 2000
MAX_ITEM_CHARS = 200
MAX_LIST_ITEMS = 20
MAX_TEAM_SIZE = 50


class ProjectMetadata(BaseModel):
    """Memoria de la sesión: hechos destilados del proyecto, aparte del historial.

    Sobrevive al recorte de la ventana. Solo la cambia ``app/state/memory.py``.
    """

    project_name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    assumed_team_size: int | None = Field(default=None, ge=1, le=MAX_TEAM_SIZE)
    mentioned_technologies: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    agreed_scope: str | None = Field(default=None, max_length=MAX_SCOPE_CHARS)
    explicit_constraints: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    rejected_options: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)

    def is_empty(self) -> bool:
        return (
            not self.project_name
            and self.assumed_team_size is None
            and not self.mentioned_technologies
            and not self.agreed_scope
            and not self.explicit_constraints
            and not self.rejected_options
        )

    def as_prompt_block(self) -> str:
        """Bloque en JSON para la plantilla ``v3``. ``v4`` renderiza campo a campo."""
        if self.is_empty():
            return ""
        return self.model_dump_json()


class MetadataUpdate(BaseModel):
    """Delta que devuelve el extractor: qué fijar, qué añadir y qué retirar.

    Estricto: un JSON con otra forma no es una extracción válida. ``null`` o una
    lista vacía significan «sin cambios en este campo».
    """

    model_config = ConfigDict(extra="forbid")

    set_project_name: str | None = None
    set_team_size: int | None = None
    set_agreed_scope: str | None = None
    add_technologies: list[str] = Field(default_factory=list)
    remove_technologies: list[str] = Field(default_factory=list)
    add_constraints: list[str] = Field(default_factory=list)
    remove_constraints: list[str] = Field(default_factory=list)
    add_rejected_options: list[str] = Field(default_factory=list)
    evidence: str | None = None

    def is_empty(self) -> bool:
        return not any(
            [
                self.set_project_name,
                self.set_team_size is not None,
                self.set_agreed_scope,
                self.add_technologies,
                self.remove_technologies,
                self.add_constraints,
                self.remove_constraints,
                self.add_rejected_options,
            ]
        )


class MemoryChange(BaseModel):
    """Una línea de auditoría: qué hecho cambió, en qué turno y con qué evidencia."""

    turn: int
    field: str
    before: str | int | list[str] | None = None
    after: str | int | list[str] | None = None
    evidence: str | None = None


class SessionCreateRequest(BaseModel):
    """Cuerpo opcional de ``POST /sessions``: reset (archiva la anterior) y herencia de memoria."""

    previous_session_id: str | None = None
    inherit_memory: bool = False


class SessionCreated(BaseModel):
    session_id: str
    inherited_from: str | None = None


class AttachmentInfo(BaseModel):
    name: str
    pages: int
    chars: int
    truncated: bool
    ms: float


class SessionState(BaseModel):
    """Lo auditable de una sesión: memoria, cambios y ciclo de vida. No incluye el historial."""

    session_id: str
    status: Literal["active", "archived"]
    archived_reason: str | None = None
    inherited_from: str | None = None
    turns: int
    window_turns: int
    tier: str | None = None
    project_metadata: ProjectMetadata
    memory_log: list[MemoryChange]
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class SessionEstimateResponse(BaseModel):
    session_id: str
    project_metadata: ProjectMetadata
    memory_changes: list[MemoryChange] = Field(default_factory=list)
    kind: Literal["estimation", "estimation_update", "answer"]
    message: str | None = None
    tier: Literal["developer", "pm", "executive"] | None = None
    estimation: str | None = None  # Markdown de la vista del tier.
    estimation_structured: TierEstimate | None = None
    warnings: list[ValidationIssue]
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    model: str
    provider: str
    gateway: str = "litellm"
    prompt_version: str
    usage: UsageInfo
    cost_usd: float | None = None
    latency_ms: float
    cache_hit: bool = False
    cache_kind: str | None = None
    cache_similarity: float | None = None
    trace_id: str | None = None
