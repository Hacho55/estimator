---
name: add-golden-case
description: Añade un caso al golden dataset del estimador (evals/golden_dataset.yaml) con sus criterios de éxito. Úsala cuando aparezca un tipo de entrada que el dataset no cubre o en la revisión trimestral.
---

# Añadir un caso al golden dataset

1. Elige la categoría que falta: simple, medium, large_dependencies, ambiguous,
   edge_contradictions, multilingual, out_of_scope, adversarial o session_memory. Representa
   entradas reales, no casos exóticos que nadie envía.
2. Escribe el caso en `evals/golden_dataset.yaml` con un `id` único y una sola fuente de entrada:
   `input`, `input_file` o `turns` (sesión). Los adjuntos van en `attachments` como texto.
3. Anota `expected` con lo que hace bueno a una respuesta, no con la respuesta concreta:
   `estimable`, `hours_range`, `components_any_of`, `risks_any_of`, `must_not_contain` o
   `metadata`. Tolerancias generosas al principio: la suite ya aplica ±50 % al rango.
4. Explica en `notes` por qué existe el caso y de dónde sale el rango (tabla de tarifas, un
   proyecto del histórico). Deja `reviewed: false` hasta que lo valide un experto.
5. Comprueba que carga y ejecútalo solo:

   ```bash
   uv run python -c "from evals.dataset import load_dataset; print(len(load_dataset()))"
   ```

   ```bash
   uv run pytest evals -m eval -k <id>
   ```

6. Si el caso falla, decide si es el sistema o el caso. No ajustes el rango a la salida del
   modelo (antipatrón 1 de la fase 4): el rango refleja lo que estimaría una persona.
7. Sincroniza con Langfuse si se usa (`scripts/sync_golden_dataset.py`) y anota el cambio en
   `CHANGELOG.md`.
