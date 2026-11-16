"""Ejecuta un caso del golden dataset contra el sistema real, como lo haría un cliente.

Pasa por la API (``/sessions``) con un JWT del tier: memoria, herramientas, revisión,
guardrails y vista, todo como en producción. Lo único que no es real es el transporte
HTTP (TestClient, en proceso).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt
from pydantic import TypeAdapter

from app.evaluation.properties import Subject
from app.schemas.tiers import TierEstimate
from app.security.caller import JWT_AUDIENCE
from evals.dataset import GoldenCase
from evals.pdf import text_pdf

_VIEW = TypeAdapter(TierEstimate)


@dataclass
class Spend:
    """Lo que costó la ejecución: se imprime al terminar la suite (coste visible)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, body: dict) -> None:
        self.calls += 1
        usage = body.get("usage") or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cost_usd += body.get("cost_usd") or 0.0


SPEND = Spend()


@dataclass
class CaseRun:
    case: GoldenCase
    tier: str
    bodies: list[dict] = field(default_factory=list)
    seconds: float = 0

    @property
    def final(self) -> dict:
        return self.bodies[-1]

    def view(self):
        structured = self.final.get("estimation_structured")
        return _VIEW.validate_python(structured) if structured else None

    def subject(self) -> Subject | None:
        """La vista final como ``Subject`` de las propiedades (con lo esperado del caso)."""
        view = self.view()
        if view is None:
            return None
        memory = self.final.get("project_metadata") or {}
        return Subject(tier=self.tier, view=view, memory=memory, expected=self.case.expected.as_properties())

    def view_text(self) -> str:
        return " ".join(
            str(self.final.get(key) or "") for key in ("estimation", "message")
        ) + " " + str(self.final.get("estimation_structured") or "")


def token_for(tier: str, secret: str, user: str = "eval") -> str:
    claims = {"sub": f"{user}-{tier}", "tier": tier, "aud": JWT_AUDIENCE, "exp": int(time.time()) + 3600}
    return jwt.encode(claims, secret, algorithm="HS256")


def run_case(client, case: GoldenCase, tier: str, secret: str) -> CaseRun:
    headers = {"Authorization": f"Bearer {token_for(tier, secret)}"}
    created = client.post("/sessions", headers=headers)
    created.raise_for_status()
    session_id = created.json()["session_id"]
    run = CaseRun(case=case, tier=tier)
    started = time.perf_counter()
    turns = case.turns or [case.transcript]
    for index, text in enumerate(turns):
        files = None
        if index == 0 and case.attachments:
            files = [("attachments", (item.name, text_pdf(item.text), "application/pdf")) for item in case.attachments]
        response = client.post(
            f"/sessions/{session_id}/estimate",
            data={"transcript": text, "project_type": case.project_type, "detail_level": case.detail_level},
            files=files,
            headers=headers,
        )
        assert response.status_code == 200, f"{case.id}/{tier} turno {index + 1}: {response.status_code} {response.text[:300]}"
        body = response.json()
        SPEND.add(body)
        run.bodies.append(body)
    run.seconds = time.perf_counter() - started
    return run
