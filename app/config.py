"""Configuración cargada una sola vez desde el entorno y desde .env."""

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Langfuse lee LANGFUSE_* de os.environ. pydantic-settings no las exporta.
load_dotenv()

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Estimador CAG"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    LOG_FORMAT: Literal["json", "text"] = "json"
    LOG_PROMPTS: bool = False

    LITELLM_API_KEY: SecretStr
    LITELLM_BASE_URL: str = "https://litellm.doi.azure.datadope.co"
    LLM_MODEL: str = "openrouter/openai/gpt-4o-mini"
    # Sesión 3 · fallback: vacío no reserva otro modelo. Un ejemplo es
    # openrouter/anthropic/claude-haiku-4.5, distinto del principal.
    LLM_FALLBACK_MODEL: str = ""
    # Sesión 3 · routing: off no elige. complexity puede bajar un texto corto al reserva.
    LLM_ROUTING: Literal["off", "complexity"] = "off"

    LLM_TEMPERATURE: float = Field(0.2, ge=0, le=2)
    LLM_MAX_TOKENS: int = Field(8000, gt=0)
    LLM_TIMEOUT_SECONDS: float = Field(60, gt=0)
    LLM_MAX_RETRIES: int = Field(3, ge=0)
    LLM_RESPONSE_FORMAT: Literal["json_schema", "json_object"] = "json_schema"
    LLM_PROMPT_CACHE: Literal["auto", "off"] = "auto"
    LLM_PROMPT_VERSION: str = "v1"

    # Guardrails: log registra sin bloquear las políticas exception; enforce las aplica.
    GUARDRAILS_MODE: Literal["log", "enforce"] = "log"
    PII_SCORE_THRESHOLD: float = Field(0.5, ge=0, le=1)

    # Caché semántica. log mide sin servir; on sirve aciertos; off la apaga.
    REDIS_URL: str = ""
    SEMANTIC_CACHE_MODE: Literal["off", "log", "on"] = "log"
    SEMANTIC_CACHE_THRESHOLD: float = Field(0.92, ge=0, le=1)
    EMBEDDING_MODEL: str = "openrouter/openai/text-embedding-3-small"
    EMBEDDING_DIMS: int = Field(1536, gt=0)

    CONTEXT_FORMAT: Literal["markdown", "plain", "json"] = "markdown"
    CONTEXT_DETAIL_LEVEL: Literal["summary", "standard", "full"] = "standard"
    CONTEXT_MAX_EXAMPLES: int = Field(7, gt=0)
    CONTEXT_MAX_AGE_YEARS: int = Field(4, ge=0)
    CONTEXT_MAX_TOKENS: int = Field(6000, gt=0)

    MODEL_CONTEXT_WINDOW: int = Field(128_000, gt=0)
    CONTEXT_SAFETY_MARGIN_TOKENS: int = Field(5_000, ge=0)
    TRANSCRIPTION_MAX_CHARS: int = Field(40_000, gt=50)

    CONVERSATION_MAX_TURNS: int = Field(10, gt=0)
    # Sesiones: ventana deslizante sin anclas. Un turno es un par user/assistant.
    SESSION_MAX_TURNS: int = Field(6, gt=0)
    # Extractor de memoria: modelo barato. Vacío = LLM_MODEL.
    METADATA_MODEL: str = ""
    # Revisión Actor-Critic-Boss (fase 5). Se activa por tier en app/tiers.py; esto la apaga entera.
    ACB_ENABLED: bool = True
    CRITIC_MODEL: str = ""  # Vacío = LLM_MODEL. Mejor otra familia que el actor.
    BOSS_MAX_ITERATIONS: int = Field(2, ge=1, le=3)  # Llamadas al actor como máximo.
    # Tiers: el perfil llega en un JWT del backend de negocio (jwt) o es fijo (off).
    AUTH_MODE: Literal["jwt", "off"] = "off"
    DEFAULT_TIER: Literal["developer", "pm", "executive"] = "developer"
    AI_SERVICE_SECRET: SecretStr = SecretStr("")
    ATTACHMENT_MAX_FILES: int = Field(5, gt=0)
    ATTACHMENT_MAX_BYTES: int = Field(5_000_000, gt=0)
    # Texto de todos los adjuntos de un turno. Lo que pase se trunca con aviso (D3).
    ATTACHMENT_MAX_CHARS: int = Field(40_000, gt=0)
    # Texto de adjuntos que ve el extractor de memoria (coste acotado).
    METADATA_ATTACHMENT_CHARS: int = Field(12_000, ge=0)
    # Ciclo de vida: inactiva → archivada (409); archivada → purgada.
    SESSION_IDLE_ARCHIVE_HOURS: float = Field(24, gt=0)
    SESSION_ARCHIVE_RETENTION_HOURS: float = Field(72, gt=0)
    # Tamaño descomprimido máximo de un DOCX (es un zip).
    ATTACHMENT_MAX_EXPANDED_BYTES: int = Field(50_000_000, gt=0)
    CONVERSATION_TTL_MINUTES: int = Field(60, gt=0)
    CONVERSATION_MAX_ACTIVE: int = Field(1000, gt=0)
    CONVERSATION_NEAR_LIMIT_RATIO: float = Field(0.8, gt=0, le=1)

    # Observabilidad. Vacías: el estimador arranca y no envía trazas.
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: SecretStr = SecretStr("")
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"

    @field_validator("LITELLM_BASE_URL", "LANGFUSE_BASE_URL")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def jwt_needs_a_secret(self) -> "Settings":
        # HS256 con un secreto corto se rompe por fuerza bruta: se exige al arrancar.
        if self.AUTH_MODE == "jwt" and len(self.AI_SERVICE_SECRET.get_secret_value()) < 32:
            raise ValueError("AUTH_MODE=jwt exige AI_SERVICE_SECRET de al menos 32 caracteres.")
        return self

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.LANGFUSE_PUBLIC_KEY and self.LANGFUSE_SECRET_KEY.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # El cliente de Langfuse toma el entorno al crearse. Hay que dejarlo en
    # os.environ antes de importar el SDK.
    if settings.langfuse_enabled and not os.environ.get("LANGFUSE_TRACING_ENVIRONMENT"):
        os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = settings.APP_ENV
    if settings.langfuse_enabled:
        # Máscaras de PII antes de que llm_client importe langfuse.openai.
        # Se pasa settings para no reentrar en get_settings (lru_cache aún vacío).
        from app.observability import configure_langfuse

        configure_langfuse(settings)
    return settings


def derive_provider(model: str) -> str:
    """El proveedor se deduce del nombre del modelo, que es lo que enruta LiteLLM."""
    known = {"openai", "anthropic", "google", "gemini", "mistral", "groq", "cohere"}
    parts = [part for part in model.split("/") if part]
    for part in parts:
        if part in known:
            return "google" if part == "gemini" else part
    lowered = model.lower()
    if "claude" in lowered:
        return "anthropic"
    if "gpt" in lowered or "openai" in lowered:
        return "openai"
    return "unknown"
