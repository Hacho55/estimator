"""Carga y renderiza las plantillas Jinja2 versionadas del estimador.

La carta vive en ``app/prompts/estimation/<versión>/``. Cambiar la redacción es
editar o crear una carpeta; el resto del código solo elige la versión.
"""

from __future__ import annotations

import hashlib
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


def _context(request: PromptFields, *, rates: str, examples: str, version: str) -> dict:
    project_type = _value(request.project_type)
    return {
        "description": request.description,
        "project_type": project_type.replace("_", " ") if project_type else None,
        "detail_level": _value(request.detail_level),
        "output_format": _value(request.output_format),
        "rates": rates,
        "examples": examples,
        "version": version,
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
) -> tuple[str, str]:
    """Devuelve ``(system, user)`` listos para enviar al modelo."""
    _require_version(version)
    context = _context(request, rates=rates, examples=examples, version=version)
    try:
        system = _env.get_template(f"estimation/{version}/system.j2").render(**context)
        user = _env.get_template(f"estimation/{version}/user.j2").render(**context)
    except TemplateNotFound as exc:
        raise UnknownPromptVersionError(f"Falta una plantilla en la versión '{version}'.") from exc
    _log_render(version, system, user)
    return system, user


def render_reminder(version: str = "v1") -> str:
    _require_version(version)
    return _env.get_template(f"estimation/{version}/reminder.j2").render().strip()


def render_repair(version: str = "v1", *, error: str) -> str:
    _require_version(version)
    return _env.get_template(f"estimation/{version}/repair.j2").render(error=error).strip()
