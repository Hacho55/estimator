# 0004 · Propiedades, pirámide de evals y juez calibrado

**Estado:** aceptada (sesión 5, fase 4).

## Contexto

Con un LLM, `assert horas == 16` da falsos negativos masivos o, relajado, falsos positivos
silenciosos. Hace falta medir calidad sin iterar a ciegas ni revisar cien casos a mano.

## Decisión

- **Propiedades como funciones puras** (`app/evaluation/properties.py`): una definición para los
  tests del código, la suite de evals y las puntuaciones online de Langfuse.
- **Dos carpetas.** `tests/` (modelo simulado, cada push) y `evals/` (modelo real, golden dataset).
- **Pirámide.** Hard (propiedades, una llamada por caso y tier), soft (consistencia, CV < 0,25)
  y juez (`GEval` de DeepEval por `llm_client`). Lo que se comprueba con código no va al juez.
- **Golden dataset con criterios de éxito**, borrador del asistente validado por una persona
  (D11). En casos sin revisar, los umbrales numéricos avisan pero no bloquean.
- **Juez calibrado** contra veredictos con etiqueta humana antes de fijar su umbral. La primera
  calibración (umbral 0,4) es **provisional**: los veredictos los propuso el asistente y el juez
  es de la misma familia que el generador (ver `docs/evals.md`). Pasa a definitiva cuando una
  persona revise `evals/judge_calibration.yaml` y se repita con un juez de otra familia.
- **CI** (D12): `evals-hard` en PR a `main` y a mano; soft y juez solo a mano.
- **Langfuse como registro** (D10): dataset, ejecuciones y puntuaciones; sus evaluadores
  gestionados quedan para la sesión 15.

## Alternativas descartadas

- **Suite solo de juez:** cara, lenta y con un único punto de fallo (pirámide invertida).
- **Juez propio sin DeepEval:** más código que mantener sin ganancia para este alcance.

## Consecuencias

Las evals cuestan dinero (≈0,06 USD por caso y tier en la primera medición) y minutos (≈100 s
por estimación): por eso no corren en cada push.

## Revisar cuando

Cada trimestre (dataset) y al cambiar de modelo generador o juez (calibración).
