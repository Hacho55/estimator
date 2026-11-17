# Stress test del CAG · informe

Ejercicio previo a la sesión 6: **medir dónde se rompe el CAG antes de añadir RAG**. No se
optimiza nada. Prompts, ventana, límites y modelos son los de la sesión 5, y el resultado es el
baseline contra el que se comparará RAG. Plan y decisiones: [`docs/plan-pre-session-6.md`](../../docs/plan-pre-session-6.md).

## Parámetros de la corrida

| Parámetro | Valor |
|---|---|
| Endpoint | `POST /api/v2/sessions/{id}/estimate` por HTTP, servidor local con un worker |
| Tier | `developer` (JWT del harness): actor con herramientas de investigación, sin revisión Actor-Critic-Boss |
| Modelo del actor | `claude-sonnet-5.5` vía LiteLLM, `LLM_MAX_TOKENS=8000` |
| Ventana | `SESSION_MAX_TURNS=6`, sin anclas ni summary; los hechos viven en la memoria (ADR 0002) |
| Adjuntos | `ATTACHMENT_MAX_CHARS=40.000` (el enunciado supone 60.000); el extractor de memoria ve 12.000 |
| Presupuestos | latencia 25 s y coste 0,05 USD por turno (`LatencyBudgetMetric`, `CostBudgetMetric`) |
| Matriz | 3 escenarios × 20 turnos × 2 repeticiones; adjuntos 0/5/20/50/100 KB × 3 repeticiones × 2 turnos; ráfaga de 10 sesiones a la vez. 160 filas |
| Gasto | 5,97 USD con precio conocido: 5,90 en `results.csv` y 0,07 en la fila descartada de la primera corrida, que paró en la fila 32. Los 60 turnos truncados no traen precio: con los precios por token que salen de una regresión sobre los turnos con 200 (≈ 2,2 USD/M de entrada, ≈ 9,8 USD/M de salida), cada uno costó del orden de 0,12 USD, unos **7 USD más sin contabilizar** |

El CSV está en [`results.csv`](results.csv), una fila por turno. La fila descartada de la
primera corrida, que incluye el primer 502, está en `results.discarded.csv`. Las tablas salen
de `uv run python -m evals.stress.report evals/stress/results.csv`.

### Cómo se traduce el enunciado

- `anchors_count` es el **número de hechos en la memoria**, porque `/sessions` no tiene anclas.
- `summary_chars` son los **caracteres de la memoria renderizada** en el prompt, porque no hay summary.
- `turn_index` es el turno del escenario.
  - `session_turn` es el del servidor, que no cuenta los turnos que fallaron.
  - En la primera versión del runner estaban mezclados. Se corrigió el CSV por posición dentro de la sesión, porque cada turno escribe una fila, falle o no.

### Por qué así (D17, D18, D20)

- **D17 · La observación viaja en la respuesta.** El campo `observation` es aditivo en `SessionEstimateResponse`, y además se emite el evento `turn_observed` en structlog.
  - El runner lee el JSON que ya recibe, sin cruzar logs por timestamp.
  - El router es el único sitio que ve los dos gastos del turno: el actor y el extractor de memoria.
- **D18 · Las métricas viven en `evals/stress/metrics.py`, no en `app/evaluation/properties.py`.**
  - Operan sobre la observación y el snapshot de la sesión, no sobre la vista del tier.
  - `properties.py` es código de producción (puntuaciones online); esto es instrumentación del ejercicio.
  - Son deterministas: coincidencia exacta sin juez ni embeddings.
- **D20 · Matriz por bloque, no la literal.**
  - Con este modelo, la literal (900 turnos) costaba unos 60 USD y unas 25 h.
  - La matriz cruza cada eje solo donde aporta: deriva con 20 turnos, tamaño del adjunto en 2 turnos, concurrencia en el primer turno.

## Lectura

**El CAG se rompe primero por la salida, no por el contexto, y lo hace con un fallo duro.**

- **El fallo duro.** 59 de los 160 turnos (37 %) devolvieron 502 `llm_output_truncated`: el actor llegó a los 8.000 tokens de salida. La tasa fue del 68 % en `growing`, el 50 % en `pivot` y el 28 % en `contradiction`. No se concentra al final de la sesión: 26 de los 58 fallos de los escenarios largos caen en los turnos 1–10, y `growing` falla ya en el turno 3.
- **Latencia: la dicta la salida, no la entrada.**
  - Entre los 92 turnos que respondieron fuera de la ráfaga, se correlaciona con `tokens_out` (r = 0,55, unos 8 ms por token) y apenas con `tokens_in` (r = 0,14).
  - La curva 1 no crece con `tokens_in`: la mediana es 16 s en 10–15 k tokens y 20 s en 20–30 k.
  - `tokens_in` sube de 11 k en el turno 1 a 24 k en el turno de sesión 7 y ahí se estanca, porque la ventana de 6 turnos lo acota.
  - La mitad de los turnos supera el SLA de 25 s.
- **Coste: también lo dicta la salida.**
  - Se correlaciona con `tokens_out` (r = 0,87) más que con `tokens_in` (r = 0,42).
  - Media de 0,057 USD por turno; el 49 % pasa del presupuesto de 0,05 USD.
  - La curva 2 no muestra crecimiento: el turno 20 cuesta 1,1–1,4 veces el turno 1. Su acumulado es una cota inferior (marcado con `?`), porque los turnos truncados se pagaron sin precio.
  - Ni la caché exacta ni la semántica acertaron nunca, porque con herramientas de investigación el tier `developer` no cachea. Sí se reutilizó el prefijo en 79 de esos 92 turnos.
- **Ráfaga: 10 sesiones a la vez no saturan.** La P50 pasó de 44 s en serie a 47 s, y 1 de los 10 turnos se truncó.

**La memoria no olvida; pierde lo que nunca le llegó.**

- **Memoria sin olvido silencioso.** En los tres escenarios, todos los hechos cuyo turno respondió con 200 seguían en la memoria al final.
  - El recall es del 100 % sobre esos hechos, hasta el turno 20, con la ventana de 6 turnos.
  - El nombre del proyecto se mantuvo en el 100 % de los turnos.
  - La memoria pesa como mucho 7 hechos y 886 caracteres.
- **La deriva aparente es otra cosa.** La curva 3 cae al 28 % en `growing`, al 27 % en `pivot` y al 54 % en `contradiction`. La tabla "Causa de la deriva" muestra de dónde sale: las 257 comprobaciones fallidas son todas de hechos declarados en turnos que fallaron.
  - Un turno que devuelve 502 no llega al extractor, así que sus hechos no entran en la memoria y la sesión sigue como si no se hubieran dicho.
  - Así **el fallo duro se convierte en degradación silenciosa**: el cliente vio un error en el turno 5 de `pivot`, pero en los 15 turnos siguientes el estimador sigue estimando con React, que el usuario había descartado.
  - La contradicción explícita, en cambio, se resolvió bien. En `contradiction` el turno 8 (el presupuesto pasa de 30.000 a 80.000) respondió en las dos repeticiones. La memoria sustituyó la restricción y retiró la vieja en todos los turnos siguientes.
  - Lo que perdió ese escenario son requisitos de turnos que fallaron, como la nómina del turno 6.
- **Los adjuntos: el caso límite que justifica RAG.** Ahí sí hay pérdida silenciosa de contenido.
  - Por encima de 40.000 caracteres el pliego se trunca: el 100 % de los de 50 y 100 KB, en 39.829 caracteres.
  - El recall de los marcadores cae del 100 % (5 y 20 KB) al 67 % (50 KB) y al 33 % (100 KB). Desaparecen exactamente los que quedan más allá del corte: el del 50 % y el del 90 % del documento.
  - El cliente recibe el aviso `attachment_truncated`, pero el modelo estima sin esa parte y no lo dice en la respuesta.
  - El turno 2 recuerda lo mismo que el turno 1: lo que entró en la memoria se mantiene y lo que se cortó no vuelve.
  - **Un pliego de más de 40 KB es el caso que el CAG no puede resolver metiendo todo**: ahí entra la recuperación por fragmentos de la sesión 6.

**Qué no se tocó y por qué.** Subir `LLM_MAX_TOKENS`, acortar la salida del tier `developer`, o
reintentar o recuperar el turno truncado haría desaparecer casi toda la deriva. Pero eso es
optimizar, y el ejercicio mide. Queda como primera candidata para la sesión 6, junto a las
anclas de `/api/v2/conversations` como variante a comparar (ver el plan, §4). Comparar RAG
contra este baseline exige separar las dos causas: RAG puede arreglar los adjuntos, pero no el
truncado de salida.

## Tablas

`?`: algún turno sin precio conocido en ese agregado.

Filas: 160 (59 con error). Coste total: 5.90 USD conocidos + 59 turnos sin precio.

### Resumen por escenario y tamaño

| Escenario | KB | n | P50 s | P95 s | P95 s sin caché | Coste USD | Caché exacta | Caché semántica | Recall memoria | Recall pliego | En SLA |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| attachments | 0 | 6 | 13.7 | 46.9 | 46.9 | 0.24 | 0 % | 0 % | — | — | 50 % |
| attachments | 5 | 6 | 11.5 | 54.9 | 54.9 | 0.38 | 0 % | 0 % | — | 100 % | 50 % |
| attachments | 20 | 6 | 17.7 | 111.6 | 111.6 | 0.44 | 0 % | 0 % | — | 100 % | 50 % |
| attachments | 50 | 6 | 12.0 | 59.1 | 59.1 | 0.46 | 0 % | 0 % | — | 67 % | 50 % |
| attachments | 100 | 6 | 10.7 | 45.2 | 45.2 | 0.43 | 0 % | 0 % | — | 33 % | 50 % |
| burst | 0 | 9 (+1 err) | 47.4 | 54.7 | 54.7 | 0.69 | 0 % | 0 % | 100 % | — | 0 % |
| contradiction | 0 | 29 (+11 err) | 24.4 | 103.1 | 103.1 | 1.63 | 0 % | 0 % | 76 % | — | 52 % |
| growing | 0 | 13 (+27 err) | 45.2 | 49.7 | 49.7 | 0.71 | 0 % | 0 % | 63 % | — | 38 % |
| pivot | 0 | 20 (+20 err) | 23.9 | 47.1 | 47.1 | 0.94 | 0 % | 0 % | 54 % | — | 55 % |

### Curva 1 · latencia frente a `tokens_in` (sin aciertos de caché ni ráfaga)

| `tokens_in` | n | P50 s | P95 s | Mediana `tokens_out` | Mediana estimación s | Mediana extractor s |
|---|---:|---:|---:|---:|---:|---:|
| 5,000–9,999 | 3 | 24.4 | 41.0 | 3,125 | 22.1 | 2.3 |
| 10,000–14,999 | 33 | 16.4 | 49.7 | 1,869 | 14.8 | 1.7 |
| 15,000–19,999 | 23 | 27.8 | 58.8 | 2,929 | 25.0 | 2.1 |
| 20,000–29,999 | 26 | 19.7 | 103.1 | 2,572 | 20.7 | 1.7 |
| 30,000–49,999 | 7 | 45.2 | 59.1 | 5,712 | 40.7 | 4.7 |

### Curva 2 · coste acumulado frente al turno (sin adjunto, media de repeticiones)

| Turno | growing acum. USD | growing vs turno 1 | pivot acum. USD | pivot vs turno 1 | contradiction acum. USD | contradiction vs turno 1 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 0.072 | 1.0× | 0.076 | 1.0× | 0.065 | 1.0× |
| 2 | 0.163 | 1.3× | 0.170 | 1.2× | 0.133 | 1.0× |
| 3 | 0.163? | — | 0.170? | — | 0.170 | 0.6× |
| 4 | 0.249? | 1.2× | 0.197? | 0.4× | 0.269 | 1.5× |
| 5 | 0.334? | 1.2× | 0.197? | — | 0.343 | 1.1× |
| 6 | 0.334? | — | 0.197? | — | 0.343? | — |
| 7 | 0.334? | — | 0.231? | 0.4× | 0.378? | 0.5× |
| 8 | 0.334? | — | 0.231? | — | 0.428? | 0.8× |
| 9 | 0.365? | 0.4× | 0.231? | — | 0.428? | — |
| 10 | 0.365? | — | 0.270? | 0.5× | 0.452? | 0.4× |
| 11 | 0.365? | — | 0.270? | — | 0.452? | — |
| 12 | 0.365? | — | 0.279? | 0.1× | 0.536? | 1.3× |
| 13 | 0.376? | 0.1× | 0.279? | — | 0.584? | 0.7× |
| 14 | 0.376? | — | 0.279? | — | 0.584? | — |
| 15 | 0.376? | — | 0.317? | 0.5× | 0.611? | 0.4× |
| 16 | 0.376? | — | 0.317? | — | 0.649? | 0.6× |
| 17 | 0.376? | — | 0.317? | — | 0.689? | 0.6× |
| 18 | 0.376? | — | 0.332? | 0.2× | 0.754? | 1.0× |
| 19 | 0.398? | 0.3× | 0.386? | 0.7× | 0.800? | 0.7× |
| 20 | 0.483? | 1.2× | 0.468? | 1.1× | 0.888? | 1.4× |

### Curva 3 · deriva de memoria frente a N

| Turno N | growing recall | growing nombre | growing hechos | pivot recall | pivot nombre | pivot hechos | contradiction recall | contradiction nombre | contradiction hechos |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100 % | 100 % | 6 | 100 % | 100 % | 4 | 100 % | 100 % | 2 |
| 2 | 100 % | 100 % | 7 | 100 % | 100 % | 4 | 100 % | 100 % | 3 |
| 3 | — | — | — | — | — | — | 100 % | 100 % | 4 |
| 4 | 83 % | 100 % | 7 | 100 % | 100 % | 4 | 100 % | 100 % | 4 |
| 5 | 71 % | 100 % | 7 | — | — | — | 100 % | 100 % | 4 |
| 7 | — | — | — | 50 % | 100 % | 4 | 80 % | 100 % | 4 |
| 8 | — | — | — | — | — | — | 83 % | 100 % | 4 |
| 9 | 50 % | 100 % | 7 | — | — | — | — | — | — |
| 10 | — | — | — | 40 % | 100 % | 4 | 71 % | 100 % | 4 |
| 12 | — | — | — | 36 % | 100 % | 4 | 67 % | 100 % | 4 |
| 13 | 38 % | 100 % | 7 | — | — | — | 61 % | 100 % | 4 |
| 15 | — | — | — | 31 % | 100 % | 4 | 55 % | 100 % | 4 |
| 16 | — | — | — | — | — | — | 55 % | 100 % | 4 |
| 17 | — | — | — | — | — | — | 58 % | 100 % | 5 |
| 18 | — | — | — | 27 % | 100 % | 4 | 54 % | 100 % | 5 |
| 19 | 28 % | 100 % | 7 | 27 % | 100 % | 4 | 54 % | 100 % | 5 |
| 20 | 28 % | 100 % | 7 | 27 % | 100 % | 4 | 54 % | 100 % | 5 |

### Fallos duros

| Escenario | Turnos | 5xx | Tasa | Turnos 1–10 / 11–20 | Error |
|---|---:|---:|---:|---:|---:|
| growing | 40 | 27 | 68 % | 12 / 15 | llm_output_truncated |
| pivot | 40 | 20 | 50 % | 10 / 10 | llm_output_truncated |
| contradiction | 40 | 11 | 28 % | 4 / 7 | llm_output_truncated |

### Causa de la deriva

| Escenario | Comprobaciones | Recall | Perdidos: su turno falló | Perdidos: su turno dio 200 | Recall sin los turnos fallidos |
|---|---:|---:|---:|---:|---:|
| growing | 127 | 46 % | 68 | 0 | 100 % |
| pivot | 198 | 40 % | 118 | 0 | 100 % |
| contradiction | 208 | 66 % | 71 | 0 | 100 % |

### Adjuntos

| KB | Caracteres extraídos | Truncado | `tokens_in` turno 1 | P50 s | USD turno 1 | Recall turno 1 | Recall turno 2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 0 % | 14,613 | 44.1 | 0.058 | — | — |
| 5 | 5,382 | 0 % | 19,170 | 54.9 | 0.100 | 100 % | 100 % |
| 20 | 20,346 | 0 % | 29,312 | 56.5 | 0.120 | 100 % | 100 % |
| 50 | 39,829 | 100 % | 36,844 | 50.5 | 0.127 | 67 % | 67 % |
| 100 | 39,829 | 100 % | 36,425 | 41.7 | 0.116 | 33 % | 33 % |

### Ráfaga

| Carga | n | P50 s | P95 s | P95 s de reloj | Aciertos de caché |
|---|---:|---:|---:|---:|---:|
| Primer turno, en serie | 9 | 44.1 | 47.1 | 47.2 | 0 % |
| Ráfaga (10 a la vez, 1 fallidos) | 9 | 47.4 | 54.7 | 54.7 | 0 % |
