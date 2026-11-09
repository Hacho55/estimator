"""Contrato de la sesión: hechos del proyecto, aparte del historial."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.estimation import Estimation, UsageInfo, ValidationIssue


class ProjectMetadata(BaseModel):
    project_name: str | None = None
    assumed_team_size: int | None = None
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = None

    def is_empty(self) -> bool:
        return (
            not self.project_name
            and self.assumed_team_size is None
            and not self.mentioned_technologies
            and not self.agreed_scope
        )

    def as_prompt_block(self) -> str:
        """Texto del bloque. Vacío en el primer turno, cuando aún no hay hechos."""
        if self.is_empty():
            return ""
        return self.model_dump_json()


class SessionCreated(BaseModel):
    session_id: str


class SessionEstimateResponse(BaseModel):
    session_id: str
    project_metadata: ProjectMetadata
    kind: Literal["estimation", "estimation_update", "answer"]
    message: str | None = None
    estimation: str | None = None
    estimation_structured: Estimation | None = None
    warnings: list[ValidationIssue]
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
