"""Emisión del JWT de usuario (capa 2 de los tiers) y token de servicio de la API interna."""

from __future__ import annotations

import hmac
import time

import jwt
from fastapi import Header, HTTPException

from business_backend.config import get_business_settings

AUDIENCE = "estimador"  # El JWT es para el estimador: lo comprueba su app/security/caller.py.


def issue_user_token(user_id: str, tier: str) -> tuple[str, int]:
    settings = get_business_settings()
    expires = int(time.time()) + settings.USER_TOKEN_MINUTES * 60
    claims = {"sub": user_id, "tier": tier, "aud": AUDIENCE, "exp": expires}
    token = jwt.encode(claims, settings.AI_SERVICE_SECRET.get_secret_value(), algorithm="HS256")
    return token, expires


def user_from_token(authorization: str | None = Header(default=None)) -> str:
    """Para ``PATCH /api/me/tier``: el usuario se identifica con el token que le dimos."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Falta el token.")
    try:
        claims = jwt.decode(
            token,
            get_business_settings().AI_SERVICE_SECRET.get_secret_value(),
            algorithms=["HS256"],
            audience=AUDIENCE,
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Token no válido.") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=401, detail="Token sin usuario.")
    return subject


def require_internal_token(authorization: str | None = Header(default=None)) -> None:
    """La API interna solo la usa el estimador, con un token de servicio (no el de un usuario)."""
    scheme, _, token = (authorization or "").partition(" ")
    expected = get_business_settings().BUSINESS_INTERNAL_TOKEN.get_secret_value()
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Token interno no válido.")
