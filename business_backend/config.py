"""Configuración del backend de negocio. Independiente de ``app.config``."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BusinessSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BUSINESS_DB_PATH: str = "business_backend/business.db"
    # Secreto compartido con el estimador para firmar el JWT del usuario (HS256).
    AI_SERVICE_SECRET: SecretStr = SecretStr("")
    # Token de servicio a servicio para la API interna. No es el token de un usuario.
    BUSINESS_INTERNAL_TOKEN: SecretStr = SecretStr("")
    USER_TOKEN_MINUTES: int = Field(15, gt=0)

    @model_validator(mode="after")
    def secrets_are_long(self) -> BusinessSettings:
        if len(self.AI_SERVICE_SECRET.get_secret_value()) < 32:
            raise ValueError("AI_SERVICE_SECRET debe tener al menos 32 caracteres.")
        if len(self.BUSINESS_INTERNAL_TOKEN.get_secret_value()) < 32:
            raise ValueError("BUSINESS_INTERNAL_TOKEN debe tener al menos 32 caracteres.")
        return self


@lru_cache
def get_business_settings() -> BusinessSettings:
    return BusinessSettings()
