from app.evaluation.metrics import collect_metrics
from app.schemas.estimation import UsageInfo
from app.services.postprocessor import parse_turn, validate_and_fix
from tests.llm_fakes import estimation_turn


def test_metrics_for_a_clean_estimation():
    turn = parse_turn(estimation_turn())
    estimation, warnings = validate_and_fix(turn.estimation)
    scores = collect_metrics(
        first_try=True,
        warnings=warnings,
        estimation=estimation,
        usage=UsageInfo(input_tokens=100, output_tokens=50, total_tokens=150, cached_tokens=80),
        cost_usd=0.01,
        latency_ms=10,
        expected={"hours_min": 50, "hours_max": 150, "estimable": True},
    )
    assert scores["json_valid_first_try"] == 1
    assert scores["hours_multiple_of_5"] == 1
    assert scores["hours_in_expected_range"] == 1
    assert scores["is_estimable_matches_expected"] == 1
    assert scores["cached_ratio"] == 0.8
    assert scores["sections_complete"] == 1
