---
name: verify-estimador
description: Verifica el estimador CAG tras un cambio en app/, los tests o los scripts. Ejecuta la comprobación de estructura, ruff, pytest y, si el servidor puede arrancar, /health, /docs y /api/v1/context. Úsala al terminar cambios del estimador.
---

# Verificar el estimador

Ejecuta, en este orden, desde la raíz del repo:

1. `uv run python scripts/check_structure.py`
2. `uv run ruff check .`
3. `uv run pytest`
4. `uv run python scripts/run_experiment.py --dry-run`

Si hace falta comprobar el proceso en marcha:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`curl --fail` a `/health`, `/docs` y `/api/v1/context`.

`./scripts/smoke_test.sh` llama al modelo real y gasta crédito. Solo si quien pide la verificación lo quiere y `.env` tiene clave.
