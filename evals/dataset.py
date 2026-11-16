"""Golden dataset tipado. Una sola fuente para la suite de evals y para run_experiment."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(__file__).with_name("golden_dataset.yaml")


class Attachment(BaseModel):
    name: str
    text: str


class ExpectedMetadata(BaseModel):
    project_name: str | None = None
    rejected_options_any_of: list[str] = Field(default_factory=list)


class Expected(BaseModel):
    estimable: bool | None = None
    hours_range: tuple[float, float] | None = None
    components_any_of: list[str] = Field(default_factory=list)
    risks_any_of: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    metadata: ExpectedMetadata | None = None

    def as_properties(self) -> dict:
        """Lo que entiende app/evaluation/properties.py (Subject.expected)."""
        return {
            "estimable": self.estimable,
            "hours_range": list(self.hours_range) if self.hours_range else None,
            "components_any_of": self.components_any_of,
            "risks_any_of": self.risks_any_of,
        }


class GoldenCase(BaseModel):
    id: str
    category: str
    tiers: list[str]
    project_type: str = "web_saas"
    detail_level: str = "medium"
    input: str | None = None
    input_file: str | None = None
    turns: list[str] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    expected: Expected
    notes: str = ""
    reviewed: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> GoldenCase:
        sources = [bool(self.input), bool(self.input_file), bool(self.turns)]
        if sum(sources) != 1:
            raise ValueError(f"{self.id}: usa exactamente uno de input, input_file o turns.")
        return self

    @property
    def transcript(self) -> str:
        if self.turns:
            return self.turns[0]
        if self.input:
            return self.input
        text = (ROOT / str(self.input_file)).read_text(encoding="utf-8")
        if text.startswith("---"):
            _, _front, text = text.split("---", 2)
        return text.strip()


def load_dataset(path: Path = DATASET) -> list[GoldenCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [GoldenCase.model_validate(item) for item in data["cases"]]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay ids repetidos en el golden dataset.")
    return cases


def case_tier_pairs(cases: list[GoldenCase], *, single_turn_only: bool = False) -> list[tuple[GoldenCase, str]]:
    return [(case, tier) for case in cases for tier in case.tiers if not (single_turn_only and case.turns)]
