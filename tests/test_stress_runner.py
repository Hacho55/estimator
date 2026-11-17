"""Runner del stress test, en proceso y con el modelo simulado: forma del CSV y reanudación."""

from __future__ import annotations

import csv
import json

import pytest

from app.state.response_cache import clear_response_cache
from evals.stress.fixtures.build_pdfs import ensure_pdfs
from evals.stress.metrics import CostBudgetMetric, LatencyBudgetMetric
from evals.stress.run import COLUMNS, CsvSink, Settings, Unit, attachments_scenario, plan_units, run_units
from evals.stress.scenarios import Scenario, get_scenario
from tests.llm_fakes import tier_turn
from tests.test_sessions import _install, _result


def _fake(monkeypatch) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result(json.dumps({"set_project_name": "Orion", "add_technologies": ["React"]}))
        return _result(tier_turn("developer"))

    _install(monkeypatch, complete)


def _settings() -> Settings:
    return Settings(
        tier="developer",
        secret=None,
        latency=LatencyBudgetMetric(25_000),
        cost=CostBudgetMetric(0.05),
        pdfs=ensure_pdfs((5,)),
    )


def _rows(path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_block_matrix_does_not_cross_multi_turn_with_sizes() -> None:
    units = plan_units(["pivot", "attachments", "burst"], [0, 5, 20], repeats=2, attachment_repeats=3, burst_sessions=4)
    assert sum(unit.scenario.name == "pivot" for unit in units) == 2
    assert {unit.attachment_kb for unit in units if unit.scenario.name == "pivot"} == {0}
    assert sum(unit.scenario.name == "attachments" for unit in units) == 9
    assert [unit.concurrency for unit in units if unit.scenario.name == "burst"] == [4] * 4
    full = plan_units(["pivot"], [0, 5], repeats=1, attachment_repeats=1, burst_sessions=1, matrix="full")
    assert [unit.attachment_kb for unit in full] == [0, 5]


def test_runner_writes_one_row_per_turn_with_observation_and_metrics(client, monkeypatch, tmp_path) -> None:
    _fake(monkeypatch)
    pivot = get_scenario("pivot")
    short = Scenario(name="pivot", project_type="mobile_app", detail_level="medium", turns=pivot.turns[:5])
    units = [Unit(short, 0, 1), Unit(attachments_scenario(), 5, 1)]
    output = tmp_path / "results.csv"
    written = run_units(client, units, _settings(), CsvSink(output, resume=False), log=lambda _: None)

    rows = _rows(output)
    assert written == len(rows) == 7
    assert list(rows[0]) == list(COLUMNS)
    pivot_rows = [row for row in rows if row["scenario"] == "pivot"]
    assert [row["turn_index"] for row in pivot_rows] == ["1", "2", "3", "4", "5"]
    assert pivot_rows[0]["first_fact_kept"] == "1"  # Orion en la memoria.
    assert pivot_rows[4]["drift_ok"] == "0"  # El extractor simulado nunca retira React.
    assert "stack.react" in pivot_rows[4]["drift_failures"]
    attached = [row for row in rows if row["scenario"] == "attachments"]
    assert int(attached[0]["attachments_total_chars"]) > 5000
    assert attached[0]["attachment_recall"] != ""
    assert all(row["latency_ok"] == "1" and row["status"] == "200" for row in rows)


def test_resume_keeps_complete_sessions_and_still_counts_the_discarded_spend(tmp_path) -> None:
    output = tmp_path / "results.csv"
    sink = CsvSink(output, resume=False, tier="developer")
    base = {"tier": "developer", "scenario": "attachments", "attachment_kb": 5, "concurrency": 1, "status": 200}
    complete = [{**base, "repeat": 1, "turn_index": turn, "tokens_in": 10, "cost_usd": 0.1} for turn in (1, 2)]
    partial = [{**base, "repeat": 2, "turn_index": 1, "tokens_in": 10, "cost_usd": 0.4}]
    sink.write(complete + partial)

    resumed = CsvSink(output, resume=True, tier="developer")
    assert resumed.done == {("attachments", "5", "1", "1")}
    assert len(_rows(output)) == 2
    assert len(_rows(output.with_suffix(".discarded.csv"))) == 1
    assert round(resumed.spent, 2) == 0.6  # Lo descartado también se pagó.
    with pytest.raises(SystemExit):
        CsvSink(output, resume=True, tier="pm")  # No mezcla tiers.


def test_runner_stops_between_turns_when_the_cap_is_crossed(client, monkeypatch, tmp_path) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        result = _result(json.dumps({}) if name == "extract-metadata" else tier_turn("developer"))
        result.cost_usd = 0.5
        return result

    _install(monkeypatch, complete)
    sink = CsvSink(tmp_path / "results.csv", resume=False, max_usd=0.9)
    messages: list[str] = []
    written = run_units(client, [Unit(get_scenario("pivot"), 0, 1)], _settings(), sink, log=messages.append)
    assert written == 1  # El turno 1 cuesta 1,0 USD (actor + extractor): no se paga el 2.
    assert any("Tope de gasto" in message for message in messages)


def test_unknown_cost_stops_the_run_unless_allowed(client, monkeypatch, tmp_path) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        result = _result(json.dumps({}) if name == "extract-metadata" else tier_turn("developer"))
        result.cost_usd = None
        return result

    _install(monkeypatch, complete)
    unit = Unit(attachments_scenario(), 5, 1)
    stopped = CsvSink(tmp_path / "a.csv", resume=False)
    assert run_units(client, [unit], _settings(), stopped, log=lambda _: None) == 1
    clear_response_cache()  # Si no, el turno 1 sería un acierto (sin coste del actor).
    allowed = CsvSink(tmp_path / "b.csv", resume=False, allow_unknown_cost=True)
    assert run_units(client, [unit], _settings(), allowed, log=lambda _: None) == 2
    assert allowed.unknown_cost == 2


def test_a_tier_other_than_requested_invalidates_the_row(client, monkeypatch, tmp_path) -> None:
    _fake(monkeypatch)
    settings = _settings()
    settings.tier = "executive"  # Sin JWT en los tests, el servidor resuelve su DEFAULT_TIER.
    output = tmp_path / "results.csv"
    run_units(client, [Unit(get_scenario("pivot"), 0, 1)], settings, CsvSink(output, resume=False), log=lambda _: None)
    rows = _rows(output)
    assert len(rows) == 1 and rows[0]["status"] == "tier_mismatch"


def test_failed_snapshot_leaves_drift_empty_instead_of_lost(client, monkeypatch, tmp_path) -> None:
    from evals.stress import run as runner

    _fake(monkeypatch)
    monkeypatch.setattr(runner, "_snapshot", lambda *args: (None, "snapshot: 500"))
    output = tmp_path / "results.csv"
    short = Scenario(name="pivot", project_type="mobile_app", detail_level="medium", turns=get_scenario("pivot").turns[:1])
    run_units(client, [Unit(short, 0, 1)], _settings(), CsvSink(output, resume=False), log=lambda _: None)
    row = _rows(output)[0]
    assert row["status"] == "200" and row["error"] == "snapshot: 500"
    assert row["drift_recall"] == "" and row["first_fact_kept"] == ""


def test_report_renders_every_table_from_the_csv(client, monkeypatch, tmp_path) -> None:
    from evals.stress.report import load, percentile, render

    _fake(monkeypatch)
    pivot = get_scenario("pivot")
    short = Scenario(name="pivot", project_type="mobile_app", detail_level="medium", turns=pivot.turns[:3])
    units = [Unit(short, 0, 1), Unit(attachments_scenario(), 5, 1), Unit(get_scenario("burst"), 0, 1, concurrency=2)]
    output = tmp_path / "results.csv"
    run_units(client, units, _settings(), CsvSink(output, resume=False), log=lambda _: None)

    text = render(load(output))
    for title in ("Resumen", "Curva 1", "Curva 2", "Curva 3", "Adjuntos", "Ráfaga"):
        assert f"## {title}" in text
    assert "| pivot | 0 | 3 |" in text
    assert percentile([1, 2, 3, 4], 50) == 2 and percentile([], 95) is None


def test_a_paid_failure_counts_as_unknown_cost_and_a_fresh_run_resets_the_ledger(tmp_path) -> None:
    output = tmp_path / "results.csv"
    sink = CsvSink(output, resume=False)
    base = {"tier": "developer", "scenario": "pivot", "attachment_kb": 0, "repeat": 1, "concurrency": 1}
    sink.write([{**base, "status": "422", "session_id": "s1"}, {**base, "status": "transport", "session_id": "s1"}])
    assert sink.unknown_cost == 1  # El 422 no llegó al modelo; el fallo de transporte pudo pagarse.
    CsvSink(output, resume=True, tier="developer")
    assert output.with_suffix(".discarded.csv").exists()
    CsvSink(output, resume=False)
    assert not output.with_suffix(".discarded.csv").exists()
