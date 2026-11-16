"""Familia 2 (soft): consistencia entre ejecuciones del mismo caso.

Tres ejecuciones sobre los tres primeros casos estimables: todas deben estimar y el
coeficiente de variación del total de horas debe quedar por debajo de 0,25.
"""

from __future__ import annotations

import statistics

import pytest

from evals.conftest import CASES

pytestmark = [pytest.mark.eval, pytest.mark.slow]
RUNS = 3
MAX_CV = 0.25
SOFT_CASES = [case for case in CASES if case.expected.estimable and not case.turns][:3]


def _hours(view) -> float | None:
    if hasattr(view, "total_hours"):
        return view.total_hours
    if hasattr(view, "headline_cost_range"):  # executive: el punto medio del coste hace de proxy.
        return (view.headline_cost_range.min + view.headline_cost_range.max) / 2
    return None


@pytest.mark.parametrize("case", SOFT_CASES, ids=[case.id for case in SOFT_CASES])
@pytest.mark.parametrize("tier", ["developer", "pm"])
def test_estimates_are_consistent_across_runs(run, case, tier) -> None:
    views = [run(case, tier, fresh=True).view() for _ in range(RUNS)]
    assert all(view is not None for view in views), "alguna ejecución no devolvió estimación"
    assert all(view.is_estimable for view in views), "un caso estimable salió como no estimable"
    values = [_hours(view) for view in views]
    assert all(value is not None and value > 0 for value in values), f"medidas inválidas: {values}"
    mean = statistics.mean(values)
    cv = statistics.stdev(values) / mean if mean else 0
    assert cv < MAX_CV, f"CV={cv:.2f} con valores {values}"
