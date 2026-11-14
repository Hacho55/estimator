"""Guardrails de entrada y salida. Cada control declara política y qué hace al disparar.

Modo ``log`` (defecto): se registra y no se bloquea en políticas ``exception``.
Los ``filter`` aplican siempre porque no rechazan la petición.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import structlog

from app.config import get_settings
from app.observability import log_event, record_scores
from app.schemas.estimation import Estimation, ValidationIssue
from app.services.errors import InputRejectedError

log = structlog.get_logger(__name__)

Policy = Literal["exception", "fix_retry", "filter", "log"]

INJECTION_PATTERNS = (
    re.compile(r"ignora(?:\s+las)?\s+instrucciones\s+anteriores", re.IGNORECASE),
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"</project_description>", re.IGNORECASE),
    re.compile(r"</user_message>", re.IGNORECASE),
    re.compile(r"<system>", re.IGNORECASE),
)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){8,}\d")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", re.IGNORECASE)
DNI_RE = re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE)
NIE_RE = re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE)

_PRESIDIO = None
_PRESIDIO_TRIED = False


@dataclass(frozen=True)
class Guardrail:
    name: str
    policy: Policy
    description: str


PROMPT_INJECTION = Guardrail(
    name="prompt_injection",
    policy="exception",
    description="Patrones de inyección o etiquetas del prompt en la entrada.",
)
DYNAMIC_INJECTION = Guardrail(
    name="dynamic_context_injection",
    policy="log",
    description=(
        "Patrones de inyección en adjuntos o resultados de herramientas. Solo se registra: "
        "ese texto va delimitado y declarado como dato, y rechazar un PDF del cliente por "
        "una frase no ayuda al usuario."
    ),
)
PII_INPUT = Guardrail(
    name="pii_input",
    policy="filter",
    description="Tacha datos personales en la entrada antes del modelo y de la caché.",
)
OUT_OF_SCOPE = Guardrail(
    name="out_of_scope",
    policy="filter",
    description="Si no es estimable, vacía tareas, fases y totales.",
)
PII_OUTPUT = Guardrail(
    name="pii_output",
    policy="filter",
    description="Tacha datos personales en los textos de la estimación.",
)


def _emit(guardrail: Guardrail, *, mode: str, fired: bool, **extra) -> None:
    log_event(
        "guardrail_triggered",
        name=guardrail.name,
        policy=guardrail.policy,
        mode=mode,
        fired=fired,
        **extra,
    )
    if fired:
        record_scores({f"guardrail.{guardrail.name}": 1.0})


def _issue(code: str, message: str, field: str | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, severity="info", message=message, field=field)


def detect_prompt_injection(text: str) -> str | None:
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def _regex_redact(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    cleaned = EMAIL_RE.sub("<EMAIL>", text)
    if cleaned != text:
        found.append("EMAIL")
    text = cleaned
    cleaned = PHONE_RE.sub("<TELEFONO>", text)
    if cleaned != text:
        found.append("PHONE")
    text = cleaned
    cleaned = IBAN_RE.sub("<IBAN>", text)
    if cleaned != text:
        found.append("IBAN")
    text = cleaned
    cleaned = DNI_RE.sub("<DNI>", text)
    if cleaned != text:
        found.append("DNI")
    text = cleaned
    cleaned = NIE_RE.sub("<DNI>", text)
    if cleaned != text:
        found.append("NIE")
    return cleaned, found


def _presidio_engine():
    global _PRESIDIO, _PRESIDIO_TRIED
    if _PRESIDIO_TRIED:
        return _PRESIDIO
    _PRESIDIO_TRIED = True
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig

        analyzer = AnalyzerEngine(supported_languages=["es"])
        dni = PatternRecognizer(
            supported_entity="DNI_NIE",
            patterns=[
                Pattern("dni", r"\b\d{8}[A-Za-z]\b", 0.6),
                Pattern("nie", r"\b[XYZxyz]\d{7}[A-Za-z]\b", 0.6),
            ],
            supported_language="es",
        )
        analyzer.registry.add_recognizer(dni)
        anonymizer = AnonymizerEngine()
        operators = {
            "PERSON": OperatorConfig("replace", {"new_value": "<PERSONA>"}),
            "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<EMAIL>"}),
            "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "<TELEFONO>"}),
            "IBAN_CODE": OperatorConfig("replace", {"new_value": "<IBAN>"}),
            "DNI_NIE": OperatorConfig("replace", {"new_value": "<DNI>"}),
        }
        _PRESIDIO = (analyzer, anonymizer, operators)
        log.info("presidio_ready")
    except Exception as exc:  # noqa: BLE001 — degradación documentada
        log.warning("presidio_unavailable", reason=str(exc))
        _PRESIDIO = None
    return _PRESIDIO


def redact_pii(text: str) -> tuple[str, list[str], str]:
    """Devuelve texto tachado, entidades vistas y motor usado (presidio|regex)."""
    settings = get_settings()
    engine = _presidio_engine()
    if engine is not None:
        analyzer, anonymizer, operators = engine
        try:
            results = analyzer.analyze(
                text=text,
                language="es",
                entities=list(operators),
                score_threshold=settings.PII_SCORE_THRESHOLD,
            )
            if results:
                redacted = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators).text
                entities = sorted({item.entity_type for item in results})
                return redacted, entities, "presidio"
            return text, [], "presidio"
        except Exception as exc:  # noqa: BLE001
            log.warning("presidio_analyze_failed", reason=str(exc))
    redacted, entities = _regex_redact(text)
    return redacted, entities, "regex"


def guard_input(text: str) -> str:
    """Aplica inyección (exception) y PII (filter). Devuelve el texto a usar aguas abajo."""
    settings = get_settings()
    mode = settings.GUARDRAILS_MODE
    matched = detect_prompt_injection(text)
    if matched:
        _emit(PROMPT_INJECTION, mode=mode, fired=True, pattern=matched)
        if mode == "enforce":
            raise InputRejectedError(
                "La descripción parece intentar cambiar las instrucciones del estimador. "
                "Quita esas frases y vuelve a enviar solo los datos del proyecto."
            )
    else:
        _emit(PROMPT_INJECTION, mode=mode, fired=False)

    redacted, entities, engine = redact_pii(text)
    fired = bool(entities) or redacted != text
    _emit(PII_INPUT, mode=mode, fired=fired, entities=entities, engine=engine)
    return redacted


def guard_dynamic_text(text: str, *, source: str) -> str:
    """Adjuntos y resultados de herramientas: se registra la inyección y se tacha la PII.

    No lanza: el contenido externo es dato (regla 1 de la fase 1), no programa.
    """
    settings = get_settings()
    mode = settings.GUARDRAILS_MODE
    matched = detect_prompt_injection(text)
    _emit(DYNAMIC_INJECTION, mode=mode, fired=bool(matched), pattern=matched, source=source)
    redacted, entities, engine = redact_pii(text)
    _emit(PII_INPUT, mode=mode, fired=bool(entities) or redacted != text, entities=entities, engine=engine, source=source)
    return redacted


def guard_output(estimation: Estimation) -> tuple[Estimation, list[ValidationIssue]]:
    """Filtros de salida: fuera de alcance y PII en textos."""
    settings = get_settings()
    mode = settings.GUARDRAILS_MODE
    warnings: list[ValidationIssue] = []
    data = estimation.model_dump()

    if not estimation.is_estimable:
        _emit(OUT_OF_SCOPE, mode=mode, fired=True)
        data["tasks"] = []
        data["phases"] = []
        data["total_hours"] = 0
        data["total_cost_eur"] = 0
        data["team"] = []
        warnings.append(
            _issue(
                "out_of_scope",
                estimation.not_estimable_reason or "La petición quedó fuera de alcance.",
            )
        )
    else:
        _emit(OUT_OF_SCOPE, mode=mode, fired=False)

    text_fields = [
        ("project_title", data.get("project_title") or ""),
        ("summary", data.get("summary") or ""),
        ("not_estimable_reason", data.get("not_estimable_reason") or ""),
    ]
    changed = False
    entities_seen: list[str] = []
    for field, value in text_fields:
        if not value:
            continue
        redacted, entities, _engine = redact_pii(value)
        if redacted != value:
            data[field] = redacted
            changed = True
            entities_seen.extend(entities)
    for index, task in enumerate(data.get("tasks") or []):
        for key in ("name", "phase", "role"):
            value = str(task.get(key) or "")
            if not value:
                continue
            redacted, entities, _engine = redact_pii(value)
            if redacted != value:
                task[key] = redacted
                changed = True
                entities_seen.extend(entities)
    for member in data.get("team") or []:
        value = str(member.get("role") or "")
        if not value:
            continue
        redacted, entities, _engine = redact_pii(value)
        if redacted != value:
            member["role"] = redacted
            changed = True
            entities_seen.extend(entities)
    for phase in data.get("phases") or []:
        value = str(phase.get("phase") or "")
        if not value:
            continue
        redacted, entities, _engine = redact_pii(value)
        if redacted != value:
            phase["phase"] = redacted
            changed = True
            entities_seen.extend(entities)
    for key in ("assumptions", "risks"):
        items = data.get(key) or []
        new_items = []
        for item in items:
            redacted, entities, _engine = redact_pii(str(item))
            new_items.append(redacted)
            if redacted != item:
                changed = True
                entities_seen.extend(entities)
        data[key] = new_items
    for entry in data.get("phase_assumptions") or []:
        phase_name = str(entry.get("phase") or "")
        if phase_name:
            redacted_phase, entities, _engine = redact_pii(phase_name)
            if redacted_phase != phase_name:
                entry["phase"] = redacted_phase
                changed = True
                entities_seen.extend(entities)
        notes = []
        for item in entry.get("assumptions") or []:
            redacted, entities, _engine = redact_pii(str(item))
            notes.append(redacted)
            if redacted != item:
                changed = True
                entities_seen.extend(entities)
        entry["assumptions"] = notes

    _emit(PII_OUTPUT, mode=mode, fired=changed, entities=sorted(set(entities_seen)))
    if changed:
        warnings.append(_issue("pii_output", "Se tacharon datos personales en la estimación."))

    return Estimation.model_validate(data), warnings
