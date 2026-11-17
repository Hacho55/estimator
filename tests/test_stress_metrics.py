"""Stress test: métricas, escenarios y adjuntos (sin modelo)."""

from __future__ import annotations

import pytest

from app.attachments.extractor import extract_text
from app.config import get_settings
from evals.pdf import text_pdf
from evals.stress.fixtures.build_pdfs import MARKERS, SIZES_KB, build_text
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric, MemoryDriftMetric, drift_for_turn
from evals.stress.scenarios import MULTI_TURN, SCENARIOS, Fact, get_scenario

SNAPSHOT = {
    "project_metadata": {
        "project_name": "Nimbus",
        "assumed_team_size": 4,
        "mentioned_technologies": ["Flutter", "Node.js"],
        "agreed_scope": "Inventario multi-tenant con SSO",
        "explicit_constraints": ["Presupuesto máximo 80.000 EUR"],
        "rejected_options": ["React"],
    },
    "memory_log": [{"field": "explicit_constraints", "before": ["Presupuesto 30.000 EUR"]}],
}


# --- Presupuestos -------------------------------------------------------------


def test_latency_budget_passes_fails_and_accepts_the_exact_limit() -> None:
    metric = LatencyBudgetMetric(budget_ms=25_000)
    assert metric.evaluate({"latency_ms": 12_000}).passed
    assert not metric.evaluate({"latency_ms": 66_000}).passed
    assert metric.evaluate({"latency_ms": 25_000}).score == 1.0


def test_cost_budget_passes_fails_and_unknown_cost_does_not_pass() -> None:
    metric = CostBudgetMetric(budget_usd=0.05)
    assert metric.evaluate({"cost_usd": 0.01}).passed
    assert not metric.evaluate({"cost_usd": 0.07}).passed
    assert metric.evaluate({"cost_usd": 0.05}).passed
    unknown = metric.evaluate({"cost_usd": None})
    assert not unknown.passed and "desconocido" in unknown.details


def test_budgets_reject_non_positive_values() -> None:
    with pytest.raises(ValueError):
        LatencyBudgetMetric(0)
    with pytest.raises(ValueError):
        CostBudgetMetric(-1)


# --- Deriva de memoria --------------------------------------------------------


def test_memory_drift_finds_a_fact_case_insensitively_in_its_field() -> None:
    assert MemoryDriftMetric("nimbus", where="project_name").evaluate(SNAPSHOT).passed
    assert MemoryDriftMetric("multi-TENANT", where="scope").evaluate(SNAPSHOT).passed


def test_memory_drift_fails_when_the_fact_is_gone() -> None:
    result = MemoryDriftMetric("Orion", where="project_name").evaluate(SNAPSHOT)
    assert not result.passed and result.score == 0.0


def test_memory_drift_absent_fact_ignores_the_change_log() -> None:
    """El 30.000 retirado sigue en memory_log, pero ya no está en las restricciones vigentes."""
    old = Fact("budget.old", ("30.000",), where="constraints", expect="absent")
    assert MemoryDriftMetric(old).evaluate(SNAPSHOT).passed
    react = Fact("stack.react", ("React",), where="technologies", expect="absent")
    assert MemoryDriftMetric(react).evaluate(SNAPSHOT).passed


def test_memory_drift_on_empty_snapshot_and_exact_team_size() -> None:
    assert not MemoryDriftMetric("Nimbus", where="project_name").evaluate({}).passed
    assert MemoryDriftMetric(Fact("team", ("4",), where="team")).evaluate(SNAPSHOT).passed
    assert not MemoryDriftMetric(Fact("team", ("40",), where="team")).evaluate(SNAPSHOT).passed


def test_memory_drift_can_check_the_answer_text() -> None:
    assert MemoryDriftMetric("Kronos", where="answer").evaluate({}, answer="Integración con kronos").passed


# --- Escenarios -----------------------------------------------------------------


def test_multi_turn_scenarios_have_20_turns_and_a_first_turn_fact() -> None:
    for name in MULTI_TURN:
        scenario = get_scenario(name)
        assert len(scenario.turns) == 20
        assert len(scenario.turns[0].transcript) >= 50  # Mínimo del primer turno.
        assert scenario.turns[0].facts[0].where == "project_name"
    assert len(SCENARIOS["burst"].turns) == 1
    with pytest.raises(ValueError):
        get_scenario("nope")


def test_a_later_fact_with_the_same_key_supersedes_the_earlier_one() -> None:
    pivot = get_scenario("pivot")
    before = {fact.key: fact for fact in pivot.active_facts(4)}
    after = {fact.key: fact for fact in pivot.active_facts(5)}
    assert before["stack.react"].expect == "present"
    assert after["stack.react"].expect == "absent"
    assert "stack.flutter" in after and "stack.flutter" not in before


def test_drift_for_turn_reports_recall_and_the_first_fact() -> None:
    contradiction = get_scenario("contradiction")
    summary = drift_for_turn(contradiction, 8, {"project_metadata": {"project_name": "Vega"}})
    assert summary.first_fact_kept is True
    assert summary.checked == len(contradiction.active_facts(8))
    assert 0 < summary.recall < 1
    assert any("budget" in failure for failure in summary.failures)


# --- Adjuntos -------------------------------------------------------------------


def test_attachment_sizes_are_calibrated_and_markers_follow_the_truncation() -> None:
    limit = get_settings().ATTACHMENT_MAX_CHARS
    for size in SIZES_KB:
        text = build_text(size)
        assert size * 1000 <= len(text) <= size * 1000 * 1.1
        assert all(marker.token in text for marker in MARKERS)
    item = extract_text("attach_100kb.pdf", text_pdf(build_text(100)))
    assert item.truncated and item.chars <= limit
    kept = [marker.token for marker in MARKERS if marker.token.lower() in item.text.lower()]
    assert kept == ["Kronos"]  # El del 50 % y el del 90 % quedan fuera del límite.
    small = extract_text("attach_5kb.pdf", text_pdf(build_text(5)))
    assert small.pages >= 2 and not small.truncated
    assert all(marker.token.lower() in small.text.lower() for marker in MARKERS)


def test_unknown_latency_does_not_pass_the_budget() -> None:
    metric = LatencyBudgetMetric(budget_ms=25_000)
    assert not metric.evaluate({"latency_ms": None}).passed
    assert not metric.evaluate({}).passed
