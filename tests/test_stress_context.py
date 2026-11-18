"""Segunda corrida del stress test (plan §6): corpus, marcadores densos y runner, con el modelo simulado."""

from __future__ import annotations

import json

from app.context.preprocessing import load_estimates
from app.services.llm_service import ContextOverrides, _context_for
from evals.stress import context_run
from evals.stress.context_run import Ledger, cache_state, corpus_plan, rejection_origin, run_b, run_c
from evals.stress.fixtures.build_corpus import NEEDLE_ID, NEEDLE_TOKEN, corpus
from evals.stress.fixtures.build_pdfs import DENSE_MARKERS, build_text
from tests.llm_fakes import tier_turn
from tests.test_sessions import _install, _result


def test_corpus_has_the_size_and_moves_only_the_needle() -> None:
    control = corpus(50, needle_hours=None)
    middle = corpus(50, needle_hours=400, position="middle")
    end = corpus(50, needle_hours=400, position="end")
    assert len(control) == len(middle) == 50
    assert [item["id"] for item in middle].index(NEEDLE_ID) == 24
    assert end[-1]["id"] == NEEDLE_ID and corpus(50, needle_hours=400, position="start")[0]["id"] == NEEDLE_ID
    assert [item["id"] for item in middle if item["id"] != NEEDLE_ID] == [item["id"] for item in end if item["id"] != NEEDLE_ID]
    needle = next(item for item in middle if item["id"] == NEEDLE_ID)
    assert sum(task["quantity"] for task in needle["tasks"]) == 400
    assert NEEDLE_TOKEN not in json.dumps(control, ensure_ascii=False)


def test_the_nonce_changes_only_the_first_example() -> None:
    plain, marked = corpus(25, needle_hours=None), corpus(25, needle_hours=None, nonce="abcd")
    assert plain[1:] == marked[1:] and "abcd" in marked[0]["client_profile"]


def test_custom_corpus_reaches_the_cag_block_with_and_without_caps() -> None:
    items = load_estimates(corpus(50, needle_hours=400, position="end"))
    capped = _context_for("", ContextOverrides(estimates=items))
    free = _context_for("", ContextOverrides(estimates=items, max_examples=10_000, context_max_tokens=10_000_000))
    assert len(capped.included_ids) == 7 and NEEDLE_ID not in capped.included_ids  # Por orden, no por relevancia.
    assert len(free.included_ids) == 50 and NEEDLE_ID in free.included_ids


def test_dense_markers_are_nine_and_in_order() -> None:
    text = build_text(50, DENSE_MARKERS)
    offsets = [text.index(marker.sentence) for marker in DENSE_MARKERS]
    assert len(DENSE_MARKERS) == 9 and offsets == sorted(offsets)


def test_rejection_origin_and_cache_state() -> None:
    assert rejection_origin(413, "context_budget_exceeded") == "local"
    assert rejection_origin(504, "llm_timeout") == "timeout"
    assert rejection_origin(502, "llm_output_truncated") == "truncated"
    assert rejection_origin(502, "llm_unavailable") == "provider"
    assert rejection_origin(502, "invalid_llm_output") == "invalid_output"
    assert rejection_origin(500, "service_error") == "unknown"
    assert rejection_origin(200, "") == ""
    assert cache_state("cold", 0, None) == "cold" and cache_state("cold", 500, None) == "partial"
    assert cache_state("warm", 500, None) == "warm" and cache_state("warm", 0, None) == "partial"


def test_block_c_calibrates_the_needle_on_the_control_and_resumes(monkeypatch, tmp_path) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        prompt = json.dumps(messages, ensure_ascii=False)
        assumptions = [f"Referencia: Turnos de estiba {NEEDLE_TOKEN}"] if NEEDLE_TOKEN in prompt else ["Sin referencia."]
        return _result(tier_turn("developer", assumptions=assumptions))

    _install(monkeypatch, complete)
    ledger = Ledger(tmp_path / "c.csv", resume=False, max_usd=10)
    plan = corpus_plan([25], ["end"], capped_sizes=[25], extra=[])
    run_c(plan, ledger, lambda _: None)
    rows = {(row["mode"], row["temperature"]): row for row in ledger.rows}
    control = [row for row in ledger.rows if row["mode"] == "control"]
    assert len(control) == 3 and all(row["status"] == "200" for row in ledger.rows)
    hours = float(control[0]["total_hours"])
    assert int(rows[("free", "cold")]["needle_hours"]) == int(round(2 * hours / 5) * 5)
    assert rows[("capped", "cold")]["needle_included"] == "0" and rows[("capped", "cold")]["needle_cited"] == "0"
    assert rows[("free", "cold")]["needle_included"] == "1" and rows[("free", "warm")]["needle_cited"] == "1"
    assert int(rows[("free", "cold")]["context_tokens"]) > int(rows[("capped", "cold")]["context_tokens"])
    assert rows[("free", "cold")]["hours_shift"] == "0.0"  # El modelo simulado da las mismas horas que el control.
    assert rows[("capped", "cold")]["hours_shift"] == ""  # Sin control emparejado con topes.
    assert rows[("free", "warm")]["nonce"] == rows[("free", "cold")]["nonce"]
    resumed = Ledger(tmp_path / "c.csv", resume=True, max_usd=10)
    assert len(resumed.done) == len(plan)


def test_block_b_measures_recall_over_the_markers_the_actor_could_see(client, monkeypatch, tmp_path) -> None:
    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        if name == "extract-metadata":
            return _result("{}")
        return _result(tier_turn("developer", assumptions=["Se cubren Orfeo y Vesta."]))

    _install(monkeypatch, complete)
    ledger = Ledger(tmp_path / "b.csv", resume=False, max_usd=10)
    run_b(client, "B1", [50], 1, ledger, {}, lambda _: None)
    row = ledger.rows[0]
    assert row["status"] == "200" and row["block"] == "B"
    visible = int(row["markers_visible"])
    assert 0 < visible < 9  # Con el tope de los tests (40.000), lo del final del pliego no llega.
    assert row["markers_found"] == "2" and float(row["answer_recall"]) == round(2 / visible, 3)
    assert int(row["actor_input_tokens"]) > 0


def test_a_local_rejection_is_not_counted_as_paid(tmp_path) -> None:
    ledger = Ledger(tmp_path / "x.csv", resume=False, max_usd=10)
    ledger.write({"block": "B", "variant": "B1", "size": 300, "repeat": 1, "status": 413, "rejection_origin": "local"})
    ledger.write({"block": "B", "variant": "B2", "size": 300, "repeat": 1, "status": 504, "rejection_origin": "timeout"})
    assert ledger.unknown == 1 and context_run._key(ledger.rows[0])[1] == "B1"


def test_context_report_renders_the_three_blocks(monkeypatch, tmp_path) -> None:
    from evals.stress.context_report import render

    async def complete(messages, response_format, *, name, metadata=None, model=None, estimated_input_tokens=None):
        return _result(tier_turn("developer"))

    _install(monkeypatch, complete)
    ledger = Ledger(tmp_path / "c.csv", resume=False, max_usd=10)
    run_c(corpus_plan([25], ["middle"], capped_sizes=[25], extra=[]), ledger, lambda _: None)
    b_row = dict.fromkeys(context_run.COLUMNS, "") | {
        "block": "B",
        "variant": "B1",
        "size": "200",
        "status": "413",
        "rejection_origin": "local",
        "markers_visible": "9",
    }
    a_row = {"variant": "A8", "scenario": "pivot", "turn_index": "1", "status": "200", "session_id": "s", "latency_ms": "1000",
             "tokens_out": "10", "cost_usd": "0.01", "drift_recall": "1.0", "drift_checked": "3", "drift_failures": ""}  # fmt: skip
    text = render([a_row], [b_row, *ledger.rows])
    for title in ("### A", "### B", "### C", "tal cual", "sin topes"):
        assert title in text
    assert "no: fallo duro" in text and "1 local" in text


def test_viability_needs_evidence_and_the_ledger_never_deletes_paid_rows(tmp_path) -> None:
    import pytest

    from evals.stress.context_report import _verdict

    assert _verdict(None, 0.01, 1.0, 0.8) == "datos insuficientes"
    assert _verdict(10_000, 0.01, 1.0, 0.8) == "sí"
    assert _verdict(30_000, 0.10, 0.5, 0.8) == "no: latencia, coste, recuperación"
    path = tmp_path / "c.csv"
    Ledger(path, resume=False, max_usd=1).write({"block": "C", "status": 200, "cost_usd": 0.1})
    with pytest.raises(SystemExit):
        Ledger(path, resume=False, max_usd=1)
