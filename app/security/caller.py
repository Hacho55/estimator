"""Capa 2 de los tiers: validar el canal por el que llega el perfil.

El backend de negocio guarda ``users.tier`` y emite un JWT HS256 firmado con
``AI_SERVICE_SECRET`` (claims ``sub``, ``tier``, ``exp``, ``aud``). El estimador
solo lo valida: firma, caducidad, audiencia y que el tier exista.

``AUTH_MODE=off`` (tests y desarrollo sin backend) usa ``DEFAULT_TIER``. En ningún
modo se lee el tier del cuerpo de la petición: un cliente no puede pedir el perfil
más caro escribiendo ``tier=executive``.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header

from app.config import get_settings
from app.schemas.tiers import Tier
from app.services.errors import AuthenticationError

JWT_ALGORITHM = "HS256"
JWT_AUDIENCE = "estimador"


@dataclass(frozen=True)
class CallerContext:
    user_id: str
    tier: Tier


def caller_from_token(token: str) -> CallerContext:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.AI_SERVICE_SECRET.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("El token ha caducado.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Token no válido.") from exc
    try:
        tier = Tier(payload.get("tier"))
    except ValueError as exc:
        raise AuthenticationError("El token no trae un tier conocido.") from exc
    return CallerContext(user_id=str(payload["sub"]), tier=tier)


def get_caller(authorization: str | None = Header(default=None)) -> CallerContext:
    """Dependencia de FastAPI: quién llama y con qué perfil."""
    settings = get_settings()
    if settings.AUTH_MODE == "off":
        return CallerContext(user_id="anonymous", tier=Tier(settings.DEFAULT_TIER))
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Falta el token (Authorization: Bearer).")
    return caller_from_token(token.strip())
