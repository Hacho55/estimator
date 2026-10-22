import json

import pytest

from app.services.errors import InvalidLLMOutputError
from app.services.postprocessor import (
    parse_turn,
    render_markdown,
    to_history_content,
    validate_and_fix,
)
from tests.llm_fakes import estimation_payload, estimation_turn


def test_repairs_impossible_frontend_cost_and_totals():
    payload = estimation_payload()
    payload["tasks"][0]["cost_eur"] = 50000
    payload["total_cost_eur"] = 50000 + 1800 + 1100 + 450
    payload["total_hours"] = 1
    estimation, issues = validate_and_fix(parse_turn(json.dumps({
        "kind": "estimation",
        "message": "ok",
        "estimation": payload,
    })).estimation)
    assert estimation.tasks[0].cost_eur == 2200
    assert estimation.total_hours == 100
    assert estimation.total_cost_eur == 5550
    codes = {issue.code for issue in issues}
    assert "cost_mismatch" in codes
    assert "total_hours_mismatch" in codes
    assert "total_cost_mismatch" in codes


def test_rounds_hours_to_multiples_of_five_and_flags_rate():
    payload = estimation_payload()
    payload["tasks"][0]["hours"] = 12
    payload["tasks"][0]["rate_eur"] = 200
    payload["tasks"][0]["cost_eur"] = 2400
    estimation, issues = validate_and_fix(parse_turn(json.dumps({
        "kind": "estimation",
        "message": "ok",
        "estimation": payload,
    })).estimation)
    assert estimation.tasks[0].hours == 10
    codes = {issue.code for issue in issues}
    assert "hours_not_multiple_of_5" in codes
    assert "rate_out_of_range" in codes
    assert estimation.tasks[0].cost_eur == 2000


def test_duration_warning_does_not_change_the_range():
    payload = estimation_payload(duration_weeks_min=20, duration_weeks_max=30)
    estimation, issues = validate_and_fix(parse_turn(json.dumps({
        "kind": "estimation",
        "message": "ok",
        "estimation": payload,
    })).estimation)
    assert estimation.duration_weeks_min == 20
    assert any(issue.code == "duration_incoherent" for issue in issues)


def test_invalid_json_and_fences():
    with pytest.raises(InvalidLLMOutputError):
        parse_turn("esto no es json")
    turn = parse_turn("```json\n" + estimation_turn() + "\n```")
    assert turn.kind == "estimation"


def test_not_estimable_is_a_warning_and_history_stores_the_corrected_version():
    payload = estimation_payload(is_estimable=False, not_estimable_reason="Es una campaña de prensa.", tasks=[], total_hours=0, total_cost_eur=0)
    turn = parse_turn(json.dumps({"kind": "estimation", "message": "no", "estimation": payload}))
    estimation, issues = validate_and_fix(turn.estimation)
    assert any(issue.code == "not_estimable" for issue in issues)
    stored = json.loads(to_history_content(turn, estimation))
    assert stored["estimation"]["is_estimable"] is False
    assert "No estimable" in render_markdown(estimation)
