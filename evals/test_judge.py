"""Familia 3 (juez): calidad subjetiva con GEval de DeepEval. Pocos casos, caro.

Dos criterios que no se pueden comprobar con código. El umbral sale de la calibración
(``scripts/calibrate_judge.py``, anotado en docs/evals.md); se empieza en 0,5.
"""

from __future__ import annotations

import os

import pytest

from evals.conftest import CASES
from evals.criteria import SCOPE_COHERENCE, TIER_FIT
from evals.dataset import case_tier_pairs

pytestmark = [pytest.mark.eval, pytest.mark.slow, pytest.mark.judge]
# 0,4: primera calibración (docs/evals.md), 8/8 de acuerdo con los veredictos humanos.
THRESHOLD = float(os.environ.get("EVAL_JUDGE_THRESHOLD", "0.4"))

JUDGE_PAIRS = [(case, tier) for case, tier in case_tier_pairs(CASES, single_turn_only=True) if case.expected.estimable][:6]


def _metrics(tier: str):
    from deepeval.metrics import GEval
    from deepeval.test_case import SingleTurnParams

    from evals.judge_model import EstimatorJudge

    judge = EstimatorJudge()
    params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
    return [
        GEval(name="ScopeCoherence", criteria=SCOPE_COHERENCE, evaluation_params=params, threshold=THRESHOLD, model=judge, async_mode=False),
        GEval(name="TierFit", criteria=TIER_FIT[tier], evaluation_params=params, threshold=THRESHOLD, model=judge, async_mode=False),
    ]


@pytest.mark.parametrize(("case", "tier"), JUDGE_PAIRS, ids=[f"{case.id}-{tier}" for case, tier in JUDGE_PAIRS])
def test_judge(run, case, tier) -> None:
    from deepeval import assert_test
    from deepeval.test_case import LLMTestCase

    result = run(case, tier)
    output = result.final.get("estimation") or ""
    assert_test(LLMTestCase(input=case.transcript, actual_output=output), _metrics(tier))
