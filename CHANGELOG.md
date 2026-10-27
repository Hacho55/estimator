# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa [versionado semántico](https://semver.org/lang/es/).

## [Unreleased]

### Añadido

- El cliente puede entregar la respuesta del modelo por trozos. Un acierto de caché sale de una vez. Si el stream se corta antes del primer token, vale el modelo de reserva; si ya empezó, no se cambia a mitad. `finish_reason=length` sigue sin guardarse en la caché. `POST /api/v1/estimate` no cambia.
- La caché de respuestas guarda en memoria, 24 horas, la estimación de una petición idéntica (transcripción, modelo, temperatura, versión del prompt e historial). Un acierto no llama al modelo y la respuesta lo marca en `cache_hit`.
- `LLM_ROUTING=complexity` elige el modelo antes de estimar. Solo una transcripción corta, sin historial y con un requisito técnico como mucho, pasa a `LLM_FALLBACK_MODEL`. El defecto sigue siendo `off`.
- Si `LLM_FALLBACK_MODEL` está definido, una llamada que falla por timeout, corte de conexión, 429, 5xx o un 400 de formato se repite una vez con ese modelo. La respuesta publica el modelo que contestó. 401, 403 y el resto de 400 no cambian de modelo.
- `experiments/CONCLUSIONES.md` recoge la corrida del 26 de octubre de 2026, la estrategia elegida y cómo se comparó con los rangos esperados. El README indica ese archivo como lectura de la revisión.

### Corregido

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
