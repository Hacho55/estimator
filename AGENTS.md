# Convenciones del estimador

Arquitectura CAG: tarifas y estimaciones históricas viajan en cada llamada. No hay base de datos ni retrieval.

## Qué archivo se toca

- Contrato HTTP o campos de la respuesta: `app/schemas/`. Cambios que rompan el contrato van a `/api/v2`, no a v1. Los `operation_id` no se renombran.
- Texto del prompt, orden o caché: `app/services/prompt_builder.py`.
- Forma de los ejemplos dentro del prompt: `app/context/preprocessing.py`.
- Datos de referencia, sin lógica ni formato: `app/context/examples.py`.
- Llamada al modelo: solo `app/services/llm_client.py` (`AsyncOpenAI` contra LiteLLM, `chat.completions`).
- Validación de la estimación: `app/services/postprocessor.py`.
- Orquestación: `app/services/llm_service.py` (`generate_estimation`). No conoce HTTP.
- Historial de conversación: `app/state/`. Nunca dentro de `services/`.
- Pydantic va en `app/schemas/`. `app/models/` queda reservado para el ORM de más adelante.

## Contexto

Menos es más. El preprocesamiento anonimiza, normaliza y calcula los totales; el modelo no hace esa aritmética. Todos los niveles de detalle muestran las mismas secciones. El orden del prompt es rol, formato, tarifas, ejemplos, restricciones y, al final, la transcripción. Los ejemplos van en orden fijo para no romper la caché del prefijo.

## Conversación

`POST /api/v1/estimate` es de un solo turno. La conversación es optativa. En el historial se guarda la estimación ya corregida, no la salida en bruto. El primer turno y la última estimación no se descartan. Un solo worker: el almacén es memoria del proceso.

## Revisión

La conclusión del experimento de contexto está en `experiments/CONCLUSIONES.md`. Es el archivo que hay que leer para ver qué estrategia quedó elegida.

## Secretos y verificación

`.env` no se versiona. Los tests sustituyen `llm_client.complete` y no llevan claves de Langfuse.

Antes de dar un cambio por terminado: `uv run python scripts/check_structure.py`, `uv run ruff check .`, `uv run pytest`. Si cambió el prompt o los ejemplos, `uv run python scripts/run_experiment.py --dry-run`.

Cada cambio se anota en `CHANGELOG.md`, en `[Unreleased]`. Un cambio de prompt incluye su `prompt_version`.

Las skills del repo están en `.agents/skills/`: `verify-estimador`, `iterate-system-prompt`, `add-estimation-example`, `update-changelog`.
