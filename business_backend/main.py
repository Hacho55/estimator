"""API del backend de negocio.

- ``GET /api/users``: usuarios de demostración (para el selector del front).
- ``POST /api/auth/demo-login``: JWT con el tier del usuario, firmado para el estimador.
- ``PATCH /api/me/tier``: el usuario cambia su perfil (un developer puede pedir la
  vista ejecutiva); devuelve un token nuevo. El tier cambia aquí, no en el cuerpo de
  la petición al estimador.
- ``POST /api/internal/similar_projects``: proyectos cerrados parecidos y sus métricas
  reales. Solo con el token de servicio del estimador.

Autenticación de demostración: no hay contraseñas. La real queda fuera de alcance.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from business_backend import db, rules
from business_backend.auth import issue_user_token, require_internal_token, user_from_token
from business_backend.config import get_business_settings

Tier = Literal["developer", "pm", "executive"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db(get_business_settings().BUSINESS_DB_PATH)
    yield


app = FastAPI(title="Backend de negocio (demo)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["content-type", "authorization"],
)


class User(BaseModel):
    id: str
    name: str
    tier: Tier


class LoginRequest(BaseModel):
    user_id: str


class TokenResponse(BaseModel):
    token: str
    expires_at: int
    user: User


class TierChange(BaseModel):
    tier: Tier


class SimilarProjectsRequest(BaseModel):
    technologies: list[str] = Field(default_factory=list, max_length=30)
    scope_summary: str = Field(min_length=3, max_length=2000)
    team_size: int | None = Field(default=None, ge=1, le=50)


def _path() -> str:
    return get_business_settings().BUSINESS_DB_PATH


def _token_for(row) -> TokenResponse:
    token, expires = issue_user_token(row["id"], row["tier"])
    return TokenResponse(token=token, expires_at=expires, user=User(**dict(row)))


@app.get("/api/users", response_model=list[User])
def list_users() -> list[User]:
    return [User(**dict(row)) for row in db.list_users(_path())]


@app.post("/api/auth/demo-login", response_model=TokenResponse)
def demo_login(request: LoginRequest) -> TokenResponse:
    row = db.get_user(_path(), request.user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Usuario desconocido.")
    return _token_for(row)


@app.patch("/api/me/tier", response_model=TokenResponse)
def change_tier(change: TierChange, user_id: str = Depends(user_from_token)) -> TokenResponse:
    row = db.set_tier(_path(), user_id, change.tier)
    if row is None:
        raise HTTPException(status_code=404, detail="Usuario desconocido.")
    return _token_for(row)


@app.post("/api/internal/similar_projects", dependencies=[Depends(require_internal_token)])
def similar_projects(request: SimilarProjectsRequest) -> dict:
    projects = rules.find_similar(
        db.completed_projects(_path()),
        technologies=request.technologies,
        scope_summary=request.scope_summary,
        team_size=request.team_size,
    )
    return {"projects": projects}
