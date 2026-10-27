# Estimador CAG

Servicio FastAPI que recibe la transcripción de una reunión y devuelve una estimación de software. El contexto de referencia (tarifas y estimaciones históricas) viaja en cada llamada: es una arquitectura CAG, sin base de datos ni retrieval.

## Qué revisar para las conclusiones

El documento de la revisión es [experiments/CONCLUSIONES.md](experiments/CONCLUSIONES.md). Ahí está la estrategia elegida, cómo se puntuó y la tabla completa de la corrida del 26 de octubre de 2026. Los CSV que genera el script en `experiments/results/` no se versionan.

## Requisitos

- Python 3.11 o superior. El proyecto fija 3.12 en `.python-version`.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). En macOS o Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- Una clave del proxy LiteLLM. Sin `LITELLM_API_KEY` el proceso no arranca.
- Langfuse es opcional. Sin sus dos claves la aplicación funciona y no envía trazas.

## Puesta en marcha

Desde la raíz del repositorio:

```bash
uv sync
cp .env.example .env
```

Abre `.env` y rellena al menos `LITELLM_API_KEY`. El resto tiene valores por defecto. `.env` no se versiona.

Arranca un solo worker:

```bash
uv run uvicorn app.main:app --reload
```

Queda en `http://127.0.0.1:8000`. Para pararlo, `Ctrl+C` en esa terminal.

Comprueba que responde. `/health` debe devolver `status` igual a `healthy`, la versión y el modelo configurado:

```bash
curl -s http://127.0.0.1:8000/health
```

La documentación interactiva está en `http://127.0.0.1:8000/docs`.

Una estimación con la transcripción del ejercicio:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,pathlib; print(json.dumps({"transcription": pathlib.Path("data/transcripcion_reunion.md").read_text()}))')"
```

La transcripción tiene que tener al menos 50 caracteres. La respuesta trae el Markdown en `estimation`, el objeto en `estimation_structured`, `warnings`, `usage`, `cost_usd` y `trace_id`.

`./scripts/smoke_test.sh` repite la estimación dos veces, para ver `cached_tokens` en la segunda, y luego hace una conversación de tres turnos. Llama al modelo real.

Cada cambio en `.env` exige reiniciar uvicorn. La configuración se lee una vez y el cliente de LiteLLM se crea al importar la aplicación.

## Variables de entorno

| Variable | Obligatorio | Default | Valores | Qué ajusta |
| --- | --- | --- | --- | --- |
| `LITELLM_API_KEY` | sí | — | clave del proxy | Autenticación. Si falta, el proceso no arranca |
| `LITELLM_BASE_URL` | no | proxy del curso | URL sin barra final | Dónde se envían las llamadas |
| `LLM_MODEL` | no | `openrouter/openai/gpt-4o-mini` | nombre que entienda LiteLLM | Modelo. El proveedor sale del nombre |
| `LLM_FALLBACK_MODEL` | no | vacío | otro nombre de LiteLLM | Un solo reintento con ese modelo si el principal agota los reintentos por timeout, corte, 429, 5xx o un 400 de formato. También es el modelo barato si `LLM_ROUTING=complexity` |
| `LLM_ROUTING` | no | `off` | `off` o `complexity` | `complexity` manda al modelo barato solo una transcripción corta y con un requisito técnico como mucho. Ante la duda se queda en `LLM_MODEL` |
| `LLM_TEMPERATURE` | no | `0.2` | `0` a `2` | Variación de la respuesta |
| `LLM_MAX_TOKENS` | no | `8000` | entero mayor que 0 | Tope de la respuesta. Una estimación completa necesita varios miles |
| `LLM_TIMEOUT_SECONDS` | no | `60` | mayor que 0 | Espera antes de responder 504 |
| `LLM_MAX_RETRIES` | no | `3` | `0` o más | Reintentos del SDK ante 408, 409, 429 y 5xx |
| `LLM_RESPONSE_FORMAT` | no | `json_schema` | `json_schema` o `json_object` | Cómo se pide el JSON. `json_object` sirve si el modelo no acepta el esquema |
| `LLM_PROMPT_CACHE` | no | `auto` | `auto` o `off` | `cache_control` en modelos Anthropic. OpenAI y Gemini cachean el prefijo solos |
| `APP_ENV` | no | `development` | `development`, `staging`, `production` | Entorno de Langfuse. En `production` desaparece `/estimate/preview` |
| `APP_NAME` | no | `Estimador CAG` | texto | Nombre interno |
| `LOG_LEVEL` | no | `DEBUG` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Verbosidad |
| `LOG_FORMAT` | no | `json` | `json` o `text` | Formato de cada línea de log |
| `LOG_PROMPTS` | no | `false` | `true` o `false` | Si el log incluye la transcripción y los mensajes |
| `CONTEXT_FORMAT` | no | `markdown` | `markdown`, `plain`, `json` | Cómo se escriben tarifas y ejemplos dentro del prompt |
| `CONTEXT_DETAIL_LEVEL` | no | `standard` | `summary`, `standard`, `full` | Cuánto detalle lleva cada ejemplo |
| `CONTEXT_MAX_EXAMPLES` | no | `7` | entero mayor que 0 | Cuántos ejemplos vigentes entran |
| `CONTEXT_MAX_AGE_YEARS` | no | `4` | `0` o más | Antigüedad máxima. El ejemplo de 2017 queda fuera |
| `CONTEXT_MAX_TOKENS` | no | `6000` | entero mayor que 0 | Techo del bloque de contexto. Si no cabe, baja el detalle y luego descarta ejemplos |
| `MODEL_CONTEXT_WINDOW` | no | `128000` | entero mayor que 0 | Ventana del modelo, en tokens |
| `CONTEXT_SAFETY_MARGIN_TOKENS` | no | `5000` | `0` o más | Hueco que se reserva antes de llamar al modelo |
| `TRANSCRIPTION_MAX_CHARS` | no | `40000` | mayor que 50 | Largo máximo del texto de entrada |
| `CONVERSATION_MAX_TURNS` | no | `10` | entero mayor que 0 | Pares recientes que se conservan, además de las anclas |
| `CONVERSATION_TTL_MINUTES` | no | `60` | entero mayor que 0 | Vida de una conversación en memoria |
| `CONVERSATION_MAX_ACTIVE` | no | `1000` | entero mayor que 0 | Conversaciones simultáneas. Al pasarse, se elimina la más antigua |
| `CONVERSATION_NEAR_LIMIT_RATIO` | no | `0.8` | entre 0 y 1 | A partir de qué fracción de la ventana `near_limit` pasa a `true` |
| `LANGFUSE_PUBLIC_KEY` | no | vacío | clave pública del proyecto | Activa las trazas solo si también hay clave secreta |
| `LANGFUSE_SECRET_KEY` | no | vacío | clave secreta del proyecto | No la pegues en el chat ni en el repositorio |
| `LANGFUSE_BASE_URL` | no | `https://cloud.langfuse.com` | URL de la región | Cloud EU. En US es `https://us.cloud.langfuse.com` |

El proveedor no tiene variable propia. `openrouter/openai/...` es OpenAI, `openrouter/anthropic/...` es Anthropic y `gemini` se trata como Google.

## Langfuse

1. Crea un proyecto en [Langfuse Cloud](https://cloud.langfuse.com).
2. En el proyecto, abre Settings y API keys. Copia la clave pública y la secreta a `.env`.
3. Deja `LANGFUSE_BASE_URL` en la región de ese proyecto.
4. Reinicia el servidor.

El SDK instalado es Langfuse Python 5, el vigente para Langfuse Cloud v4. `APP_ENV` se publica como entorno de cada observación. Una conversación agrupa sus turnos por `session_id`. Las puntuaciones (`json_valid_first_try`, `warnings_total`, `cost_usd` y el resto) cuelgan de la observación raíz `generate-estimation`.

## API

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/health` | Estado del proceso |
| POST | `/api/v1/estimate` | Una transcripción, una estimación |
| GET | `/api/v1/context` | Ejemplos usados, presupuesto y caché |
| POST | `/api/v1/estimate/preview` | Prompt completo, sin llamar al modelo. Solo con `APP_ENV=development` |
| POST | `/api/v1/conversations` | Empieza una conversación con la transcripción |
| POST | `/api/v1/conversations/{id}/messages` | Siguiente turno: ajuste o pregunta |
| GET | `/api/v1/conversations/{id}` | Historial |
| DELETE | `/api/v1/conversations/{id}` | Borra la conversación |

El cuerpo de `POST /api/v1/estimate` y de `POST /api/v1/conversations` es `{"transcription": "..."}`. El de un mensaje siguiente es `{"message": "..."}`.

Ver el contexto que se inyectaría, sin gastar una llamada:

```bash
curl -s http://127.0.0.1:8000/api/v1/context
curl -s "http://127.0.0.1:8000/api/v1/context?include_text=true"
```

Ver el prompt armado, solo en desarrollo:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/estimate/preview \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,pathlib; print(json.dumps({"transcription": pathlib.Path("data/transcripcion_reunion.md").read_text()}))')"
```

Conversación:

```bash
# El primer turno devuelve conversation_id
curl -s -X POST http://127.0.0.1:8000/api/v1/conversations \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,pathlib; print(json.dumps({"transcription": pathlib.Path("data/transcripcion_reunion.md").read_text()}))')"

curl -s -X POST http://127.0.0.1:8000/api/v1/conversations/EL_ID/messages \
  -H "Content-Type: application/json" \
  -d '{"message": "Sube el diseño a 60 horas y añade una fase de testing."}'
```

El contrato v1 admite campos nuevos opcionales. Renombrar, quitar o cambiar el tipo de un campo exige `/api/v2` con su propio router, mientras v1 sigue respondiendo. Los `operation_id` no se renombran.

## Errores habituales

| HTTP | `error` | Qué ha pasado | Qué cambiar |
| --- | --- | --- | --- |
| el proceso no arranca | — | Falta `LITELLM_API_KEY` | Rellénala en `.env` y vuelve a lanzar uvicorn |
| 422 | validación de FastAPI | La transcripción tiene menos de 50 caracteres, o falta el campo | Alarga el texto o revisa el JSON |
| 413 | `context_budget_exceeded` | La petición no cabe en la ventana con el margen | Sube `MODEL_CONTEXT_WINDOW` si el modelo lo permite, baja `CONTEXT_MAX_TOKENS` o acorta la transcripción |
| 502 | `llm_unavailable` | El proxy no completó la llamada | Revisa la clave, `LITELLM_BASE_URL` y que el nombre de `LLM_MODEL` exista en el proxy |
| 502 | `llm_output_truncated` | La respuesta llegó a `LLM_MAX_TOKENS` | Súbelo. 3000 se queda corto en una actualización |
| 502 | `invalid_llm_output` | El JSON sigue siendo inválido después del reintento de reparación | Prueba `LLM_RESPONSE_FORMAT=json_object` |
| 504 | `llm_timeout` | El modelo tardó más de `LLM_TIMEOUT_SECONDS` | Sube el timeout |
| 404 | `conversation_not_found` | El id no existe o pasó el TTL | Empieza otra conversación |
| 404 en `/estimate/preview` | — | `APP_ENV` no es `development` | Cámbialo y reinicia |

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

Un worker. El historial y la caché de respuestas están en la memoria del proceso y se pierden al reiniciar. La caché devuelve la misma estimación si coinciden la transcripción, el modelo, la temperatura, la versión del prompt y el historial, durante 24 horas, y no llama al modelo. Varios workers tendrían memorias distintas. Quien itera `stream_estimation` ve los trozos del modelo; un acierto de caché llega de una vez. `POST /api/v1/estimate` sigue esperando la estimación completa.

## Dónde se ajusta el funcionamiento

La configuración de una ejecución va en `.env`. El comportamiento del producto vive en estos archivos. Después de editar código, reinicia el servidor.

| Quieres cambiar | Dónde |
| --- | --- |
| Modelo, temperatura, tope de salida, timeout, formato JSON, caché, tamaño del contexto, ventana y conversación | `.env`. Los defaults están en `app/config.py` |
| Tarifas por hora y sus rangos | `RATE_CARD` en `app/context/examples.py` |
| Estimaciones históricas que el modelo usa de referencia | `ESTIMATION_EXAMPLES` en `app/context/examples.py`. Datos en bruto, sin costes calculados a mano |
| Qué se considera obsoleto, cómo se anonimiza, cómo se pasa de días a horas y cómo se redacta cada nivel de detalle | `app/context/preprocessing.py` |
| Rol, formato de salida, restricciones, recordatorio de cada turno y orden del prompt | `app/services/prompt_builder.py` |
| Marca de caché de Anthropic y el punto de corte cuando los ejemplos dejen de ser estáticos | `apply_cache_strategy` en `app/services/prompt_builder.py` |
| Transporte hacia LiteLLM: reintentos, coste de la cabecera, tokens cacheados | `app/services/llm_client.py` |
| Qué se corrige solo (costes, totales, horas a múltiplos de 5) y qué se avisa en `warnings` | `app/services/postprocessor.py` |
| Orden de las etapas y el reintento cuando el JSON no vale | `app/services/llm_service.py` |
| Cuántos turnos caben, qué anclas no se tiran y el TTL | `app/state/conversations.py` y las variables `CONVERSATION_*` |
| Campos de la petición o de la respuesta | `app/schemas/`. Un cambio que rompa el contrato va a `/api/v2` |
| Comparar formato, detalle, número de ejemplos o modelo | `experiments/variants.toml` y `uv run python scripts/run_experiment.py` |
| Textos de prueba y rangos esperados | `data/transcripcion_reunion.md` y `data/transcripciones/` |

Los ejemplos van en un orden fijo. Reordenarlos por petición rompería la caché del prefijo. Cuando haya retrieval, el orden podrá cambiar y los ejemplos quedarán fuera de ese prefijo.

## Gestión del contexto

El prompt sigue este orden, de mayor a menor atención del modelo:

1. Rol, tarea y formato de salida.
2. Tabla de tarifas.
3. Estimaciones de referencia, con separadores.
4. Restricciones.
5. Transcripción, en el mensaje de usuario.

`context/examples.py` guarda los datos en bruto. `context/preprocessing.py` los valida, tira los obsoletos, anonimiza, normaliza unidades y roles, calcula costes y elige el nivel de detalle (`summary`, `standard`, `full`). El formato del bloque (`markdown`, `plain` o `json`) se cambia con `CONTEXT_FORMAT`.

Hay un presupuesto de toda la ventana (`tiktoken`, codificación `o200k_base`). Si la petición no cabe, responde 413 antes de llamar al modelo. No recorta la transcripción en silencio.

## Caché del prompt

El bloque de sistema es idéntico en todas las peticiones de un solo turno. OpenAI y Gemini cachean ese prefijo solos. Con un modelo de Anthropic y `LLM_PROMPT_CACHE=auto` se añade `cache_control`. `/api/v1/context` dice si el prefijo llega al mínimo orientativo (1024 tokens). La confirmación es `usage.cached_tokens` en la segunda llamada igual.

## Dónde se ve cada cosa

- La respuesta trae `prompt_version`, el presupuesto por bloque, los avisos, el coste y el `trace_id`.
- `GET /api/v1/context?include_text=true` muestra el texto que se inyecta y qué ejemplos se descartaron.
- Cada petición escribe una línea JSON de log. La transcripción entra en ese log solo con `LOG_PROMPTS=true`.
- En Langfuse, la traza `generate-estimation` tiene un tramo por etapa y la llamada al modelo es una generación propia (`generate-estimate`). Las conversaciones se agrupan por `session_id`.

## Experimentos

`data/transcripciones/` tiene cinco casos con un rango de horas esperado. `experiments/variants.toml` cambia una cosa cada vez (formato, detalle, número de ejemplos, modelo, caché).

```bash
uv run python scripts/run_experiment.py --dry-run
uv run python scripts/run_experiment.py --name contexto
uv run python scripts/run_experiment.py --name contexto --langfuse
```

`--dry-run` solo calcula tokens. Las otras dos llaman al modelo. Cada corrida escribe un informe en `experiments/results/`, y esa carpeta no se versiona. La lectura para la revisión, con la conclusión, está en [experiments/CONCLUSIONES.md](experiments/CONCLUSIONES.md).

## Tests y CI

```bash
uv run python scripts/check_structure.py
uv run ruff check .
uv run pytest
```

Los tests no llaman al modelo: sustituyen `llm_client.complete` y dejan las claves de Langfuse vacías. GitHub Actions comprueba la estructura, el lint, los tests, que el servidor arranca y el presupuesto de las variantes.

## Evolución

Cuando haya demasiados ejemplos para meterlos todos, `build_context_block()` recibirá la lista que devuelva un retrieval y los ejemplos dejarán de formar parte del prefijo cacheable. `llm_client.py` es el cliente que reutilizará la futura llamada de embeddings. `app/models/` queda reservado para los modelos de base de datos; Pydantic vive en `app/schemas/`.
