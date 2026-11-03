---
name: iterate-system-prompt
description: Itera el system prompt y el formato del contexto del estimador CAG. Úsala cuando se pida mejorar la calidad de las estimaciones, ajustar el prompt, comparar formatos de contexto o revisar warnings de una variante.
disable-model-invocation: true
---

# Iterar el prompt

Ciclo: ejecutar, evaluar, identificar, modificar, repetir.

1. Mira el prompt sin gastar, con el servidor en `development`: `POST /api/v2/estimate/preview` (o v1 con `transcription`).
2. Una versión nueva es una carpeta nueva en `app/prompts/estimation/<versión>/` (copia de la anterior). No edites una carpeta ya publicada. Cambia solo los `.j2` de esa carpeta.
3. Diagnóstico:
   - Redacción o condicionales de formato/detalle: los `.j2` de la versión.
   - Orden, segmentos o punto de caché: `app/services/prompt_builder.py` (no la redacción).
   - Precios descalibrados: revisa `RATE_CARD` y los ejemplos en `app/context/examples.py`.
   - Desglose genérico: sube el detalle de los ejemplos o usa `CONTEXT_DETAIL_LEVEL=full`.
   - Información irrelevante: refuerza `<constraints>` / `<scope>` o quita campos en el preprocesamiento.
4. Añade una variante en `experiments/variants.toml` con `prompt_version = "<carpeta>"` (una sola cosa distinta del baseline).
5. `uv run python scripts/run_experiment.py --name <variante>` y compara el informe de `experiments/results/` con `baseline`.
6. Revisa en Langfuse la traza `generate-estimation` si hay claves: JSON válido a la primera, warnings, tokens, caché y coste.
7. Anota el `prompt_version` nuevo (`<carpeta>-<hash>`) en `CHANGELOG.md`.
