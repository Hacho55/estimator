# Estimador CAG

Servicio FastAPI que recibe la transcripción de una reunión y devuelve una estimación de software. El contexto de referencia (tarifas y estimaciones históricas) viaja en cada llamada: es una arquitectura CAG, sin base de datos ni retrieval.

## Arquitectura

```
Cliente
  → routers/estimations.py          valida y delega
  → services/llm_service.py         generate_estimation()
       → context/preprocessing.py   formatea el contexto
       → services/prompt_builder.py arma el prompt y la caché
       → services/llm_client.py     LiteLLM (chat.completions, AsyncOpenAI)
       → services/postprocessor.py  valida, corrige y pasa a Markdown
```

El router no construye prompts ni traduce errores del modelo. Esos errores salen como `ServiceError` y `main.py` los convierte en HTTP. El estado de las conversaciones vive en `app/state/`, fuera del servicio.

## Gestión del contexto

El prompt sigue este orden, de mayor a menor atención del modelo:

1. Rol, tarea y formato de salida.
2. Tabla de tarifas.
3. Estimaciones de referencia, con separadores.
4. Restricciones.
5. Transcripción, en el mensaje de usuario.

`context/examples.py` guarda los datos en bruto. `context/preprocessing.py` los valida, tira los obsoletos, anonimiza, normaliza unidades y roles, calcula costes y elige el nivel de detalle (`summary`, `standard`, `full`). El formato del bloque (`markdown`, `plain` o `json`) se cambia con `CONTEXT_FORMAT`.

Hay un presupuesto de toda la ventana (`tiktoken`). Si la petición no cabe, responde 413 antes de llamar al modelo.

## API

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/health` | Estado del proceso |
| POST | `/api/v1/estimate` | Una transcripción, una estimación |
| GET | `/api/v1/context` | Ejemplos usados, presupuesto y caché |
| POST | `/api/v1/estimate/preview` | Prompt completo, sin llamar al modelo (solo `development`) |
| POST | `/api/v1/conversations` | Empieza una conversación |
| POST | `/api/v1/conversations/{id}/messages` | Siguiente turno |
| GET | `/api/v1/conversations/{id}` | Historial |
| DELETE | `/api/v1/conversations/{id}` | Reinicia |

La documentación interactiva está en `/docs`.

El contrato v1 admite campos nuevos opcionales. Renombrar, quitar o cambiar el tipo de un campo exige `/api/v2` con su propio router, mientras v1 sigue respondiendo. Los `operation_id` no se renombran.

## Puesta en marcha

Requiere Python 3.11+ y [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
```

Rellena `LITELLM_API_KEY`. El modelo por defecto es uno económico vía el proxy (`LLM_MODEL`). El proveedor sale del propio nombre (`openrouter/openai/...`, `openrouter/anthropic/...`).

Opcional: un proyecto en [Langfuse Cloud](https://cloud.langfuse.com) y sus claves en `LANGFUSE_PUBLIC_KEY` y `LANGFUSE_SECRET_KEY`. Sin claves no se envía ninguna traza.

```bash
uv run uvicorn app.main:app --reload
```

Un worker. El historial de las conversaciones está en memoria y se pierde al reiniciar.

```bash
curl -s http://127.0.0.1:8000/health

curl -s -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,pathlib; print(json.dumps({"transcription": pathlib.Path("data/transcripcion_reunion.md").read_text()}))')"
```

`./scripts/smoke_test.sh` hace dos estimaciones seguidas (para ver `cached_tokens` en la segunda) y una conversación de tres turnos.

## Caché del prompt

El bloque de sistema es idéntico en todas las peticiones de un solo turno. OpenAI y Gemini cachean ese prefijo solos. Con un modelo de Anthropic y `LLM_PROMPT_CACHE=auto` se añade `cache_control`. `/api/v1/context` dice si el prefijo llega al mínimo orientativo (1024 tokens). La confirmación es `usage.cached_tokens` en la segunda llamada.

## Dónde se ve cada cosa

- La respuesta trae `prompt_version`, el presupuesto por bloque, los avisos, el coste y el `trace_id`.
- `GET /api/v1/context?include_text=true` muestra el texto que se inyecta y qué ejemplos se descartaron.
- Cada petición escribe una línea JSON de log (sin la transcripción, salvo `LOG_PROMPTS=true`).
- En Langfuse, la traza `generate-estimation` tiene un tramo por etapa. Las conversaciones se agrupan por `session_id`.

## Experimentos

`data/transcripciones/` tiene cinco casos con un rango de horas esperado. `experiments/variants.toml` cambia una cosa cada vez (formato, detalle, número de ejemplos, modelo, caché).

```bash
uv run python scripts/run_experiment.py --dry-run
uv run python scripts/run_experiment.py --name contexto
uv run python scripts/run_experiment.py --name contexto --langfuse
```

El informe queda en `experiments/results/`.

## Tests y CI

```bash
uv run python scripts/check_structure.py
uv run ruff check .
uv run pytest
```

Los tests no llaman al modelo: sustituyen `llm_client.complete`. GitHub Actions comprueba la estructura, el lint, los tests, que el servidor arranca y el presupuesto de las variantes.

## Evolución

Cuando haya demasiados ejemplos para meterlos todos, `build_context_block()` recibirá la lista que devuelva un retrieval y los ejemplos dejarán de formar parte del prefijo cacheable. `llm_client.py` es el cliente que reutilizará la futura llamada de embeddings. `app/models/` queda reservado para los modelos de base de datos; Pydantic vive en `app/schemas/`.
