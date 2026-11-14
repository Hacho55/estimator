"""Memoria de la sesión: valida y fusiona el delta del extractor. Sin LLM.

El extractor propone cambios (``MetadataUpdate``); aquí se decide qué entra.
Así un descuido del modelo no borra hechos, y cada cambio queda registrado.

Reglas de fusión:
- Escalares: el valor nuevo sustituye al anterior si pasa la validación.
- Listas: unión sin distinguir mayúsculas; se conserva la primera grafía.
- Retirar una tecnología la pasa a ``rejected_options`` (política 1 de olvido:
  revisión explícita por el usuario).
- Una opción rechazada no puede seguir entre las tecnologías.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.session import (
    MAX_ITEM_CHARS,
    MAX_LIST_ITEMS,
    MAX_NAME_CHARS,
    MAX_SCOPE_CHARS,
    MAX_TEAM_SIZE,
    MemoryChange,
    MetadataUpdate,
    ProjectMetadata,
)


@dataclass
class MergeResult:
    metadata: ProjectMetadata
    changes: list[MemoryChange] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)  # Motivos, para la traza.


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _key(text: str) -> str:
    return _clean(text).casefold()


def _clean_items(items: list[str], discarded: list[str], label: str) -> list[str]:
    kept: list[str] = []
    for item in items:
        text = _clean(item)
        if not text:
            continue
        if len(text) > MAX_ITEM_CHARS:
            discarded.append(f"{label}: elemento demasiado largo")
            continue
        kept.append(text)
    return kept


def validate_update(update: MetadataUpdate) -> tuple[MetadataUpdate, list[str]]:
    """Validación heurística posterior al extractor. Devuelve el delta saneado y los motivos de descarte."""
    discarded: list[str] = []
    name = _clean(update.set_project_name) or None
    if name and len(name) > MAX_NAME_CHARS:
        discarded.append("project_name: demasiado largo")
        name = None
    scope = _clean(update.set_agreed_scope) or None
    if scope and len(scope) > MAX_SCOPE_CHARS:
        discarded.append("agreed_scope: demasiado largo")
        scope = None
    team = update.set_team_size
    if team is not None and not 1 <= team <= MAX_TEAM_SIZE:
        discarded.append(f"assumed_team_size: fuera de rango ({team})")
        team = None

    add_tech = _clean_items(update.add_technologies, discarded, "add_technologies")
    remove_tech = _clean_items(update.remove_technologies, discarded, "remove_technologies")
    contradictory = {_key(item) for item in add_tech} & {_key(item) for item in remove_tech}
    if contradictory:
        # Añadir y retirar lo mismo en un turno: no se sabe qué quiso decir.
        discarded.append("technologies: se añade y se retira lo mismo")
        add_tech = [item for item in add_tech if _key(item) not in contradictory]
        remove_tech = [item for item in remove_tech if _key(item) not in contradictory]

    cleaned = MetadataUpdate(
        set_project_name=name,
        set_team_size=team,
        set_agreed_scope=scope,
        add_technologies=add_tech,
        remove_technologies=remove_tech,
        add_constraints=_clean_items(update.add_constraints, discarded, "add_constraints"),
        remove_constraints=_clean_items(update.remove_constraints, discarded, "remove_constraints"),
        add_rejected_options=_clean_items(update.add_rejected_options, discarded, "add_rejected_options"),
        evidence=_clean(update.evidence)[:MAX_ITEM_CHARS] or None,
    )
    return cleaned, discarded


def _union(
    current: list[str],
    extra: list[str],
    discarded: list[str],
    label: str,
) -> tuple[list[str], list[str]]:
    """Unión sin duplicados. Con la lista llena, lo nuevo se descarta: lo establecido no se olvida.

    Devuelve la lista y los elementos que sí entraron.
    """
    seen = {_key(item) for item in current}
    merged = list(current)
    accepted: list[str] = []
    for item in extra:
        if _key(item) in seen:
            continue
        if len(merged) >= MAX_LIST_ITEMS:
            discarded.append(f"{label}: lista llena, se descarta «{item}»")
            continue
        merged.append(item)
        accepted.append(item)
        seen.add(_key(item))
    return merged, accepted


def _without(current: list[str], removed: list[str]) -> list[str]:
    gone = {_key(item) for item in removed}
    return [item for item in current if _key(item) not in gone]


def apply_update(metadata: ProjectMetadata, update: MetadataUpdate, *, turn: int) -> MergeResult:
    """Fusiona el delta ya validado. Devuelve la memoria nueva y su registro de cambios."""
    cleaned, discarded = validate_update(update)
    data = metadata.model_dump()
    evidence = cleaned.evidence

    data["project_name"] = cleaned.set_project_name or data["project_name"]
    if cleaned.set_team_size is not None:
        data["assumed_team_size"] = cleaned.set_team_size
    data["agreed_scope"] = cleaned.set_agreed_scope or data["agreed_scope"]

    # Retirar o rechazar una tecnología la quita siempre de las activas: el usuario lo dijo.
    # Que además quede en rejected_options depende de que quepa (si no, se informa).
    leaving = cleaned.remove_technologies + cleaned.add_rejected_options
    technologies = _without(data["mentioned_technologies"], leaving)
    technologies, added = _union(technologies, cleaned.add_technologies, discarded, "mentioned_technologies")
    # Volver a añadir algo rechazado es una revisión: deja de estar rechazado, solo si entró.
    revived = {_key(item) for item in added}
    rejected = [item for item in data["rejected_options"] if _key(item) not in revived]
    rejected, _ = _union(rejected, leaving, discarded, "rejected_options")
    technologies = _without(technologies, rejected)

    constraints = _without(data["explicit_constraints"], cleaned.remove_constraints)
    constraints, _ = _union(constraints, cleaned.add_constraints, discarded, "explicit_constraints")

    data["mentioned_technologies"] = technologies
    data["rejected_options"] = rejected
    data["explicit_constraints"] = constraints

    merged = ProjectMetadata(**data)
    changes = [
        MemoryChange(turn=turn, field=name, before=before, after=after, evidence=evidence)
        for name, before, after in _diff(metadata, merged)
    ]
    return MergeResult(metadata=merged, changes=changes, discarded=discarded)


def _diff(before: ProjectMetadata, after: ProjectMetadata):
    old, new = before.model_dump(), after.model_dump()
    for name in old:
        if old[name] != new[name]:
            yield name, old[name], new[name]
