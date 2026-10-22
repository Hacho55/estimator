---
name: add-estimation-example
description: Añade o edita una estimación histórica en el contexto CAG del estimador. Úsala al incorporar un presupuesto de referencia, cambiar tarifas de un ejemplo o revisar si el bloque de contexto sigue cabiendo.
disable-model-invocation: true
---

# Añadir un ejemplo

Edita `ESTIMATION_EXAMPLES` en `app/context/examples.py`. Los datos van en bruto: sin costes ni totales calculados a mano.

- No repitas un sector que ya esté cubierto si puedes aportar otro (web, móvil, API, interno, e-commerce).
- `year` dentro de `CONTEXT_MAX_AGE_YEARS`. Una estimación vieja sirve solo como caso que el filtro debe excluir.
- Puedes dejar ruido (nombre de cliente, email, días, coma decimal, rol sin normalizar). El preprocesamiento lo quita. No hace falta limpiarlo en el dato.
- Entre 5 y 7 ejemplos vigentes. Pasarse es la señal para migrar a RAG, no para seguir metiendo texto.
- `uv run pytest tests/test_preprocessing.py` y `uv run python scripts/run_experiment.py --dry-run`.
- Si el nuevo ejemplo no cabe en `CONTEXT_MAX_TOKENS`, el informe lo marca como excluido por presupuesto. Acórtalo o baja el detalle antes de darlo por bueno.
