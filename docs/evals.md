# Evaluación del estimador

Con un LLM no se testea igualdad («16 horas»), se testean **propiedades**. Este documento
explica qué se comprueba en cada capa, cómo se ejecuta y cómo se mantiene el golden dataset.

## Dos carpetas, dos propósitos

| | `tests/` | `evals/` |
|---|---|---|
| Qué prueba | El código, con el modelo simulado | El sistema, con el modelo real |
| Cuándo | En cada push (CI) | A mano, y `evals-hard` en PR a `main` |
| Coste | Ninguno | Dinero y minutos (se imprime al terminar) |
| Fallo significa | Un bug del código | Una regresión de calidad (prompt, modelo, lógica) |

## La pirámide aplicada

| Familia | Dónde | Qué comprueba | Llamadas |
|---|---|---|---|
| **Hard** | `evals/test_hard.py` (`-m eval`) | Schema del tier, textos obligatorios, rangos ordenados y plausibles, componentes, hitos, tres riesgos, memoria respetada, horas en el rango esperado (±50 %), componentes y riesgos esperados, nada de lo inyectado en la salida, memoria de sesión | 1 por caso y tier |
| **Soft** | `evals/test_soft.py` (`-m "eval and slow"`) | Consistencia: 3 ejecuciones del mismo caso, CV del total < 0,25 y la misma decisión de estimable | 3 por caso y tier |
| **Juez** | `evals/test_judge.py` (`-m judge`) | `GEval` de DeepEval: coherencia de alcance y adecuación al tier | 1 + juez |

Las propiedades son funciones puras en `app/evaluation/properties.py`. Las mismas sirven a los
tests (con salidas fijas), a la suite de evals y a las puntuaciones online `prop_<nombre>` de
Langfuse. Lo que se puede comprobar con código no se le pide al juez.

## Ejecutar

```bash
uv run pytest evals -m eval -k test_properties_hold
```

```bash
uv run pytest evals -m "eval and slow and not judge"
```

```bash
EVAL_JUDGE_MODEL=openrouter/openai/gpt-4.1 uv run pytest evals -m judge
```

Sin `LITELLM_API_KEY` todo se salta. La suite apaga las cachés (una respuesta guardada no
mide nada), usa el canal real del tier (JWT) y escribe un informe por ejecución en
`experiments/results/evals-<fecha>.json`: caso, tier, segundos, coste, decisión de la revisión,
llamadas al actor, herramientas y avisos.

## Golden dataset

`evals/golden_dataset.yaml`, 12 casos: simple, medio, grande con dependencias, ambiguo,
contradicciones, inglés, fuera de alcance, inyección en un adjunto y dos sesiones (un hecho
que sobrevive a la ventana; una tecnología retirada que acaba en `rejected_options`). Cada caso
lleva sus criterios de éxito y una nota que explica por qué existe y de dónde sale el rango.

- **Estado**: los casos con `reviewed: false` son un borrador (D11). Sus rangos salen de la
  tabla de tarifas y del histórico; hay que validarlos antes de usarlos como umbral.
- **Revisión**: cada trimestre, o cuando aparezca en producción un tipo de entrada que el
  dataset no cubre. Un caso nuevo se añade con la skill `add-golden-case`.
- **Langfuse**: `scripts/sync_golden_dataset.py` copia el YAML a un dataset de Langfuse. El YAML
  manda; Langfuse guarda las ejecuciones para compararlas.

## Calibración del juez

El juez también se equivoca y su umbral no es universal. `evals/judge_calibration.yaml` tiene 8
salidas con veredicto humano, la mitad estropeadas a propósito (alcance inventado, piezas
olvidadas, jerga para dirección, nada que planificar para un PM).

```bash
uv run python scripts/calibrate_judge.py
```

El script muestra la nota de cada salida y el acuerdo con la persona para varios umbrales.

| Fecha | Juez | Umbral elegido | Acuerdo |
|---|---|---|---|
| 2026-11-16 | `openrouter/anthropic/claude-sonnet-5.5` | 0,4 | 8/8 (0,3 y 0,4); 7/8 con 0,5 |

Notas de la primera calibración: el juez separa bien lo estropeado (0,0–0,2) de lo bueno
(0,4–0,8), pero puntúa bajo la salida ejecutiva buena (0,4) y la de PM buena (0,5); con 0,5
habría suspendido la ejecutiva. Se toma 0,4 como `EVAL_JUDGE_THRESHOLD` por defecto.
Pendiente: repetir con un juez de otra familia que el generador (`EVAL_JUDGE_MODEL`); este es
de la misma, y la fase 4 advierte que tiende a favorecer lo suyo. Los veredictos humanos de
`judge_calibration.yaml` los propuso el asistente: conviene revisarlos.

## ¿Compensa la revisión Actor-Critic-Boss?

`experiments/variants.toml` tiene las variantes `tier-*` con la revisión forzada a `acb` o
`none`. Se comparan propiedades que pasan, coste, latencia y decisión de la revisión:

```bash
uv run python scripts/run_experiment.py --variant tier- --name tiers --langfuse
```

Con esos datos se confirma o se corrige la activación por tier de `app/tiers.py` (D14).

## Primeras mediciones (2026-11-16)

Dos ejecuciones de humo con el modelo real (`landing-hubspot`):

| Tier | Resultado | Segundos | Coste | Tokens (entrada / salida) |
|---|---|---|---|---|
| developer | propiedades OK | 105 | 0,054 USD | 8.043 / 4.898 |
| pm (con revisión) | propiedades OK | 107 | 0,069 USD | 13.277 / 5.341 |

Y de punta a punta en la app (front, backend de negocio y estimador con `AUTH_MODE=jwt`), sesión
«Atlas» (portal de reservas):

| Turno | Tier | Herramientas | Revisión | Segundos | Tokens (entrada / salida) |
|---|---|---|---|---|---|
| 1, estimación | pm | `find_similar_projects` | aceptada, 1 intento | 66 | 17.477 / 7.329 |
| 2, «quita los avisos por correo» | executive | — | aceptada, 1 intento | 97 | 16.110 / 4.517 |

El cambio de perfil entre turnos queda en `memory_changes`, la memoria actualiza el alcance en
el turno 2 y la vista ejecutiva trae rangos calculados por el código (22.500–34.500 EUR).

La latencia está muy por encima del objetivo del plan (< 12 s developer, < 25 s pm): el modelo
genera unos 5.000 tokens de salida para una landing. Antes de ajustar la activación de la
revisión conviene revisar `LLM_MAX_TOKENS`, la profundidad por defecto y el modelo del actor.

## Stress test del CAG (pre-sesión 6)

Mide dónde empieza a romperse el CAG antes de comparar con RAG: latencia frente a tokens,
coste acumulado por turno y deriva de la memoria a lo largo de 20 turnos, más adjuntos de
5–100 KB y una ráfaga concurrente. No optimiza nada: el baseline es el sistema tal cual.

```bash
uv run python -m evals.stress.run --http http://localhost:8000 --output evals/stress/results.csv
```

```bash
uv run python -m evals.stress.report evals/stress/results.csv
```

El runner necesita el estimador en `AUTH_MODE=jwt` y firma con `STRESS_TOKEN_SECRET` (el
`AI_SERVICE_SECRET` del servidor); sin `--http` levanta la app en proceso. Cada turno trae su
`observation` (`turn_observed`, ver `docs/specs/observacion.md`). Resultados y lectura en
[`evals/stress/REPORT.md`](../evals/stress/REPORT.md).

**Segunda corrida (contexto al límite).** Una variante de topes por proceso, siempre en proceso:

```bash
uv run python -m evals.stress.run --variant A16 --set LLM_MAX_TOKENS=16000 --scenarios pivot --repeats 1 --output evals/stress/results_context_a.csv
```

```bash
uv run python -m evals.stress.context_run --block B --variant B1 --sizes 50,100,500,1000
```

```bash
uv run python -m evals.stress.context_run --block C --dry-run
```

```bash
uv run python -m evals.stress.context_report
```
