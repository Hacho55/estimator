"""Modelos de los datos de referencia y del bloque de contexto ya procesado."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RateCardEntry(BaseModel):
    role: str
    rate_eur_hour: float = Field(gt=0)
    min_eur_hour: float = Field(gt=0)
    max_eur_hour: float = Field(gt=0)


class HistoricalTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    phase: str
    role: str
    quantity: float | str
    unit: Literal["hours", "days"] = "hours"


class HistoricalTeamMember(BaseModel):
    role: str
    count: float = Field(gt=0)
    dedication: float = Field(gt=0, le=1)


class HistoricalEstimate(BaseModel):
    """Estimación histórica en bruto. Los campos administrativos no entran al prompt."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    sector: str
    meeting_summary: str
    client_profile: str
    tasks: list[HistoricalTask]
    team: list[HistoricalTeamMember]
    duration_weeks_min: int = Field(gt=0)
    duration_weeks_max: int = Field(gt=0)
    assumptions: list[str]
    risks: list[str]
    year: int
    client_name: str | None = None
    internal_id: str | None = None
    created_at: str | None = None
    sales_rep_email: str | None = None
    payment_terms: str | None = None
    internal_notes: str | None = None
    created_by: str | None = None


class Exclusion(BaseModel):
    id: str
    reason: str


class ContextBlock(BaseModel):
    static_text: str
    examples_text: str
    examples_are_static: bool = True
    detail_level: str
    format: str
    included_ids: list[str]
    excluded: list[Exclusion]
    notes: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.static_text}\n\n{self.examples_text}".strip()


class TokenBlock(BaseModel):
    name: str
    tokens: int


class TokenBudget(BaseModel):
    blocks: list[TokenBlock]
    total_input: int
    reserved_output: int
    safety_margin: int
    window: int
    fits: bool
    exact: bool
    per_thousand_calls: int
    cacheable_tokens: int

    def tokens_of(self, name: str) -> int:
        return next((block.tokens for block in self.blocks if block.name == name), 0)


class ContextInfo(BaseModel):
    examples_included: list[str]
    examples_excluded: list[Exclusion]
    detail_level: str
    format: str
    notes: list[str]
    token_budget: TokenBudget
    prompt_cache_mode: str
    prompt_cache_strategy: str
    prefix_approx_tokens: int
    likely_cacheable: bool
    text: str | None = None
