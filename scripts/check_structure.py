"""Comprueba la estructura que pide el ejercicio y los añadidos del proyecto."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/tokens.py",
    "app/observability.py",
    "app/schemas/__init__.py",
    "app/schemas/estimation.py",
    "app/schemas/context.py",
    "app/schemas/conversation.py",
    "app/routers/__init__.py",
    "app/routers/estimations.py",
    "app/routers/estimations_v2.py",
    "app/services/__init__.py",
    "app/services/llm_service.py",
    "app/services/prompt_builder.py",
    "app/services/llm_client.py",
    "app/services/postprocessor.py",
    "app/services/guardrails.py",
    "app/services/errors.py",
    "app/prompts/__init__.py",
    "app/prompts/loader.py",
    "app/prompts/estimation/v1/system.j2",
    "app/prompts/estimation/v1/user.j2",
    "app/prompts/estimation/v1/examples.j2",
    "app/prompts/estimation/v1/reminder.j2",
    "app/prompts/estimation/v1/repair.j2",
    "app/prompts/estimation/v2/system.j2",
    "app/prompts/estimation/v2/user.j2",
    "app/prompts/estimation/v2/examples.j2",
    "app/prompts/estimation/v2/reminder.j2",
    "app/prompts/estimation/v2/repair.j2",
    "app/context/__init__.py",
    "app/context/examples.py",
    "app/context/preprocessing.py",
    "app/state/__init__.py",
    "app/state/conversations.py",
    "app/evaluation/__init__.py",
    "app/evaluation/metrics.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "data/transcripcion_reunion.md",
    "data/transcripciones/01_landing_hubspot.md",
    "data/transcripciones/02_app_movil_reservas.md",
    "data/transcripciones/03_integracion_erp.md",
    "data/transcripciones/04_requisitos_vagos.md",
    "data/transcripciones/05_fuera_de_alcance.md",
    "experiments/variants.toml",
    "experiments/CONCLUSIONES.md",
    "scripts/smoke_test.sh",
    "scripts/run_experiment.py",
    "web/package.json",
    "web/app/page.tsx",
    "web/app/Markdown.tsx",
    ".github/workflows/ci.yml",
    ".agents/skills/verify-estimador/SKILL.md",
    ".agents/skills/iterate-system-prompt/SKILL.md",
    ".agents/skills/add-estimation-example/SKILL.md",
    ".agents/skills/update-changelog/SKILL.md",
]


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").is_file() else ""
    env_ignored = any(line.strip() == ".env" for line in gitignore.splitlines())
    if not env_ignored:
        missing.append(".gitignore debe incluir .env")
    if missing:
        print("Estructura incompleta:")
        for path in missing:
            print(f"  - {path}")
        return 1
    print(f"Estructura correcta ({len(REQUIRED)} rutas).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
