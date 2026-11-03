"""La plantilla v2 solo cambia el uso del contexto: debe pedir el ejemplo de referencia."""

from __future__ import annotations

from app.prompts.loader import available_versions, render_estimation_prompt
from app.schemas.estimation import DetailLevel, OutputFormat, ProjectType, PromptRenderRequest


def test_v2_is_listed_and_asks_for_reference_example() -> None:
    assert "v2" in available_versions()
    request = PromptRenderRequest(
        description="Build a tiny CRM for a real estate agency with contacts and deals.",
        project_type=ProjectType.WEB_SAAS,
        detail_level=DetailLevel.MEDIUM,
        output_format=OutputFormat.PHASES_TABLE,
    )
    system_v1, _ = render_estimation_prompt(request, "v1", rates="r", examples="e")
    system_v2, _ = render_estimation_prompt(request, "v2", rates="r", examples="e")
    assert "ejemplo de referencia" in system_v2.lower()
    assert system_v1 != system_v2
