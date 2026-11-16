"""Familia 1 (hard): propiedades deterministas sobre la salida real. Una ejecución por caso y tier."""

from __future__ import annotations

import warnings

import pytest

from app.evaluation.properties import run_properties
from evals.conftest import CASES
from evals.dataset import case_tier_pairs

pytestmark = pytest.mark.eval
PAIRS = case_tier_pairs(CASES)
# Lo que depende de valores esperados escritos a mano; estructura y seguridad siempre bloquean.
ADVISORY_WHEN_DRAFT = {"hours_in_expected_range", "expected_components_present", "expected_risks_present"}


@pytest.mark.parametrize(("case", "tier"), PAIRS, ids=[f"{case.id}-{tier}" for case, tier in PAIRS])
def test_properties_hold(run, case, tier) -> None:
    result = run(case, tier)
    subject = result.subject()
    if case.expected.estimable is False:
        assert subject is None or not subject.view.is_estimable, "debía ser no estimable"
        return
    assert subject is not None, f"sin vista: kind={result.final.get('kind')}"
    failed = [item for item in run_properties(subject, offline=True) if not item.passed]
    # En casos sin revisar, los umbrales numéricos son borrador (D11): avisan, no bloquean.
    advisory = set() if case.reviewed else ADVISORY_WHEN_DRAFT
    for item in failed:
        if item.name in advisory:
            warnings.warn(f"{case.id}/{tier} (borrador) {item.name}: {item.detail}", stacklevel=1)
    blocking = [f"{item.name}: {item.detail}" for item in failed if item.name not in advisory]
    assert not blocking, "\n".join(blocking)


@pytest.mark.parametrize(("case", "tier"), PAIRS, ids=[f"{case.id}-{tier}" for case, tier in PAIRS])
def test_injected_text_does_not_leak(run, case, tier) -> None:
    if not case.expected.must_not_contain:
        pytest.skip("El caso no declara must_not_contain.")
    text = run(case, tier).view_text().casefold()
    leaked = [item for item in case.expected.must_not_contain if item.casefold() in text]
    assert not leaked, f"la salida contiene {leaked}"


SESSION_CASES = [case for case in CASES if case.expected.metadata]


@pytest.mark.parametrize("case", SESSION_CASES, ids=[case.id for case in SESSION_CASES])
def test_session_memory(run, case) -> None:
    final = run(case, case.tiers[0]).final["project_metadata"]
    expected = case.expected.metadata
    if expected.project_name:
        assert (final.get("project_name") or "").casefold() == expected.project_name.casefold()
    if expected.rejected_options_any_of:
        rejected = [item.casefold() for item in final.get("rejected_options") or []]
        assert any(option.casefold() in item for option in expected.rejected_options_any_of for item in rejected), rejected
