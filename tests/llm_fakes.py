"""Respuestas JSON que el cliente falso devuelve al servicio."""

import json


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
