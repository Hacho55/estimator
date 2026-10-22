from datetime import date

from app.context.examples import ESTIMATION_EXAMPLES
from app.context.preprocessing import build_context_block, load_estimates, prepare
from app.schemas.context import HistoricalEstimate


def test_obsolete_example_is_excluded_and_pii_is_removed():
    block = build_context_block(today=date(2026, 10, 22))

    assert "web-2017" not in block.included_ids
    assert any(item.id == "web-2017" and item.reason == "obsoleta" for item in block.excluded)
    assert "inv-2024" in block.included_ids
    assert "reservas-2025" in block.included_ids
    rendered = block.text
    assert "ana.lopez@clinicas.test" not in rendered
    assert "Ana López" not in rendered
    assert "600 123 456" not in rendered
    assert "comercial@empresa-interna.test" not in rendered
    assert "Margen comercial" not in rendered
    assert "PRJ-1044" not in rendered
    assert "2,5" not in rendered
    assert "diseñador UX" not in rendered
    assert "ux_ui" in rendered


def test_days_and_decimal_comma_become_hours():
    raw = next(item for item in ESTIMATION_EXAMPLES if item["id"] == "reservas-2025")
    prepared = prepare(HistoricalEstimate.model_validate(raw))
    design = next(task for task in prepared.tasks if task.name == "Flujos de reserva")
    assert design.hours == 20
    assert design.role == "ux_ui"
    assert design.cost_eur == 20 * 50


def test_totals_are_computed_not_copied():
    prepared = prepare(load_estimates()[0])
    assert prepared.total_hours == sum(task.hours for task in prepared.tasks)
    assert prepared.total_cost_eur == sum(task.cost_eur or 0 for task in prepared.tasks)


def test_every_detail_level_keeps_the_same_sections():
    for level in ("summary", "standard", "full"):
        block = build_context_block(detail_level=level, today=date(2026, 10, 22))
        text = block.examples_text
        for heading in ("Equipo", "Duración", "Supuestos", "Riesgos"):
            assert heading in text
        assert "===== ESTIMACIÓN DE REFERENCIA 1 =====" in text
        assert text.strip().endswith("===== FIN DE ESTIMACIONES DE REFERENCIA =====")


def test_formats_are_deterministic_and_distinct():
    markdown = build_context_block(fmt="markdown", today=date(2026, 10, 22))
    again = build_context_block(fmt="markdown", today=date(2026, 10, 22))
    plain = build_context_block(fmt="plain", today=date(2026, 10, 22))
    as_json = build_context_block(fmt="json", today=date(2026, 10, 22))
    assert markdown.examples_text == again.examples_text
    assert markdown.examples_text != plain.examples_text
    assert '"proyecto"' in as_json.examples_text
    assert "| Tarea |" in markdown.examples_text


def test_budget_drops_examples_from_the_end():
    block = build_context_block(max_tokens=200, today=date(2026, 10, 22))
    assert len(block.included_ids) < 5
    assert any(item.reason == "presupuesto" for item in block.excluded)
    assert any("presupuesto" in note for note in block.notes)


def test_build_context_block_accepts_an_arbitrary_list():
    one = load_estimates([ESTIMATION_EXAMPLES[0]])
    block = build_context_block(one, today=date(2026, 10, 22))
    assert block.included_ids == ["inv-2024"]
