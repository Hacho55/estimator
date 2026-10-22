"""Contrato del modo conversacional. El estado no vive en el servicio."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.context import TokenBudget
from app.schemas.estimation import Estimation, UsageInfo, ValidationIssue


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    kind: str | None = None
    anchor: bool = False


class ConversationCreateRequest(BaseModel):
    transcription: str = Field(
        ...,
        min_length=50,
        description="Transcripción de la reunión con la que empieza la conversación",
    )


class ConversationMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Ajuste o pregunta sobre la estimación")


class ConversationState(BaseModel):
    turns_total: int
    turns_in_window: int
    turns_dropped: int
    approx_input_tokens: int
    near_limit: bool


class ConversationTurnResponse(BaseModel):
    conversation_id: str
    turn: int
    kind: Literal["estimation", "estimation_update", "answer"]
    message: str | None = None
    estimation: str | None = None
    estimation_structured: Estimation | None = None
    warnings: list[ValidationIssue]
    conversation_state: ConversationState
    model: str
    provider: str
    gateway: str = "litellm"
    prompt_version: str
    token_budget: TokenBudget
    usage: UsageInfo
    cost_usd: float | None = None
    latency_ms: float
    trace_id: str | None = None


class ConversationDetail(BaseModel):
    conversation_id: str
    messages: list[ChatMessage]
    conversation_state: ConversationState
