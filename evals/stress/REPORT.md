# Stress test del CAG · informe

Ejercicio previo a la sesión 6: **medir dónde se rompe el CAG antes de añadir RAG**. No se
optimiza nada. Prompts, ventana, límites y modelos son los de la sesión 5, y el resultado es el
baseline contra el que se comparará RAG. Plan y decisiones: [`docs/plan-pre-session-6.md`](../../docs/plan-pre-session-6.md).

Hay **dos corridas**. La primera rompió el sistema, pero por la longitud de la salida, que RAG
no arregla. La segunda ([más abajo](#segunda-corrida-el-contexto-al-límite)) fuerza lo que sí
justificaría RAG: un contexto de entrada de hasta 230 k tokens y un corpus de referencia de
hasta 400 estimaciones. **La conclusión que vale para la sesión 6 es la de la segunda corrida.**

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

---

## Segunda corrida: el contexto al límite

Plan: [`docs/plan-pre-session-6.md`](../../docs/plan-pre-session-6.md), §6. Datos:
[`results_context_a.csv`](results_context_a.csv) (A, por sesiones) y
[`results_context.csv`](results_context.csv) (B y C). Tablas:
`uv run python -m evals.stress.context_report`.

### Parámetros

| Bloque | Qué se forzó | Variante (solo en el runner, en proceso) |
|---|---|---|
| A | `pivot` con 8 k y con 16 k tokens de salida | `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS=300`; herramientas como en la primera corrida |
| B1 | Pliego de 50 a 1.000 KB con 9 requisitos del 10 al 90 % | Tope de adjuntos subido a 1.000.000 caracteres; guarda de ventana tal cual (128 k) |
| B2 | Los tamaños que B1 rechaza | Además, `MODEL_CONTEXT_WINDOW=1.000.000` para llegar al proveedor |
| C | Corpus de 5 a 400 estimaciones con una aguja al principio, en medio o al final | Sin herramientas; modo **tal cual** (7 ejemplos, 6.000 tokens) y modo **sin topes** |

- Todas las variantes llevan `LLM_MAX_TOKENS=16000`, sin reintentos y con la caché semántica apagada.
- La caché de respuestas se borra antes de cada llamada.
- **Gasto:** 21,59 USD con precio conocido, más 15 turnos truncados de A sin precio, unos 2–3 USD más estimados.
  - El plan preveía 17–20 USD. La diferencia es haber ampliado B hasta 1.000 KB, después de ver que 300 KB solo eran 70 k tokens.
  - El tope de 30 USD se respetó.

**Umbrales de viabilidad.** Se fijaron antes de correr, y los de latencia (25 s) y coste
(0,05 USD) **ya los incumple la línea base**: el control de C tarda unos 45 s y cuesta unos
0,07 USD. La columna "¿Viable?" queda dominada por eso. Lo que de verdad discrimina es el
**coste relativo** a la línea base y la **selección** del ejemplo pertinente, y la lectura se
apoya en esas dos cosas.

### Lectura

**1. El contexto de entrada no rompe la calidad, ni siquiera a 230 k tokens.**
- **Recall:** en B los 9 requisitos se citaron en las 30 respuestas, en todas las posiciones y en todos los tamaños hasta 1.000 KB (230 k tokens). No aparece "perdido en el medio".
- **Latencia:** plana, de 38 a 48 s, porque la sigue dictando la salida.
- **Coste:** sube en línea recta, de 0,107 USD con 14 k tokens a 0,838 USD con 230 k. El coste del turno (actor y extractor) crece unos 3,4 USD por cada millón de tokens de entrada del actor, contados con la estimación del presupuesto.
- **Truncado residual a 1.000 KB:** el pliego se cortó en 995.846 caracteres, detrás del requisito del 90 %. Los 9 requisitos seguían visibles.
- **El primer fallo duro es nuestra propia guarda.** Con 500 KB (116 k tokens) el estimador responde 413 `context_budget_exceeded` antes de llamar al modelo y sin coste. El proveedor aceptó los 230 k tokens.
- **Límite del método:** el relleno del pliego sintético es repetitivo y los requisitos destacan ("REQUISITO OBLIGATORIO"). Es más fácil que un pliego real con requisitos parecidos entre sí, así que esto es una cota optimista de la recuperación, no una prueba de que no se pierda nada.

**2. El CAG tal cual pierde el ejemplo pertinente en silencio en cuanto el corpus pasa de 7.**
- **Por qué:** `build_context_block` se queda con los 7 primeros **por orden de la lista**; la transcripción no elige ejemplos.
- **Qué pasa con la aguja:** con 50 y con 200 estimaciones, la aguja solo entra si es la primera. En medio o al final queda fuera, no se cita, y la estimación se queda en 535–860 horas.
  - Con la aguja dentro salen 1.450–1.960 horas.
  - El control sin aguja da 745–955 horas, y la aguja lleva 1.550.
- **No hay error ni aviso.** El dry-run lo confirma sin coste: la aguja queda fuera para cualquier N ≥ 25 salvo en la primera posición.
- **Este es el caso que justifica RAG:** con más de 7 proyectos históricos, que el pertinente llegue al prompt depende del orden de la lista, no de su relevancia.

**3. Sin topes, el modelo encuentra la aguja, pero el coste crece con el corpus.**
- **Recuperación:** con el corpus entero en el prompt, la aguja se citó en 37 de 38 llamadas hasta 400 estimaciones (108 k tokens). El único fallo, en N = 25, no se repitió en dos repeticiones más.
- **Influencia:** la aguja mueve la estimación entre un +69 % y un +120 % frente al control emparejado.
- **Coste:** en frío se multiplica por 10,3 entre N = 5 (0,053 USD) y N = 400 (0,544 USD).
- **Latencia:** la mediana en frío va de 27 a 52 s, sin una tendencia clara a partir de N = 100.
- **Caché del prefijo:** es la defensa del CAG. Con acierto, la llamada a N = 400 bajó a 0,084 USD (6,5 veces menos). Pero el proveedor solo sirvió la caché en 7 de las 18 llamadas calientes, y la mediana caliente a N = 400 fue 0,540 USD, casi la de frío.
- **Contrafactual de RAG.** Un RAG que recupere 5 ejemplos llevaría un contexto parecido al de N = 5, unos 0,05 USD por llamada sea cual sea el corpus.
  - Es una **estimación con los precios medidos, no una medición**.
  - No incluye el coste de la recuperación ni su recall, que podría fallar donde aquí el modelo no falló.

**4. La salida sigue rompiendo, y RAG no lo arregla.**
- **A:** con 16 k tokens de salida, los turnos truncados bajan de 9 a 6 de 20, pero la salida del turno (actor y extractor) crece hasta 16,5 k tokens. El modelo escribe más cuando puede: es un problema de diseño de la salida del tier `developer`, no del contexto.
- **Memoria:**
  - Con 8 k, la memoria conservó el 100 % de lo que le llegó, igual que en la primera corrida.
  - Con 16 k apareció el primer olvido silencioso real: el extractor no capturó "sin conexión" en el turno 9, un turno que respondió bien. Retención del 93 % sobre los turnos con 200.

**Dónde está el límite del CAG, en una frase.** No está en la capacidad: el modelo lee 230 k tokens y encuentra lo que busca. Está en **elegir qué entra y pagar por ello**.
- Con los topes de producción, el ejemplo pertinente queda fuera en silencio a partir de 8 estimaciones.
- Sin topes, cada turno cuesta proporcional al corpus: 10 veces más a 400 estimaciones, y la caché que lo abarata solo se aplicó en 7 de 18 llamadas.
- Las dos cosas son lo que RAG resuelve: seleccionar por relevancia con un presupuesto fijo.
- Lo que RAG **no** resuelve, y hay que medir aparte en la sesión 6, es la salida truncada.

**Diferencias con la primera corrida que hay que tener en cuenta.**
- La segunda corre en proceso y no contra el servidor. Allí Presidio no encontró el modelo en español y el guardrail usó el patrón de respaldo.
- No cambia nada de lo que se mide aquí, porque los marcadores y la aguja no llevan datos personales.

### Tablas de la segunda corrida

#### A · Atribución del truncado (`pivot`, 8 k frente a 16 k de salida)

| Variante | Turnos | Fallos | P50 s | Máx. `tokens_out` | USD conocidos | Recall medio | Recall en el turno 20 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A8 | 20 | 9 llm_output_truncated | 23.4 | 8,429 | 0.60 | 58 % | 27 % |
| A16 | 20 | 6 llm_output_truncated | 30.1 | 16,484 | 1.47 | 82 % | 53 % |

Causa de la deriva, A16:

| Escenario | Comprobaciones | Recall | Perdidos: su turno falló | Perdidos: su turno dio 200 | Recall sin los turnos fallidos |
|---|---:|---:|---:|---:|---:|
| pivot | 129 | 74 % | 26 | 7 | 93 % |

Causa de la deriva, A8:

| Escenario | Comprobaciones | Recall | Perdidos: su turno falló | Perdidos: su turno dio 200 | Recall sin los turnos fallidos |
|---|---:|---:|---:|---:|---:|
| pivot | 104 | 43 % | 59 | 0 | 100 % |

#### B · Contexto de entrada (pliegos de 50 a 1.000 KB)

| Variante | KB | Resultado | Tokens del actor | P50 s | USD (mediana) | Visibles | Recall respuesta | Recall memoria | ¿Viable? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | 50 | 3 200 | 14,195 | 40.9 | 0.107 | 9 / 9 | 100 % | 33 % | no: latencia, coste |
| B1 | 100 | 3 200 | 25,501 | 37.3 | 0.146 | 9 / 9 | 100 % | 33 % | no: latencia, coste |
| B1 | 150 | 3 200 | 36,810 | 40.9 | 0.187 | 9 / 9 | 100 % | — | no: latencia, coste |
| B1 | 200 | 3 200 | 48,111 | 41.1 | 0.227 | 9 / 9 | 100 % | — | no: latencia, coste |
| B1 | 250 | 3 200 | 59,421 | 44.5 | 0.254 | 9 / 9 | 100 % | — | no: latencia, coste |
| B1 | 300 | 3 200 | 70,725 | 38.4 | 0.304 | 9 / 9 | 100 % | — | no: latencia, coste |
| B1 | 400 | 3 200 | 93,341 | 38.9 | 0.378 | 9 / 9 | 100 % | — | no: latencia, coste |
| B1 | 500 | 3 local | 116,079 | — | — | 9 / 9 | — | — | no: fallo duro |
| B1 | 750 | 3 local | 173,450 | — | — | 9 / 9 | — | — | no: fallo duro |
| B1 | 1000 | 3 local | 230,149 | — | — | 9 / 9 | — | — | no: fallo duro |
| B2 | 500 | 3 200 | 116,079 | 38.9 | 0.448 | 9 / 9 | 100 % | — | no: latencia, coste |
| B2 | 750 | 3 200 | 173,450 | 40.6 | 0.642 | 9 / 9 | 100 % | — | no: latencia, coste |
| B2 | 1000 | 3 200 | 230,149 | 48.5 | 0.838 | 9 / 9 | 100 % | — | no: latencia, coste |

Requisitos citados por posición en el pliego (citados / visibles):

| Variante | KB | 10 % | 20 % | 30 % | 40 % | 50 % | 60 % | 70 % | 80 % | 90 % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | 50 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 100 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 150 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 200 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 250 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 300 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B1 | 400 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B2 | 500 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B2 | 750 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| B2 | 1000 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

#### C · Corpus del CAG (aguja entre N estimaciones)

Control sin aguja (N = 5, 3 repeticiones): 775.0, 955.0, 745.0 horas. Aguja: 1550 horas.

Modo **tal cual** (topes por defecto, 7 ejemplos por orden de la lista):

| N | Aguja | ¿Entra? | ¿Citada? | Horas | Tokens de contexto |
|---|---:|---:|---:|---:|---:|
| 50 | end | no | no | 725.0 | 1,817 |
| 50 | middle | no | no | 535.0 | 1,816 |
| 50 | start | sí | sí | 1450.0 | 1,879 |
| 200 | end | no | no | 695.0 | 1,817 |
| 200 | middle | no | no | 860.0 | 1,815 |
| 200 | start | sí | sí | 1960.0 | 1,878 |

Modo **sin topes** (todo el corpus en el prompt):

| N | Tokens de contexto | Tokens del actor | P50 s frío | P50 s caliente | USD frío | Frío vs N mínimo | USD caliente | Aciertos de caché | USD con acierto | Aguja citada | Horas vs control | Fallos | ¿Viable? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 1,326 | 2,524 | 27.3 | 36.9 | 0.053 | 1.0× | 0.067 | 1/3 | 0.034 | 6/6 | 98 % | 0 | no: latencia, coste |
| 25 | 6,741 | 7,939 | 40.1 | 42.7 | 0.093 | 1.8× | 0.097 | 0/3 | — | 7/8 | 92 % | 0 | no: latencia, coste |
| 50 | 13,407 | 14,605 | 32.7 | 41.8 | 0.114 | 2.2× | 0.063 | 2/3 | 0.061 | 6/6 | 92 % | 0 | no: latencia, coste |
| 100 | 26,698 | 27,896 | 47.4 | 45.0 | 0.190 | 3.6× | 0.187 | 1/3 | 0.068 | 6/6 | 85 % | 0 | no: latencia, coste |
| 200 | 53,315 | 54,513 | 52.3 | 41.1 | 0.319 | 6.0× | 0.080 | 2/3 | 0.072 | 6/6 | 69 % | 0 | no: latencia, coste |
| 400 | 106,663 | 107,861 | 46.4 | 43.2 | 0.544 | 10.3× | 0.540 | 1/3 | 0.084 | 6/6 | 120 % | 0 | no: latencia, coste |

Aguja citada por posición (sin topes, frío y caliente juntos):

| N | Principio | Medio | Final |
|---|---:|---:|---:|
| 5 | 2/2 | 2/2 | 2/2 |
| 25 | 2/2 | 3/4 | 2/2 |
| 50 | 2/2 | 2/2 | 2/2 |
| 100 | 2/2 | 2/2 | 2/2 |
| 200 | 2/2 | 2/2 | 2/2 |
| 400 | 2/2 | 2/2 | 2/2 |

Gasto conocido de la segunda corrida: 21.59 USD; llamadas pagadas sin precio: 15.
