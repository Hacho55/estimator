# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa [versionado semántico](https://semver.org/lang/es/).

## [Unreleased]

### Eliminado

- Streamlit y Chainlit dejan de ser clientes del estimador. El front en `web/` queda como única interfaz.

### Añadido

- `/api/v2` publica el formulario tipado (`description`, `project_type`, `detail_level`, `output_format`) y `GET /api/v2/prompts`. La carpeta de plantilla se elige con `prompt_version` (query) o `LLM_PROMPT_VERSION` (defecto `v1`). La variante `v2` pide nombrar el ejemplo de referencia en los supuestos; el experimento `prompt-v2` la compara con `baseline`.
- `POST /api/v2/conversations/stream` y `.../messages/stream` envían `token` y cierran con `estimate` (incluye `conversation_id`, `turn`, `kind`, `conversation_state` y `estimation_structured`). El historial solo se escribe tras un cierre correcto.
- El front Next abre con el formulario tipado (descripción, tipo, detalle, formato), elige la versión de prompt en opciones avanzadas, crea la conversación por SSE y dibuja `estimation_structured` según el formato. Tras la primera estimación admite un ajuste o pregunta sin reescribir el prompt.
- Guardrails de entrada y salida en `app/services/guardrails.py` (`GUARDRAILS_MODE=log|enforce`): inyección de prompt, PII con Presidio (o regex si falta spaCy), fuera de alcance y PII en la estimación. El prompt gana el bloque `<scope>` (nuevo hash `v1-` / `v2-`).
- Caché semántica (`app/state/semantic_cache.py`, Redis opcional vía `docker compose`): bucket por versión/formato/modelo y embedding de la descripción. `SEMANTIC_CACHE_MODE=log` por defecto; la respuesta incluye `cache_kind` y `cache_similarity`.
- Tras el bloque `<scope>` y los condicionales del formulario, las huellas vigentes son `v1-fffb730b` y `v2-80fa5ba0` (detalle medium, formato phases_table).
- `Estimation` calcula `phases` a partir de las tareas. `phase_assumptions` es opcional y, con detalle `detailed`, su ausencia deja un aviso.
- El texto del prompt vive en plantillas Jinja2 versionadas (`app/prompts/estimation/v1/`). El loader renderiza system y user; `prompt_builder` solo ordena segmentos, caché e historial. La versión informada sigue siendo `v1-<hash>` del texto renderizado.
- El front en `web/` es una aplicación Next. Desde la sesión 4 habla con `/api/v2/conversations/stream` (formulario tipado); el stream de v1 (`POST /api/v1/estimate/stream`) sigue disponible para clientes que solo envían `transcription`.
- `POST /api/v1/estimate/stream` y los streams de v2 envían cada trozo en un evento `token` y cierran con `estimate`. Un acierto de caché manda solo `estimate`.
- Cada llamada al modelo deja tres posibles líneas con structlog (`llm_call_started`, `llm_call_completed`, `llm_call_failed`): modelo, proveedor, tokens, coste, latencia, `finish_reason`, si vino de caché y si entró el modelo de reserva. `LOG_FORMAT` sigue eligiendo JSON o consola. El evento `estimation` y Langfuse no cambian.
- El cliente puede entregar la respuesta del modelo por trozos. Un acierto de caché sale de una vez. Si el stream se corta antes del primer token, vale el modelo de reserva; si ya empezó, no se cambia a mitad. `finish_reason=length` sigue sin guardarse en la caché. `POST /api/v1/estimate` no cambia.
- La caché de respuestas guarda en memoria, 24 horas, la estimación de una petición idéntica (transcripción, modelo, temperatura, versión del prompt e historial). Un acierto no llama al modelo y la respuesta lo marca en `cache_hit`.
- `LLM_ROUTING=complexity` elige el modelo antes de estimar. Solo una transcripción corta, sin historial y con un requisito técnico como mucho, pasa a `LLM_FALLBACK_MODEL`. El defecto sigue siendo `off`.
- Si `LLM_FALLBACK_MODEL` está definido, una llamada que falla por timeout, corte de conexión, 429, 5xx o un 400 de formato se repite una vez con ese modelo. La respuesta publica el modelo que contestó. 401, 403 y el resto de 400 no cambian de modelo.
- `experiments/CONCLUSIONES.md` recoge la corrida del 26 de octubre de 2026, la estrategia elegida y cómo se comparó con los rangos esperados. El README indica ese archivo como lectura de la revisión.

### Corregido

- Langfuse aplica las mismas máscaras de PII a generaciones OpenAI y a input/output del SDK; el log de Redis ya no incluye credenciales. El postprocesador alinea siempre `cost_eur` tras redondear horas. `available_versions` exige también `examples.j2`. El recorte de historial usa la versión y el formulario reales. Create v1 limpia conversaciones vacías; create/message tienen `max_length`. OpenAPI v1 documenta 422 como `ErrorResponse`.
- El historial de conversación y las trazas de Langfuse guardan la descripción ya tachada; los mensajes `answer` y la caché exacta/semántica usan el turno postprocesado. `available_versions` exige las cuatro plantillas. Los 422 de validación FastAPI salen como `ErrorResponse`. Al crear una conversación, un fallo antes de escribir turnos la descarta en `finally`. El front borra la conversación al volver al formulario y pinta el equipo de la estimación.
- La versión de prompt inválida en streams de v2 responde 422 con `ErrorResponse` (antes rompía el SSE). Las conversaciones vacías tras un fallo del primer turno se descartan.
- La caché exacta distingue `project_type`, detalle y formato del formulario. El repair usa la carpeta de prompt pedida. El borrador en streaming pasa por el mismo filtro de PII que la salida final. La caché semántica en memoria tiene tope de entradas.
- La caché semántica en Redis guarda la huella SHA-256 de la descripción en vez del texto; el match sigue siendo por vector.
- La latencia de un fallback incluye el intento fallido. La caché de respuestas purga lo vencido y no pasa de 256 entradas. El navegador puede borrar una conversación.
- El front distingue el error de la API, avisa si el stream se corta y recarga el contexto cuando cambia la transcripción. La tabla cabe en una pantalla estrecha.
- Mientras el modelo escribe, el front muestra el markdown de la estimación. El JSON no se pinta. Al cerrar, sigue el texto ya validado.
- En el front, la tabla de tareas se pinta como tabla. Tras la llamada se ven modelo, tokens, latencia y si salió de caché. El prompt y el contexto inyectado se abren con «Ver contexto».
- El navegador del front puede llamar a la API desde `localhost` o `127.0.0.1` en cualquier puerto. La estimación en pantalla usa la misma tipografía que el formulario.
- El CI deja de podar la caché de uv al terminar. Ese paso fallaba con el código 2 después de que estructura, ruff, tests, arranque y presupuesto ya habían pasado.
- El experimento llama al modelo dentro de un solo bucle de asyncio. Cada caso abría y cerraba el suyo, y la segunda llamada fallaba con `Event loop is closed`.

### Cambiado

- Comentarios que marcan qué código es observabilidad (logs y trazas de Langfuse) y qué código calcula la estimación.
- Los nombres internos (constantes, etiquetas del prompt, claves del JSON de contexto y códigos de exclusión) pasan a inglés. El texto que ve el usuario y el que lee el modelo siguen en español. El prompt pasa a `v1-983141c6`.
- Trazas de Langfuse alineadas con la plataforma v4 y el SDK de Python 5: cada observación lleva sesión, entorno y versión; la generación del modelo queda a cargo de la integración de OpenAI; las puntuaciones cuelgan de la observación raíz.
- El README documenta la puesta en marcha, las variables de entorno, los errores habituales y el archivo que hay que tocar para cada ajuste.

## [0.1.0] - 2026-10-22

### Añadido

- Servicio FastAPI que estima software a partir de la transcripción de una reunión, con arquitectura CAG.
- Contexto estático con tabla de tarifas y estimaciones históricas. El preprocesamiento anonimiza, normaliza unidades y roles, calcula costes y controla el tamaño del bloque.
- Prompt con rol, formato, tarifas, ejemplos y restricciones, y caché del prefijo cuando el proveedor la soporta.
- Llamada a LiteLLM con `chat.completions`, reintentos, coste, tokens y `cached_tokens`.
- Postprocesamiento: la salida JSON se valida, se corrigen costes y totales, y el resto se señala en `warnings`.
- `POST /api/v1/estimate`, `GET /health`, `GET /api/v1/context` y, en desarrollo, `POST /api/v1/estimate/preview`.
- Conversación multi-turn en memoria, con ventana deslizante y la transcripción y la última estimación como anclas.
- Tests con el cliente LLM simulado, comprobación de estructura y workflow de GitHub Actions.
- Trazas opcionales en Langfuse y `scripts/run_experiment.py` para comparar variantes de contexto.
- Prompt de referencia `v1-a0209ae4`. La versión (`v1-` más el hash del prefijo) viaja en cada respuesta.
- Límite de salida `LLM_MAX_TOKENS` en 8000, para que una estimación completa y su actualización quepan en una sola respuesta.
