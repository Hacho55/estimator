# Convenciones del estimador

Arquitectura CAG: tarifas y estimaciones históricas viajan en cada llamada. No hay base de datos ni retrieval.

## Referencia del curso (LIDR)

Material de enseñanza en `/Users/horaciosuarez/DEVS/AI-Eng/lidr-ai-engineering` (`LIDR-academy-temp/ai-engineering`). Consulta para ideas y conceptos; no obliga a paridad. Ver `CONSULTA.md` allí y la regla `.cursor/rules/lidr-curso-referencia.mdc`. Ramas `session_N` / `session_N_live`.

## Qué archivo se toca

- Contrato HTTP o campos de la respuesta: `app/schemas/`. Cambios que rompan el contrato van a `/api/v2`, no a v1. Los `operation_id` no se renombran.
- Texto del prompt: `app/prompts/estimation/<versión>/*.j2`. No se edita una carpeta ya publicada: una versión nueva es una carpeta nueva. Orden, segmentos y caché del prefijo: `app/services/prompt_builder.py`. Carga: `app/prompts/loader.py`.
- Forma de los ejemplos dentro del prompt: `app/context/preprocessing.py`.
- Datos de referencia, sin lógica ni formato: `app/context/examples.py`.
- Llamada al modelo y embeddings: solo `app/services/llm_client.py` (`AsyncOpenAI` contra LiteLLM), también el *function calling* (`complete_with_tools`).
- Herramientas: una por archivo en `app/tools/` (schema y ejecución; las llamadas HTTP a servicios externos van aquí, no al modelo). Cuándo se ofrecen: `app/services/tool_policy.py`. Bucle de investigación: `app/services/research.py`.
- Validación de la estimación: `app/services/postprocessor.py`.
- Guardrails (política declarada por control): `app/services/guardrails.py`.
- Orquestación: `app/services/llm_service.py` (`generate_estimation`). No conoce HTTP.
- Memoria de la sesión (hechos del proyecto): el extractor propone un delta en `app/services/metadata_extractor.py` y `app/state/memory.py` lo valida y fusiona, sin LLM. Las sesiones usan una ventana deslizante sin anclas porque los hechos que importan viven en la memoria, que no se recorta.
- Historial de conversación y caché exacta: `app/state/` en memoria del proceso. Caché semántica: `app/state/semantic_cache.py` (Redis si hay `REDIS_URL`; si no, memoria). Nunca dentro de `services/`.
- Perfiles (tiers): configuración en `app/tiers.py` (plantilla, schemas, herramientas y revisión por tier); schemas en `app/schemas/tiers.py` (borrador del modelo y vista del cliente); del borrador a la vista, sin LLM, en `app/services/tier_views.py`; plantillas `system_<tier>.j2` con parciales en la carpeta de versión. El tier llega por `app/security/caller.py` (JWT del backend de negocio o `AUTH_MODE=off`), nunca del cuerpo de la petición.
- Revisión Actor-Critic-Boss: contrato en `app/schemas/review.py`; crítico en dos capas (propiedades + LLM) en `app/services/critic.py` con su prompt en `app/prompts/critic/<versión>/`; decisión en `app/services/boss.py` (código, sin LLM); bucle con tope en `app/services/review_loop.py`. Se activa por tier en `app/tiers.py`.
- Backend de negocio simulado: `business_backend/` (otro servicio, puerto 8100, SQLite propia). Guarda `users.tier`, emite el JWT y decide qué es un proyecto similar. `app/` nunca lo importa ni abre su BBDD: le habla por HTTP (hay un test que lo comprueba).
- Pydantic va en `app/schemas/`. `app/models/` queda reservado para el ORM de más adelante.

## Contexto

Menos es más. El preprocesamiento anonimiza, normaliza y calcula los totales; el modelo no hace esa aritmética. Todos los niveles de detalle muestran las mismas secciones. El orden del prompt es rol, formato, tarifas, ejemplos, alcance, restricciones y, al final, la descripción. Los ejemplos van en orden fijo para no romper la caché del prefijo.

## Conversación y formulario

`POST /api/v1/estimate` sigue siendo de un solo turno con `transcription`. El formulario tipado y la conversación del front viven en `/api/v2`. En el historial se guarda la estimación ya corregida, no la salida en bruto. En `/api/v2/conversations`, el primer turno y la última estimación no se descartan; en `/sessions` la ventana es simple y la memoria guarda los hechos. Un solo worker: conversaciones y caché exacta son memoria del proceso.

## Revisión

La conclusión del experimento de contexto está en `experiments/CONCLUSIONES.md`. Ideas reutilizables de las sesiones 2–5: `docs/insights.md`.

## Secretos y verificación

`.env` no se versiona. Los tests sustituyen `llm_client.complete` (y el embedding cuando toca) y no llevan claves de Langfuse. Los marcados `presidio` y `redis` no corren en el job principal.

Antes de dar un cambio por terminado: `uv run python scripts/check_structure.py`, `uv run ruff check .`, `uv run pytest`. Si cambió el prompt o los ejemplos, `uv run python scripts/run_experiment.py --dry-run`.

Cada cambio se anota en `CHANGELOG.md`, en `[Unreleased]`. Un cambio de prompt incluye su `prompt_version`.

Las skills del repo están en `.agents/skills/`: `verify-estimador`, `iterate-system-prompt`, `add-estimation-example`, `update-changelog`.
