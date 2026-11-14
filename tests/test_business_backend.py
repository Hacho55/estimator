"""Backend de negocio: JWT del usuario con su tier y la API interna de proyectos similares."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.security.caller import caller_from_token
from app.services.errors import AuthenticationError
from business_backend.config import get_business_settings

SECRET = "k" * 40
INTERNAL = "i" * 40
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv("BUSINESS_DB_PATH", str(tmp_path / "business.db"))
    monkeypatch.setenv("AI_SERVICE_SECRET", SECRET)
    monkeypatch.setenv("BUSINESS_INTERNAL_TOKEN", INTERNAL)
    get_business_settings.cache_clear()
    from business_backend.main import app

    with TestClient(app) as client:
        yield client
    get_business_settings.cache_clear()


def _login(backend, user_id: str) -> dict:
    response = backend.post("/api/auth/demo-login", json={"user_id": user_id})
    assert response.status_code == 200
    return response.json()


def test_login_token_is_accepted_by_the_estimator(backend, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "jwt")
    get_settings.cache_clear()
    body = _login(backend, "u-exec")
    caller = caller_from_token(body["token"])
    assert caller.user_id == "u-exec" and caller.tier.value == "executive"


def test_tier_changes_in_the_backend_and_comes_back_signed(backend, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "jwt")
    get_settings.cache_clear()
    token = _login(backend, "u-dev")["token"]
    changed = backend.patch("/api/me/tier", json={"tier": "executive"}, headers={"Authorization": f"Bearer {token}"})
    assert changed.status_code == 200
    assert caller_from_token(changed.json()["token"]).tier.value == "executive"
    assert {user["id"]: user["tier"] for user in backend.get("/api/users").json()}["u-dev"] == "executive"


def test_a_token_signed_with_another_secret_is_rejected_by_the_estimator(backend, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("AI_SERVICE_SECRET", "z" * 40)
    get_settings.cache_clear()
    get_business_settings.cache_clear()
    token = backend.post("/api/auth/demo-login", json={"user_id": "u-pm"}).json()["token"]
    monkeypatch.setenv("AI_SERVICE_SECRET", "y" * 40)
    get_settings.cache_clear()
    with pytest.raises(AuthenticationError):
        caller_from_token(token)


def test_unknown_user_and_unknown_tier_are_rejected(backend) -> None:
    assert backend.post("/api/auth/demo-login", json={"user_id": "nadie"}).status_code == 404
    token = _login(backend, "u-dev")["token"]
    bad = backend.patch("/api/me/tier", json={"tier": "ceo"}, headers={"Authorization": f"Bearer {token}"})
    assert bad.status_code == 422


def test_similar_projects_requires_the_internal_token(backend) -> None:
    body = {"technologies": ["React"], "scope_summary": "CRM con contactos"}
    assert backend.post("/api/internal/similar_projects", json=body).status_code == 401
    user_token = _login(backend, "u-dev")["token"]  # Un token de usuario no vale para la API interna.
    assert (
        backend.post("/api/internal/similar_projects", json=body, headers={"Authorization": f"Bearer {user_token}"}).status_code
        == 401
    )


def test_similar_projects_ranks_and_hides_internal_fields(backend) -> None:
    response = backend.post(
        "/api/internal/similar_projects",
        json={"technologies": ["Next.js", "Stripe"], "scope_summary": "reservas con pagos en línea y panel", "team_size": 2},
        headers={"Authorization": f"Bearer {INTERNAL}"},
    )
    projects = response.json()["projects"]
    assert projects[0]["project_id"] == "p-102"
    assert {"deviation_pct", "actual_hours", "materialized_risks"} <= set(projects[0])
    assert "client_name" not in projects[0] and "margin_pct" not in projects[0]
    assert all(project["project_id"] != "p-109" for project in projects)  # Cancelados no cuentan.
    assert len(projects) <= 5


def test_the_estimator_never_imports_the_business_backend() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "app").rglob("*.py")
        if "business_backend" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_tier_change_rejects_tokens_without_exp_or_sub(backend) -> None:
    import jwt as pyjwt

    no_exp = pyjwt.encode({"sub": "u-dev", "tier": "pm", "aud": "estimador"}, SECRET, algorithm="HS256")
    no_sub = pyjwt.encode({"tier": "pm", "aud": "estimador", "exp": 4_102_444_800}, SECRET, algorithm="HS256")
    for token in (no_exp, no_sub):
        response = backend.patch("/api/me/tier", json={"tier": "pm"}, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
