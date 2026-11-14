"""Respuestas JSON que el cliente falso devuelve al servicio."""

import json
from types import SimpleNamespace


class FakeResult:
    def __init__(self, *, cache_hit: bool, markdown: str | None = None, message: str | None = None) -> None:
        self.cache_hit = cache_hit
        self.estimation_markdown = markdown
        self.message = message
        self.model = "openrouter/openai/gpt-4o-mini"
        self.latency_ms = 0 if cache_hit else 12
        self.usage = SimpleNamespace(input_tokens=0 if cache_hit else 8, output_tokens=0 if cache_hit else 3)


class FakeStream:
    def __init__(self, pieces: list[str], result: FakeResult, from_cache: bool) -> None:
        self._pieces = pieces
        self.result = None
        self._result = result
        self.from_cache = from_cache

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for piece in self._pieces:
            yield piece
        self.result = self._result


def estimation_payload(**overrides) -> dict:
    data = {
        "project_title": "Landing con HubSpot",
        "summary": "Landing con formulario, integración con HubSpot y un blog. El diseño ya está en Figma.",
        "is_estimable": True,
        "not_estimable_reason": None,
        "tasks": [
            {"name": "Maquetación", "phase": "Frontend", "role": "frontend", "hours": 40, "rate_eur": 55, "cost_eur": 2200},
            {"name": "Integración HubSpot", "phase": "Backend", "role": "backend", "hours": 30, "rate_eur": 60, "cost_eur": 1800},
            {"name": "Blog", "phase": "Frontend", "role": "frontend", "hours": 20, "rate_eur": 55, "cost_eur": 1100},
            {"name": "QA", "phase": "Calidad", "role": "qa", "hours": 10, "rate_eur": 45, "cost_eur": 450},
        ],
        "total_hours": 100,
        "total_cost_eur": 5550,
        "team": [
            {"role": "frontend", "count": 1, "dedication": 1},
            {"role": "backend", "count": 1, "dedication": 0.5},
        ],
        "duration_weeks_min": 3,
        "duration_weeks_max": 5,
        "assumptions": ["El diseño de Figma está cerrado."],
        "risks": ["La API de HubSpot puede cambiar."],
    }
    data.update(overrides)
    return data


def estimation_turn(kind: str = "estimation", message: str | None = None, **overrides) -> str:
    if kind == "answer":
        payload = {"kind": "answer", "message": message or "El equipo ideal es una persona de frontend y otra de backend.", "estimation": None}
    else:
        payload = {
            "kind": kind,
            "message": message or "Estimación preparada a partir de la reunión.",
            "estimation": estimation_payload(**overrides),
        }
    return json.dumps(payload, ensure_ascii=False)


# --- Tiers (sesión 5): borradores con desglose común y campos propios ---------

_WORK = [
    {"name": "Maquetación", "group": "Frontend", "role": "frontend", "hours": 42},
    {"name": "Integración HubSpot", "group": "Backend", "role": "backend", "hours": 30},
    {"name": "Blog", "group": "Frontend", "role": "frontend", "hours": 20},
    {"name": "Pruebas", "group": "QA", "role": "qa", "hours": 10},
]


def tier_draft(tier: str, **overrides) -> dict:
    data = {
        "project_title": "Landing con HubSpot",
        "summary": "Landing con formulario, integración con HubSpot y un blog.",
        "is_estimable": True,
        "not_estimable_reason": None,
        "work": [dict(item) for item in _WORK],
        "team": [{"role": "frontend", "count": 1, "dedication": 1}, {"role": "backend", "count": 1, "dedication": 0.5}],
        "duration_weeks_min": 3,
        "duration_weeks_max": 5,
        "assumptions": ["El diseño de Figma está cerrado."],
        "risks": ["La API de HubSpot puede cambiar."],
    }
    if tier == "developer":
        data["components"] = [
            {"name": "Frontend", "technical_risks": ["SSR del blog"], "stack_assumptions": ["Next.js"]},
            {"name": "Backend", "technical_risks": ["Límites de la API"], "stack_assumptions": []},
        ]
        data["uncertainty_drivers"] = ["Volumen del blog", "Plan de HubSpot"]
    elif tier == "pm":
        for item in data["work"]:
            item["group"] = {"Frontend": "Desarrollo", "Backend": "Desarrollo", "QA": "QA"}[item["group"]]
        data["milestones"] = [
            {"name": "Landing publicada", "phase": "Desarrollo", "deliverable": "Landing y blog en producción"},
            {"name": "Aceptación", "phase": "QA", "deliverable": "Informe de pruebas"},
        ]
        data["blockers"] = ["Acceso a la cuenta de HubSpot"]
    elif tier == "executive":
        data["confidence_level"] = "medium"
        data["confidence_reason"] = "El alcance está claro y el diseño cerrado."
        data["top_three_risks"] = ["Cambios en HubSpot", "Contenido del blog tarde", "Aprobaciones del cliente"]
        data["go_no_go_recommendation"] = "Seguir: riesgo bajo y retorno rápido."
    data.update(overrides)
    return data


def tier_turn(tier: str, kind: str = "estimation", message: str | None = None, **overrides) -> str:
    if kind == "answer":
        return json.dumps({"kind": "answer", "message": message or "Respuesta.", "estimation": None}, ensure_ascii=False)
    payload = {"kind": kind, "message": message or "Estimación preparada.", "estimation": tier_draft(tier, **overrides)}
    return json.dumps(payload, ensure_ascii=False)
