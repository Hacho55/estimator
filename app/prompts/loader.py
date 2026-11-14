"""Carga y renderiza las plantillas Jinja2 versionadas del estimador.

La carta vive en ``app/prompts/estimation/<versión>/``. Cambiar la redacción es
editar o crear una carpeta; el resto del código solo elige la versión.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

log = structlog.get_logger(__name__)

_BASE_DIR = Path(__file__).resolve().parent

_env = Environment(
    loader=FileSystemLoader(_BASE_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
    keep_trailing_newline=True,
)


class PromptFields(Protocol):
    description: str
    project_type: object | None
    detail_level: object
    output_format: object
    project_metadata: str


class UnknownPromptVersionError(ValueError):
    """La carpeta de versión pedida no existe."""


_REQUIRED_TEMPLATES = ("system.j2", "user.j2", "reminder.j2", "repair.j2", "examples.j2")


def available_versions() -> list[str]:
    """Solo carpetas con las cuatro plantillas del estimador."""
    root = _BASE_DIR / "estimation"
    if not root.is_dir():
        return []
    versions: list[str] = []
    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        if all((path / name).is_file() for name in _REQUIRED_TEMPLATES):
            versions.append(path.name)
    return sorted(versions)


def _value(field: object) -> str | None:
    if field is None:
        return None
    return field.value if hasattr(field, "value") else str(field)


def _require_version(version: str) -> None:
    if version not in available_versions():
        raise UnknownPromptVersionError(f"No existe la versión de prompt '{version}'.")


# Etiquetas que delimitan datos en el prompt. Dentro de un dato se neutralizan para
# que un texto del cliente no pueda cerrar su bloque y abrir otro (regla 1: dato, no programa).
_BLOCK_TAG = re.compile(
    r"<(/?)(project_description|user_message|attachments|attachment|tool_results|result|"
    r"project_metadata|critic_feedback|constraints|scope|reminder|"
    r"current_memory|user_turn|assistant_turn)\b",
    re.IGNORECASE,
)


def as_data(value):
    """Neutraliza las etiquetas de bloque en textos, listas y diccionarios."""
    if isinstance(value, str):
        return _BLOCK_TAG.sub(lambda match: f"‹{match.group(1)}{match.group(2)}", value)
    if isinstance(value, list):
        return [as_data(item) for item in value]
    if isinstance(value, dict):
        return {key: as_data(item) for key, item in value.items()}
    return value


def _context(request: PromptFields, *, rates: str, examples: str, version: str) -> dict:
    project_type = _value(request.project_type)
    return {
        "description": as_data(request.description),
        "project_type": project_type.replace("_", " ") if project_type else None,
        "detail_level": _value(request.detail_level),
        "output_format": _value(request.output_format),
        "rates": rates,
        "examples": examples,
        "version": version,
        "project_metadata": getattr(request, "project_metadata", "") or "",
        "memory": as_data(getattr(request, "memory", None) or None),
        "attachments": as_data(getattr(request, "attachments", None) or []),
        "tool_results": as_data(getattr(request, "tool_results", None) or []),
        "critic_feedback": as_data(getattr(request, "critic_feedback", None) or []),
    }


def _log_render(version: str, system: str, user: str) -> None:
    digest = hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest()[:12]
    log.info("prompt_rendered", prompt_version=version, content_hash=digest, system_chars=len(system), user_chars=len(user))


def render_estimation_prompt(
    request: PromptFields,
    version: str = "v1",
    *,
    rates: str,
    examples: str,
    system_template: str = "system.j2",
) -> tuple[str, str]:
    """Devuelve ``(system, user)`` listos para enviar al modelo.

    ``system_template`` elige el system de un tier (``system_pm.j2``) dentro de la versión.
    """
    _require_version(version)
    context = _context(request, rates=rates, examples=examples, version=version)
    try:
        system = _env.get_template(f"estimation/{version}/{system_template}").render(**context)
        user = _env.get_template(f"estimation/{version}/user.j2").render(**context)
    except TemplateNotFound as exc:
        raise UnknownPromptVersionError(f"Falta una plantilla en la versión '{version}'.") from exc
    _log_render(version, system, user)
    return system, user


def has_template(version: str, name: str) -> bool:
    return (_BASE_DIR / "estimation" / version / name).is_file()


def has_turn_template(version: str) -> bool:
    """Desde v4, el turno de seguimiento tiene plantilla propia (con adjuntos y herramientas)."""
    return (_BASE_DIR / "estimation" / version / "turn.j2").is_file()


def render_follow_up(request: PromptFields, version: str, *, rates: str = "", examples: str = "") -> str:
    """Mensaje de usuario de un turno con historial. Solo para versiones con ``turn.j2``."""
    _require_version(version)
    context = _context(request, rates=rates, examples=examples, version=version)
    context["reminder"] = render_reminder(version)
    return _env.get_template(f"estimation/{version}/turn.j2").render(**context).strip()


def render_reminder(version: str = "v1") -> str:
    _require_version(version)
    return _env.get_template(f"estimation/{version}/reminder.j2").render().strip()


METADATA_PROMPT_VERSION = "v2"


def render_metadata_prompt(*, previous: str, user_turn: str, assistant_turn: str) -> tuple[str, str]:
    """Prompt del extractor de memoria (delta). No es una versión de estimación."""
    folder = f"metadata/{METADATA_PROMPT_VERSION}"
    system = _env.get_template(f"{folder}/system.j2").render().strip()
    user = (
        _env.get_template(f"{folder}/user.j2")
        .render(previous=as_data(previous), user_turn=as_data(user_turn), assistant_turn=as_data(assistant_turn))
        .strip()
    )
    return system, user


def render_repair(version: str = "v1", *, error: str) -> str:
    _require_version(version)
    return _env.get_template(f"estimation/{version}/repair.j2").render(error=error).strip()
