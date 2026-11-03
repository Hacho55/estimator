"""Tests de la plantilla v1: sin modelo, solo el texto renderizado."""

from __future__ import annotations

import pytest
from jinja2 import Environment, StrictUndefined, UndefinedError

from app.context.preprocessing import build_context_block
from app.prompts.loader import (
    UnknownPromptVersionError,
    available_versions,
    render_estimation_prompt,
)
from app.schemas.estimation import DetailLevel, OutputFormat, ProjectType, PromptRenderRequest
from app.services.prompt_builder import build_messages, split_system_segments


def _request(**overrides) -> PromptRenderRequest:
    base = {
        "description": "A small CRM for a real estate agency: contacts, deals, role-based access.",
        "project_type": ProjectType.WEB_SAAS,
        "detail_level": DetailLevel.MEDIUM,
        "output_format": OutputFormat.PHASES_TABLE,
    }
    base.update(overrides)
    return PromptRenderRequest(**base)


def test_user_prompt_wraps_description_in_project_description_block() -> None:
    request = _request(description="UNIQUE-MARKER-12345 build a tiny scheduling app.")
    _system, user = render_estimation_prompt(request, rates="tarifas", examples="ejemplos")
    assert "<project_description>" in user
    assert "</project_description>" in user
    start = user.index("<project_description>")
    end = user.index("</project_description>")
    assert "UNIQUE-MARKER-12345" in user[start:end]


def test_phases_table_keyword_appears_only_when_format_requested() -> None:
    table_system, _ = render_estimation_prompt(
        _request(output_format=OutputFormat.PHASES_TABLE), rates="r", examples="e"
    )
    narrative_system, _ = render_estimation_prompt(
        _request(output_format=OutputFormat.NARRATIVE), rates="r", examples="e"
    )
    assert "phases_table" in table_system
    assert "phases_table" not in narrative_system


def test_detailed_includes_assumptions_per_phase_summary_does_not() -> None:
    detailed_system, _ = render_estimation_prompt(
        _request(detail_level=DetailLevel.DETAILED), rates="r", examples="e"
    )
    summary_system, _ = render_estimation_prompt(
        _request(detail_level=DetailLevel.SUMMARY), rates="r", examples="e"
    )
    assert "list assumptions per phase" in detailed_system.lower()
    assert "list assumptions per phase" not in summary_system.lower()


def test_examples_block_is_included_in_system_prompt() -> None:
    system, _ = render_estimation_prompt(_request(), rates="tarifas-fijas", examples="EJEMPLO-UNICO")
    assert "<examples>" in system
    assert "EJEMPLO-UNICO" in system
    assert "<rates>" in system
    assert "tarifas-fijas" in system


def test_strict_undefined_raises_on_missing_variable() -> None:
    env = Environment(undefined=StrictUndefined)
    template = env.from_string("Hello {{ unknown_variable }}")
    with pytest.raises(UndefinedError):
        template.render()


def test_unknown_version_raises() -> None:
    with pytest.raises(UnknownPromptVersionError):
        render_estimation_prompt(_request(), version="v999", rates="r", examples="e")


def test_available_versions_includes_v1() -> None:
    assert "v1" in available_versions()


def test_system_splits_into_cache_segments() -> None:
    block = build_context_block()
    messages = build_messages("Reunión suficientemente larga para estimar el proyecto descrito.", block)
    segments = messages[0]["segments"]
    assert [item["segment"] for item in segments] == ["static", "examples", "restrictions"]
    assert "<examples>" in segments[1]["text"]
    assert segments[2]["text"].startswith("<scope>")
    assert "<constraints>" in segments[2]["text"]
    rebuilt = split_system_segments(messages[0]["content"])
    assert [item["segment"] for item in rebuilt] == ["static", "examples", "restrictions"]
