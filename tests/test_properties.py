"""Cada propiedad con un caso que pasa y otro que falla. Sin LLM."""

from __future__ import annotations

import pytest

from app.evaluation import properties as props
from app.evaluation.metrics import collect_metrics
from app.evaluation.properties import Subject, run_properties
from app.schemas.estimation import UsageInfo
from app.schemas.tiers import DeveloperDraft, ExecutiveDraft, PmDraft, Range
from app.services.postprocessor import validate_and_fix
from app.services.tier_views import build_view, to_estimation_draft
from tests.llm_fakes import tier_draft

DRAFTS = {"developer": DeveloperDraft, "pm": PmDraft, "executive": ExecutiveDraft}


def _subject(tier: str, memory: dict | None = None, expected: dict | None = None, **overrides) -> Subject:
    draft = DRAFTS[tier](**tier_draft(tier, **overrides))
    estimation, _ = validate_and_fix(to_estimation_draft(draft))
    return Subject(tier=tier, view=build_view(tier, draft, estimation), estimation=estimation, memory=memory, expected=expected or {})


@pytest.mark.parametrize("tier", ["developer", "pm", "executive"])
def test_a_good_estimation_passes_every_online_property(tier) -> None:
    failed = [result for result in run_properties(_subject(tier)) if not result.passed]
    assert failed == []


def test_schema_valid_fails_with_the_view_of_another_tier() -> None:
    subject = _subject("pm")
    subject.tier = "developer"
    assert not props.schema_valid(subject).passed


def test_ranges_ordered_fails_on_an_inverted_range() -> None:
    subject = _subject("executive")
    subject.view.headline_cost_range = Range(min=9000, max=1000)
    result = props.ranges_ordered(subject)
    assert not result.passed and "headline_cost_range" in result.detail


def test_absolute_bounds_fail_on_a_runaway_total() -> None:
    subject = _subject("developer")
    subject.view.total_hours = 250_000
    assert not props.hours_within_absolute_bounds(subject).passed


def test_hours_and_rates_fail_on_a_bad_task() -> None:
    subject = _subject("developer")
    subject.estimation.tasks[0].hours = 42
    subject.estimation.tasks[1].rate_eur = 500
    assert not props.hours_multiple_of_5(subject).passed
    assert not props.rates_within_card(subject).passed


def test_developer_components_fail_without_components() -> None:
    subject = _subject("developer")
    subject.view.components = []
    assert not props.developer_components(subject).passed


def test_pm_milestones_fail_when_orphaned() -> None:
    subject = _subject("pm")
    subject.view.milestones[0].phase = "Marketing"
    assert not props.pm_milestones(subject).passed


def test_executive_headline_fails_with_two_risks() -> None:
    subject = _subject("executive")
    subject.view.top_three_risks = ["uno", "dos"]
    assert not props.executive_headline(subject).passed


def test_rejected_option_proposed_again_is_caught() -> None:
    memory = {"rejected_options": ["Next.js"]}
    assert not props.respects_rejected_options(_subject("developer", memory=memory)).passed  # stack_assumptions.
    assert props.respects_rejected_options(_subject("developer", memory={"rejected_options": ["Rails"]})).passed


def test_team_size_from_memory_is_respected() -> None:
    assert props.respects_team_size(_subject("pm", memory={"assumed_team_size": 2})).passed
    assert not props.respects_team_size(_subject("pm", memory={"assumed_team_size": 6})).passed


def test_offline_properties_against_the_case() -> None:
    good = _subject("developer", expected={"estimable": True, "hours_range": [80, 140], "components_any_of": ["frontend"]})
    assert all(result.passed for result in run_properties(good, offline=True))
    bad = _subject(
        "developer",
        expected={"estimable": False, "hours_range": [400, 600], "risks_any_of": ["pasarela de pagos"]},
    )
    failed = {result.name for result in run_properties(bad, offline=True) if not result.passed}
    assert failed == {"estimable_matches", "hours_in_expected_range", "expected_risks_present"}


def test_not_estimable_views_skip_content_checks() -> None:
    subject = _subject("executive", is_estimable=False, not_estimable_reason="No es software.", top_three_risks=[], go_no_go_recommendation="")
    assert all(result.passed for result in run_properties(subject))


def test_properties_become_online_scores() -> None:
    subject = _subject("pm")
    scores = collect_metrics(
        first_try=True,
        warnings=[],
        estimation=subject.estimation,
        usage=UsageInfo(input_tokens=10, output_tokens=5, total_tokens=15, cached_tokens=0),
        cost_usd=None,
        latency_ms=10,
        view=subject.view,
        tier="pm",
    )
    assert scores["prop_pm_milestones"] == 1.0
    assert scores["prop_schema_valid"] == 1.0


def test_rejecting_an_option_in_a_risk_is_not_a_proposal() -> None:
    memory = {"rejected_options": ["Rails"]}
    subject = _subject("pm", memory=memory, risks=["El cliente descartó Rails; si vuelve, cambia el plan."])
    assert props.respects_rejected_options(subject).passed
    proposing = _subject(
        "pm",
        memory=memory,
        work=[{"name": "API en Rails", "group": "Desarrollo", "role": "backend", "hours": 40}],
        milestones=[{"name": "API", "phase": "Desarrollo", "deliverable": "API"}],
    )
    assert not props.respects_rejected_options(proposing).passed


def test_expected_terms_are_searched_in_their_own_section() -> None:
    only_in_summary = _subject(
        "developer",
        summary="Incluye una pasarela de pagos más adelante.",
        expected={"risks_any_of": ["pasarela de pagos"], "components_any_of": ["pasarela de pagos"]},
    )
    failed = {result.name for result in run_properties(only_in_summary, offline=True) if not result.passed}
    assert {"expected_risks_present", "expected_components_present"} <= failed
