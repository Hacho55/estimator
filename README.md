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

Redis es opcional (caché semántica). Sin `REDIS_URL` el servicio usa un índice en memoria y sigue:

```bash
docker compose up -d redis
# REDIS_URL=redis://127.0.0.1:6379 en .env
```

Arranca un solo worker:

```bash
uv run uvicorn app.main:app --reload
```

Queda en `http://127.0.0.1:8000`. Para pararlo, `Ctrl+C` en esa terminal.

El front Next abre con el formulario tipado, crea una sesión (`POST /sessions`) y envía la transcripción y los adjuntos a `/sessions/{id}/estimate`. Primero tiene que estar uvicorn en el puerto 8000. Si el 3000 está ocupado, Next elige otro; la API acepta `localhost` y `127.0.0.1` en el puerto que salga:

```bash
cd web && npm install && npm run dev
```

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
| `LLM_PROMPT_VERSION` | no | `v1` | carpeta en `app/prompts/estimation/` | Versión de plantilla por defecto; en v2 se puede anular por query |
| `GUARDRAILS_MODE` | no | `log` | `log` o `enforce` | `log` registra inyecciones sin bloquear; `enforce` responde 422 |
| `PII_SCORE_THRESHOLD` | no | `0.5` | 0 a 1 | Umbral de Presidio (si hay modelo spaCy) |
| `REDIS_URL` | no | vacío | URL Redis | Activa redisvl para la caché semántica |
| `SEMANTIC_CACHE_MODE` | no | `log` | `off`, `log`, `on` | `log` mide vecinos sin servirlos; `on` sirve aciertos |
| `SEMANTIC_CACHE_THRESHOLD` | no | `0.92` | 0 a 1 | Similitud mínima (coseno) |
| `EMBEDDING_MODEL` | no | `openrouter/openai/text-embedding-3-small` | nombre LiteLLM | Modelo de embeddings |
| `EMBEDDING_DIMS` | no | `1536` | entero | Dimensiones del vector |
| `APP_ENV` | no | `development` | `development`, `staging`, `production` | Entorno de Langfuse. En `production` desaparece `/estimate/preview` |
| `APP_NAME` | no | `Estimador CAG` | texto | Nombre interno |
| `LOG_LEVEL` | no | `DEBUG` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Verbosidad |
| `LOG_FORMAT` | no | `json` | `json` o `text` | Formato del log de negocio y de las líneas `llm_call_*`. `text` las pinta en consola |
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
| `SESSION_MAX_TURNS` | no | `6` | entero mayor que 0 | Pares de una sesión. Al pasarse, se tiran los más viejos. Sin anclas |
| `ATTACHMENT_MAX_FILES` | no | `5` | entero mayor que 0 | Adjuntos por petición en `/sessions/{id}/estimate` |
| `ATTACHMENT_MAX_BYTES` | no | `5000000` | entero mayor que 0 | Tamaño máximo de cada adjunto |
| `ATTACHMENT_MAX_EXPANDED_BYTES` | no | `50000000` | entero mayor que 0 | Tamaño descomprimido máximo de un DOCX. El cuerpo multipart entero se corta en `ATTACHMENT_MAX_FILES × ATTACHMENT_MAX_BYTES` más el texto |
| `ATTACHMENT_MAX_CHARS` | no | `40000` | entero mayor que 0 | Texto de todos los adjuntos de un turno. Lo que pase se trunca y se avisa (`attachment_truncated`) |
| `METADATA_ATTACHMENT_CHARS` | no | `12000` | entero ≥ 0 | Texto de adjuntos que ve el extractor de memoria |
| `SESSION_IDLE_ARCHIVE_HOURS` | no | `24` | horas | Sin actividad, la sesión se archiva: no admite turnos (409) |
| `SESSION_ARCHIVE_RETENTION_HOURS` | no | `72` | horas | Una sesión archivada se purga pasado este tiempo |
| `METADATA_MODEL` | no | vacío (`LLM_MODEL`) | id de LiteLLM | Modelo del extractor de memoria de `/sessions` |
| `AUTH_MODE` | no | `off` | `jwt`, `off` | Cómo llega el tier: JWT del backend de negocio o `DEFAULT_TIER` fijo. Nunca del cuerpo |
| `DEFAULT_TIER` | no | `developer` | `developer`, `pm`, `executive` | Tier con `AUTH_MODE=off` |
| `AI_SERVICE_SECRET` | con `jwt` | vacío | ≥ 32 caracteres | Secreto HS256 compartido con `business_backend` |
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
| POST | `/api/v1/estimate/stream` | La misma petición, en eventos `token` y `estimate` |
| GET | `/api/v1/context` | Ejemplos usados, presupuesto y caché |
| POST | `/api/v1/estimate/preview` | Prompt completo, sin llamar al modelo. Solo con `APP_ENV=development` |
| POST | `/api/v1/conversations` | Empieza una conversación con la transcripción |
| POST | `/api/v1/conversations/{id}/messages` | Siguiente turno: ajuste o pregunta |
| GET | `/api/v1/conversations/{id}` | Historial |
| DELETE | `/api/v1/conversations/{id}` | Borra la conversación |
| GET | `/api/v2/prompts` | Versiones de plantilla y la de defecto |
| POST | `/api/v2/estimate` | Formulario tipado (`description`, `project_type`, `detail_level`, `output_format`) |
| POST | `/api/v2/estimate/stream` | Igual, en SSE; el `estimate` lleva `estimation_structured` |
| POST | `/api/v2/conversations` | Crea conversación con el formulario |
| POST | `/api/v2/conversations/stream` | Igual, en SSE |
| POST | `/api/v2/conversations/{id}/messages` | Turno de ajuste o pregunta |
| POST | `/api/v2/conversations/{id}/messages/stream` | Turno en SSE |
| GET | `/api/v2/conversations/{id}` | Historial |
| DELETE | `/api/v2/conversations/{id}` | Borra la conversación |

El cuerpo de `POST /api/v1/estimate` y de `POST /api/v1/conversations` es `{"transcription": "..."}`. El de v2 es el formulario tipado; `prompt_version` va en query. Un mensaje siguiente es `{"message": "..."}`. Los streams responden con `token` y cierran con `estimate` (o `error`). Un acierto de caché manda solo `estimate`.

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

## Sesiones

`POST /sessions` devuelve un `session_id` (UUID v4). `POST /sessions/{session_id}/estimate` acepta `multipart/form-data`: `transcript`, `project_type`, `detail_level` y `attachments` (PDF o Word, opcionales). La respuesta trae `tier`, la vista de ese perfil en `estimation_structured`, su markdown en `estimation`, `project_metadata` y `memory_changes`.

### Perfil (tier)

La estructura de la respuesta depende del perfil del llamante, no de un campo del formulario:

| Tier | Vista (`estimation_structured`) |
|---|---|
| `developer` | `components` (horas, coste, trabajo, riesgos técnicos y supuestos de stack), `uncertainty_drivers`, `total_hours_range` |
| `pm` | `phases`, `milestones` con `cumulative_cost_eur`, `team_composition`, `duration_weeks_range`, `blockers` |
| `executive` | `headline_cost_range`, `headline_duration_range`, `confidence_level` y su motivo, `top_three_risks`, `go_no_go_recommendation` |

El modelo devuelve un desglose común (rol y horas) y lo propio del perfil; tarifas, costes, totales y rangos los calcula el código (`app/services/tier_views.py`). Cada tier tiene su plantilla `app/prompts/estimation/v4/system_<tier>.j2`, montada con parciales, y su `prompt_version` (`v4-<tier>-<hash>`).

El tier llega firmado. Con `AUTH_MODE=jwt`, las dos rutas exigen `Authorization: Bearer <token>`: un JWT HS256 firmado con `AI_SERVICE_SECRET`, con `sub`, `tier`, `aud=estimador` y `exp`. La sesión pertenece al `sub` que la creó; otro usuario recibe 404. Con `AUTH_MODE=off` (por defecto, para desarrollo y tests) el tier es `DEFAULT_TIER`. En ningún modo se lee del cuerpo.

```bash
curl -s -X POST localhost:8000/sessions -H "Authorization: Bearer $TOKEN"
```

```bash
curl -s -X POST "localhost:8000/sessions/$SESSION_ID/estimate" -H "Authorization: Bearer $TOKEN" -F transcript="$(cat data/transcripcion_reunion.md)" -F project_type=web_saas -F detail_level=medium
```

### Almacén, ventana y olvido

La sesión vive en memoria del proceso, sin base de datos y sin Redis, y no pasa de `CONVERSATION_MAX_ACTIVE`. Hace falta un solo worker. El historial guarda solo pares user/assistant (`SESSION_MAX_TURNS`, por defecto 6) y no conserva anclas: los hechos que importan viven en la memoria (`project_metadata`), que no se recorta. El system prompt no cuenta como turno: se regenera en cada llamada. `/api/v1` y `/api/v2` siguen con su ventana de anclas y no usan este almacén.

Olvidar es explícito, en tres sitios:

- **Revisión** (memoria): si el usuario retira una tecnología, pasa a `rejected_options` y no se vuelve a proponer.
- **Caducidad** (almacén): tras `SESSION_IDLE_ARCHIVE_HOURS` sin actividad la sesión se archiva. Un turno sobre ella da 409 `session_archived`; se conserva para auditoría y se purga tras `SESSION_ARCHIVE_RETENTION_HOURS`.
- **Reset** (endpoint): `POST /sessions` con `{"previous_session_id": "...", "inherit_memory": false}` archiva la anterior y empieza de cero; con `inherit_memory: true` arranca con sus hechos y sin historial.

`GET /sessions/{id}` devuelve lo auditable: estado, turnos, tier, memoria y registro de cambios (`memory_log`), sin el texto del historial. `Session.to_record()` serializa historial y memoria por separado (`app/state/sessions.py`, contrato `SessionStore`), para que pasar a Redis no obligue a tocar el modelo.

Los tests, incluidos los de sesión, corren con:

```bash
uv run pytest
```

### Adjuntos (camino B)

El texto se extrae en el proceso, fuera del event loop, con `pypdf` (PDF, con marca `--- página N ---`) y `python-docx` (Word, con tablas en orden). No se llama a la Files API. Cada adjunto entra en el mensaje de usuario en su propio bloque `<attachment name="…">` dentro de `<attachments>`, declarado como dato: pasa el guardrail de PII y la inyección solo se registra. Todos los adjuntos de un turno comparten `ATTACHMENT_MAX_CHARS`; lo que sobra se trunca con aviso. La respuesta lista `attachments` (páginas, caracteres, si se truncó y milisegundos) y el presupuesto de tokens tiene un bloque `attachments`.

El adjunto se paga una vez: el historial guarda `[adjunto: nombre, N caracteres]`, no el texto. Lo que haya que recordar llega a la memoria, porque el extractor ve un extracto (`METADATA_ATTACHMENT_CHARS`). El primer turno exige 50 caracteres entre transcripción y adjuntos.

Se probó el otro camino contra el proxy: un PDF mínimo como bloque `type: file` con `file_data` en base64, por `chat.completions`. El modelo del entorno (`openrouter/anthropic/claude-sonnet-5.5`) y el de reserva (`openrouter/anthropic/claude-haiku-4.5`) devolvieron el código impreso en el PDF. No basta como camino único: un `.docx` no viaja en ese bloque, y el ejercicio pide un solo camino para PDF y Word.

### project_metadata (memoria)

Tras cada turno, una llamada corta (`app/prompts/metadata/v2/`, modelo `METADATA_MODEL`) propone un **delta**: qué fijar (`set_*`), qué añadir y qué retirar, con la frase del turno que lo justifica. `app/state/memory.py` lo valida (longitudes, rangos, contradicciones) y lo fusiona sin LLM. Retirar una tecnología la pasa a `rejected_options`. Cada cambio queda en `memory_changes` de la respuesta, con el turno y la evidencia. Si la llamada falla, la memoria no cambia y su latencia se suma igual.

La memoria entra al final del system de `v4`, campo a campo y solo con lo que tiene valor, como hechos establecidos que son datos y no instrucciones. Va en un segmento propio, después del punto de caché y fuera del hash de `prompt_version`: cambia en cada turno sin romper la caché ni la versión. Los tokens, el coste y la latencia de la respuesta suman la estimación y la extracción.

El contrato v1 admite campos nuevos opcionales. Renombrar, quitar o cambiar el tipo de un campo exige `/api/v2` con su propio router, mientras v1 sigue respondiendo. Los `operation_id` no se renombran.

## Errores habituales

| HTTP | `error` | Qué ha pasado | Qué cambiar |
| --- | --- | --- | --- |
| el proceso no arranca | — | Falta `LITELLM_API_KEY` | Rellénala en `.env` y vuelve a lanzar uvicorn |
| 422 | validación de FastAPI | La descripción tiene menos de 50 caracteres, enum inválido o falta el campo | Alarga el texto o revisa el JSON |
| 422 | `input_rejected` | Inyección de prompt con `GUARDRAILS_MODE=enforce` | Quita esas frases; en `log` solo se registra |
| 422 | `unknown_prompt_version` | La carpeta de prompt no existe | Usa `GET /api/v2/prompts` |
| 413 | `context_budget_exceeded` | La petición no cabe en la ventana con el margen | Sube `MODEL_CONTEXT_WINDOW` si el modelo lo permite, baja `CONTEXT_MAX_TOKENS` o acorta la transcripción |
| 502 | `llm_unavailable` | El proxy no completó la llamada | Revisa la clave, `LITELLM_BASE_URL` y que el nombre de `LLM_MODEL` exista en el proxy |
| 502 | `llm_output_truncated` | La respuesta llegó a `LLM_MAX_TOKENS` | Súbelo. 3000 se queda corto en una actualización |
| 502 | `invalid_llm_output` | El JSON sigue siendo inválido después del reintento de reparación | Prueba `LLM_RESPONSE_FORMAT=json_object` |
| 504 | `llm_timeout` | El modelo tardó más de `LLM_TIMEOUT_SECONDS` | Sube el timeout |
| 404 | `conversation_not_found` | El id no existe o pasó el TTL | Empieza otra conversación |
| 404 en `/estimate/preview` | — | `APP_ENV` no es `development` | Cámbialo y reinicia |

## Arquitectura

```
Cliente (front Next → /api/v2)
  → routers/estimations(_v2).py     valida y delega
  → services/llm_service.py         generate_estimation() / stream
       → services/guardrails.py     entrada (inyección, PII) y salida
       → context/preprocessing.py   formatea el contexto CAG
       → prompts/loader.py          plantillas Jinja2 versionadas
       → services/prompt_builder.py orden, segmentos y caché del prefijo
       → state/response_cache.py    caché exacta (memoria)
       → state/semantic_cache.py    caché semántica (Redis o memoria)
       → services/llm_client.py     LiteLLM (chat + embeddings)
       → services/postprocessor.py  valida, corrige y pasa a Markdown
```

El router no construye prompts ni traduce errores del modelo. Esos errores salen como `ServiceError` y `main.py` los convierte en HTTP. Conversaciones y caché exacta viven en `app/state/` (memoria del proceso). La caché semántica usa Redis si hay `REDIS_URL`.

Un worker. La caché exacta coincide en descripción, modelo, temperatura, versión del prompt e historial. La semántica solo mira el primer turno, con bucket por versión/formato/modelo. `SEMANTIC_CACHE_MODE=log` mide sin servir. Quien itera `stream_estimation` ve el markdown visible por trozos.

## Dónde se ajusta el funcionamiento

La configuración de una ejecución va en `.env`. El comportamiento del producto vive en estos archivos. Después de editar código, reinicia el servidor.

| Quieres cambiar | Dónde |
| --- | --- |
| Modelo, temperatura, tope de salida, timeout, formato JSON, caché, tamaño del contexto, ventana y conversación | `.env`. Los defaults están en `app/config.py` |
| Tarifas por hora y sus rangos | `RATE_CARD` en `app/context/examples.py` |
| Estimaciones históricas que el modelo usa de referencia | `ESTIMATION_EXAMPLES` en `app/context/examples.py`. Datos en bruto, sin costes calculados a mano |
| Qué se considera obsoleto, cómo se anonimiza, cómo se pasa de días a horas y cómo se redacta cada nivel de detalle | `app/context/preprocessing.py` |
| Redacción del prompt (rol, formato, restricciones, recordatorio) | `app/prompts/estimation/<versión>/*.j2`. Versión nueva = carpeta nueva |
| Orden, segmentos y marca de caché del prefijo | `app/services/prompt_builder.py` y `app/prompts/loader.py` |
| Transporte hacia LiteLLM: reintentos, coste, embeddings | `app/services/llm_client.py` |
| Guardrails (inyección, PII, fuera de alcance) | `app/services/guardrails.py` y `GUARDRAILS_MODE` |
| Qué se corrige solo (costes, totales, horas a múltiplos de 5) y qué se avisa en `warnings` | `app/services/postprocessor.py` |
| Orden de las etapas y el reintento cuando el JSON no vale | `app/services/llm_service.py` |
| Cuántos turnos caben, qué anclas no se tiran y el TTL | `app/state/conversations.py` y las variables `CONVERSATION_*` |
| Caché semántica | `app/state/semantic_cache.py`, `SEMANTIC_CACHE_*`, `REDIS_URL` |
| Campos de la petición o de la respuesta | `app/schemas/`. Un cambio que rompa el contrato va a `/api/v2` |
| Comparar formato, detalle, número de ejemplos o modelo | `experiments/variants.toml` y `uv run python scripts/run_experiment.py` |
| Textos de prueba y rangos esperados | `data/transcripcion_reunion.md` y `data/transcripciones/` |

Los ejemplos van en un orden fijo. Reordenarlos por petición rompería la caché del prefijo. Cuando haya retrieval, el orden podrá cambiar y los ejemplos quedarán fuera de ese prefijo.

## Gestión del contexto

El prompt sigue este orden, de mayor a menor atención del modelo:

1. Rol, tarea y formato de salida (condicionales por detalle y formato del formulario).
2. Tabla de tarifas.
3. Estimaciones de referencia, con separadores.
4. Alcance (`<scope>`) y restricciones.
5. Descripción del proyecto, en el mensaje de usuario.

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

Por defecto se saltan los marcados `presidio` y `redis`. Con el modelo spaCy y Redis:

```bash
uv run python -m spacy download es_core_news_md
uv run pytest -m presidio
REDIS_URL=redis://127.0.0.1:6379 uv run pytest -m redis
```

Los tests del job principal no llaman al modelo: sustituyen `llm_client.complete` (y el embedding cuando hace falta) y dejan las claves de Langfuse vacías. GitHub Actions añade jobs aparte para Presidio y Redis.

## Evolución

Cuando haya demasiados ejemplos para meterlos todos, `build_context_block()` recibirá la lista que devuelva un retrieval y los ejemplos dejarán de formar parte del prefijo cacheable. Los embeddings ya pasan por `llm_client.embed`. `app/models/` queda reservado para los modelos de base de datos; Pydantic vive en `app/schemas/`.
