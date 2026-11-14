"""Reglas de negocio: qué cuenta como «proyecto similar» y qué métricas se devuelven.

Viven aquí, no en el estimador (fase 1, §4: «lógica duplicada» es un error). El
servicio IA solo sabe que existe la herramienta y qué devuelve.
"""

from __future__ import annotations

import re

MAX_RESULTS = 5
TECH_WEIGHT = 0.6
SCOPE_WEIGHT = 0.4
_WORD = re.compile(r"[a-záéíóúñü0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "de", "la", "el", "los", "las", "con", "y", "por", "para", "en", "un", "una", "del", "al",
    "a", "o", "que", "se", "su", "sus", "sin", "the", "and", "with", "for", "of",
}


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text) if word.casefold() not in _STOPWORDS and len(word) > 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def similarity(project: dict, *, technologies: list[str], scope_summary: str) -> float:
    tech = _jaccard({item.casefold() for item in technologies}, {item.casefold() for item in project["technologies"]})
    scope = _jaccard(_words(scope_summary), _words(project["scope_summary"]))
    return round(TECH_WEIGHT * tech + SCOPE_WEIGHT * scope, 3)


def public_metrics(project: dict, score: float) -> dict:
    """Lo que sale de la BBDD: métricas agregadas. Ni cliente ni margen."""
    deviation = (project["actual_hours"] - project["estimated_hours"]) / project["estimated_hours"]
    return {
        "project_id": project["id"],
        "title": project["title"],
        "similarity": score,
        "technologies": project["technologies"],
        "team_size": project["team_size"],
        "estimated_hours": project["estimated_hours"],
        "actual_hours": project["actual_hours"],
        "deviation_pct": round(deviation * 100, 1),
        "duration_weeks": project["duration_weeks"],
        "materialized_risks": project["materialized_risks"],
    }


def find_similar(projects: list[dict], *, technologies: list[str], scope_summary: str, team_size: int | None) -> list[dict]:
    scored = []
    for project in projects:
        score = similarity(project, technologies=technologies, scope_summary=scope_summary)
        if team_size and abs(project["team_size"] - team_size) <= 1:
            score = round(score + 0.05, 3)  # Equipo parecido: ligera preferencia.
        if score > 0:
            scored.append((score, project))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [public_metrics(project, score) for score, project in scored[:MAX_RESULTS]]
