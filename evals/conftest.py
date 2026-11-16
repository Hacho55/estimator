"""Suite de evals contra el modelo real. Se salta sin credenciales.

    uv run pytest evals -m eval                  # hard: una llamada por caso y tier
    uv run pytest evals -m "eval and slow"       # soft y juez: varias llamadas
    uv run pytest evals -m eval -k landing       # un caso

Las cachés se apagan: una eval que sirve una respuesta guardada no mide nada.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

# Antes de importar la app: perfil por JWT (el canal real), sin cachés ni telemetría de DeepEval.
EVAL_SECRET = secrets.token_hex(32)
os.environ.update(
    {
        "AUTH_MODE": "jwt",
        "AI_SERVICE_SECRET": EVAL_SECRET,
        "SEMANTIC_CACHE_MODE": "off",
        "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
        "DEEPEVAL_RESULTS_FOLDER": "",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.state.response_cache import clear_response_cache  # noqa: E402
from evals.dataset import load_dataset  # noqa: E402
from evals.harness import SPEND, CaseRun, run_case  # noqa: E402

CASES = load_dataset()


def _has_credentials() -> bool:
    """Solo la falta de clave salta la suite; otra configuración rota debe fallar, no saltarse."""
    get_settings.cache_clear()
    try:
        return bool(get_settings().LITELLM_API_KEY.get_secret_value())
    except ValidationError as exc:
        missing_key = [error for error in exc.errors() if error.get("loc") == ("LITELLM_API_KEY",) and error.get("type") == "missing"]
        if missing_key and len(missing_key) == len(exc.errors()):
            return False
        raise


def pytest_collection_modifyitems(config, items):
    if _has_credentials():
        return
    skip = pytest.mark.skip(reason="Sin LITELLM_API_KEY: las evals necesitan el modelo real.")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def api():
    from app.main import app
    from evals.judge_model import EstimatorJudge

    with TestClient(app) as client:
        EstimatorJudge.portal = client.portal  # El juez comparte bucle y cliente con la app.
        try:
            yield client
        finally:
            EstimatorJudge.portal = None


_RUNS: dict[tuple[str, str], CaseRun] = {}  # Reutilización dentro de la sesión de pytest.
_HISTORY: list[CaseRun] = []  # Todas las ejecuciones, también las repetidas (informe).


@pytest.fixture
def run(api):
    """Ejecuta (o reutiliza en la sesión de pytest) un caso para un tier."""

    def _run(case, tier: str, *, fresh: bool = False) -> CaseRun:
        key = (case.id, tier)
        if fresh or key not in _RUNS:
            clear_response_cache()
            _RUNS[key] = run_case(api, case, tier, EVAL_SECRET)
            _HISTORY.append(_RUNS[key])
        return _RUNS[key]

    return _run


def _write_report() -> Path | None:
    """Una fila por ejecución (caso, tier): decisión de la revisión, herramientas, coste y latencia.

    Es la base para decidir por datos si la revisión compensa en cada tier (fase 5, 5F).
    """
    if not _HISTORY:
        return None
    rows = []
    for index, result in enumerate(_HISTORY):
        case_id, tier = result.case.id, result.tier
        final = result.final
        review = final.get("review") or {}
        rows.append(
            {
                "run": index,
                "case": case_id,
                "tier": tier,
                "turns": len(result.bodies),
                "seconds": round(result.seconds, 1),
                "cost_usd": sum(body.get("cost_usd") or 0 for body in result.bodies),
                "review_decision": review.get("decision"),
                "actor_calls": review.get("actor_calls"),
                "open_issues": len(review.get("open_issues") or []),
                "tools": [item["tool"] for item in final.get("tools") or []],
                "warnings": [item["code"] for item in final.get("warnings") or []],
            }
        )
    folder = Path(__file__).resolve().parents[1] / "experiments" / "results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"evals-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pytest_terminal_summary(terminalreporter):
    report = _write_report()
    if report:
        terminalreporter.write_line(f"informe por ejecución: {report}")
    if SPEND.calls:
        terminalreporter.write_sep("=", "coste de las evals")
        terminalreporter.write_line(
            f"turnos: {SPEND.calls} · tokens: {SPEND.input_tokens} entrada / {SPEND.output_tokens} salida "
            f"· coste: {SPEND.cost_usd:.4f} USD (sin el juez)"
        )
