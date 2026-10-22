---
name: iterate-system-prompt
description: Itera el system prompt y el formato del contexto del estimador CAG. Úsala cuando se pida mejorar la calidad de las estimaciones, ajustar el prompt, comparar formatos de contexto o revisar warnings de una variante.
disable-model-invocation: true
---

# Iterar el prompt

Ciclo: ejecutar, evaluar, identificar, modificar, repetir.

1. Mira el prompt sin gastar, con el servidor en `development`: `POST /api/v1/estimate/preview`.
2. Diagnóstico:
   - Formato inadecuado: edita `app/services/prompt_builder.py`.
   - Precios descalibrados: revisa `RATE_CARD` y los ejemplos en `app/context/examples.py`.
   - Desglose genérico: sube el detalle de los ejemplos o usa `CONTEXT_DETAIL_LEVEL=full`.
   - Información irrelevante: refuerza `<restricciones>` o quita campos en el preprocesamiento.
3. Añade una variante en `experiments/variants.toml` que cambie una sola cosa.
4. `uv run python scripts/run_experiment.py --name <variante>` y compara el informe de `experiments/results/` con `baseline`.
5. Revisa en Langfuse la traza `generate-estimation` si hay claves: JSON válido a la primera, warnings, tokens, caché y coste.
6. Anota el `prompt_version` nuevo en `CHANGELOG.md`.
