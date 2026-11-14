"""Boss (fase 5): decide qué hacer con el feedback. Código determinista, sin LLM (D13).

Evaluar (Critic) y decidir (Boss) son roles distintos: si el crítico decidiera,
aparecerían los dos modos de fallo de Self-Refine (bucle por insatisfacción
crónica o aceptación temprana). Aquí la gobernanza son reglas explícitas,
demostrables con tests y sin sesgo de autoconfirmación:

- accept: ningún issue bloqueante (critical o major).
- iterate: hay bloqueantes, el crítico cree que se corrigen (o los señaló el
  código) y queda presupuesto de llamadas al actor.
- synthesize: el crítico rechaza, o se agotó el presupuesto. Se entrega el mejor
  candidato con sus issues abiertos a la vista; producción antes que perfección.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.review import BossDecision, ReviewIssue


def blocking(issues: list[ReviewIssue]) -> list[ReviewIssue]:
    return [issue for issue in issues if issue.blocking]


def decide(*, verdict: str, issues: list[ReviewIssue], iterations_left: int) -> BossDecision:
    """Una decisión por vuelta. ``iterations_left``: llamadas al actor que quedan."""
    if verdict == "reject":
        return "synthesize"
    if not blocking(issues):
        return "accept"
    return "iterate" if iterations_left > 0 else "synthesize"


def severity_score(issues: list[ReviewIssue]) -> int:
    """Peso para comparar candidatos: un critical vale por diez major; los minor no cuentan."""
    return sum(10 if issue.severity == "critical" else 1 for issue in blocking(issues))


@dataclass
class Candidate:
    result: object  # TurnResult del actor.
    issues: list[ReviewIssue]


def best_candidate(candidates: list[Candidate]) -> Candidate:
    """El de menos peso bloqueante; a igualdad, el más reciente (incorpora más feedback)."""
    ranked = sorted(enumerate(candidates), key=lambda pair: (severity_score(pair[1].issues), -pair[0]))
    return ranked[0][1]


def feedback_for_actor(issues: list[ReviewIssue]) -> list[dict]:
    """Lo que vuelve al actor: solo los bloqueantes, con campo y pista de arreglo."""
    return [
        {
            "severity": issue.severity,
            "category": issue.category,
            "field_path": issue.field_path,
            "description": issue.description,
            "suggested_fix": issue.suggested_fix,
        }
        for issue in blocking(issues)
    ]
