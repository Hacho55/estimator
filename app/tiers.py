"""Configuración por perfil (tier). Un único sitio, sin lógica de negocio.

Cada tier declara su plantilla, sus dos schemas (lo que devuelve el modelo y lo
que ve el cliente) y los pasos de su pipeline:

- ``research``: herramientas antes de estimar (fase 1). ``tools`` dice cuáles.
- ``review``: ``"acb"`` activa Actor-Critic-Boss (fase 5); ``"none"``, una sola pasada.

Añadir un tier exige plantilla, schemas y casos de evaluación (heurística 3 de la
fase 3); no basta con una entrada aquí.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas.tiers import (
    DeveloperEstimate,
    DeveloperTurn,
    ExecutiveEstimate,
    ExecutiveTurn,
    PmEstimate,
    PmTurn,
    Tier,
)


@dataclass(frozen=True)
class TierConfig:
    tier: Tier
    template: str  # Archivo de system dentro de la carpeta de versión.
    turn_schema: type[BaseModel]  # Salida del modelo: kind, message y borrador.
    view_schema: type[BaseModel]  # Lo que recibe el cliente.
    tools: tuple[str, ...] = ()
    research: bool = False
    review: str = "none"


TIER_CONFIG: dict[Tier, TierConfig] = {
    Tier.DEVELOPER: TierConfig(
        tier=Tier.DEVELOPER,
        template="system_developer.j2",
        turn_schema=DeveloperTurn,
        view_schema=DeveloperEstimate,
        tools=("web_search", "find_similar_projects"),
        research=True,
        review="none",
    ),
    Tier.PM: TierConfig(
        tier=Tier.PM,
        template="system_pm.j2",
        turn_schema=PmTurn,
        view_schema=PmEstimate,
        tools=("find_similar_projects",),
        research=True,
        review="acb",
    ),
    Tier.EXECUTIVE: TierConfig(
        tier=Tier.EXECUTIVE,
        template="system_executive.j2",
        turn_schema=ExecutiveTurn,
        view_schema=ExecutiveEstimate,
        tools=("find_similar_projects",),
        research=True,
        review="acb",
    ),
}


class UnknownTierError(ValueError):
    """El tier pedido no está en TIER_CONFIG."""


def resolve_tier_config(tier: Tier | str) -> TierConfig:
    try:
        return TIER_CONFIG[Tier(tier)]
    except (ValueError, KeyError) as exc:
        raise UnknownTierError(f"Tier desconocido: {tier}") from exc
