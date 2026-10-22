# Changelog

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y el proyecto usa [versionado semántico](https://semver.org/lang/es/).

## [Unreleased]

### Cambiado

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
