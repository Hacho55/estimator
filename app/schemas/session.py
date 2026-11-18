"""Contrato de la sesión: hechos del proyecto, aparte del historial."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.estimation import UsageInfo, ValidationIssue
from app.schemas.review import ReviewTrace
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

    def fact_count(self) -> int:
        """Hechos guardados: cada campo escalar presente y cada elemento de las listas."""
        scalars = [self.project_name, self.assumed_team_size, self.agreed_scope]
        lists = [self.mentioned_technologies, self.explicit_constraints, self.rejected_options]
        return sum(value is not None and value != "" for value in scalars) + sum(len(items) for items in lists)

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


class ToolUse(BaseModel):
    tool: str
    ok: bool
    ms: float
    error: str | None = None


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


class TurnObservation(BaseModel):
    """Telemetría de un turno de sesión en un solo evento (``turn_observed``).

    Los 13 primeros campos son los del ejercicio de stress de la sesión 6. Dos cambian de
    significado porque ``/sessions`` no tiene anclas ni summary (ADR 0002): lo que sobrevive a
    la ventana es la memoria.

    - ``anchors_count``: hechos guardados en la memoria (``ProjectMetadata.fact_count``).
    - ``summary_chars``: caracteres del bloque de memoria que viajó en el system.

    ``latency_ms``, ``cost_usd`` y los tokens suman todo el turno: investigación, actor (con
    sus reintentos de revisión) y extractor de memoria. El desglose va en los campos extra.
    ``cost_usd`` es ``None`` si algún modelo no tiene precio: desconocido no es gratis.
    """

    turn_index: int = Field(ge=1)
    session_id: str
    enriched_transcript_chars: int = Field(ge=0)  # Transcripción + texto extraído de los adjuntos.
    attachments_total_chars: int = Field(ge=0)  # Tras el truncado; 0 sin adjuntos.
    messages_in_window: int = Field(ge=0)  # Mensajes de historial que entraron al prompt.
    anchors_count: int = Field(ge=0)
    summary_chars: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)  # None: algún modelo sin precio conocido.
    latency_ms: float = Field(ge=0)
    cache_hit_kind: Literal["none", "exact", "semantic"] = "none"
    last_resolved_tier: str | None = None
    # Extras: lo que hace falta para leer las curvas sin otro evento.
    kind: str
    attachments_truncated: bool = False
    dropped_turns: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    estimate_ms: float = Field(default=0, ge=0)  # Investigación + actor (con sus vueltas) + revisión.
    extractor_ms: float = Field(default=0, ge=0)
    tools_used: list[str] = Field(default_factory=list)
    review_decision: str | None = None
    actor_calls: int = Field(default=1, ge=0)  # 0 si la respuesta salió de la caché.
    # Tamaño del prompt del actor (estimación del presupuesto, antes de llamar): el eje de
    # contexto de la segunda corrida del stress test. ``tokens_in`` suma todas las llamadas.
    actor_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)


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
    review: ReviewTrace | None = None  # Actor-Critic-Boss: decisión, vueltas e issues abiertos.
    tools: list[ToolUse] = Field(default_factory=list)  # Herramientas de la fase de investigación.
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
    observation: TurnObservation | None = None  # Telemetría del turno (stress test, sesión 6).
