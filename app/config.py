"""Configuración cargada una sola vez desde el entorno y desde .env."""

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
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

    CONTEXT_FORMAT: Literal["markdown", "plain", "json"] = "markdown"
    CONTEXT_DETAIL_LEVEL: Literal["summary", "standard", "full"] = "standard"
    CONTEXT_MAX_EXAMPLES: int = Field(7, gt=0)
    CONTEXT_MAX_AGE_YEARS: int = Field(4, ge=0)
    CONTEXT_MAX_TOKENS: int = Field(6000, gt=0)

    MODEL_CONTEXT_WINDOW: int = Field(128_000, gt=0)
    CONTEXT_SAFETY_MARGIN_TOKENS: int = Field(5_000, ge=0)
    TRANSCRIPTION_MAX_CHARS: int = Field(40_000, gt=50)

    CONVERSATION_MAX_TURNS: int = Field(10, gt=0)
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
