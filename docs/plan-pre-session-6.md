# Plan pre-sesión 6 · Stress test del CAG

Rama `pre-session-6`, sale de `session-5`. Entregables del ejercicio: `evals/stress/REPORT.md` y
`evals/stress/results.csv`, versionados en el repo.

El ejercicio **mide, no optimiza**. Nada de este plan toca prompts, ventana, límites ni modelos
de producción: mover una constante invalidaría el baseline contra el que se comparará RAG en la
sesión 6.

Referencia: `lidr-ai-engineering`, rama `session_06` (rama local `session_06` creada). Allí
la solución añade `TurnObservation` a la respuesta, `evals/stress/{metrics,scenarios,run}.py`,
`fixtures/build_pdfs.py` con `fpdf2` y un `REPORT.md` plantilla. Se toma la idea, no la forma:
nuestro sistema es distinto en tres puntos que cambian el ejercicio (ver §1).

---

## 1. Del enunciado a nuestro sistema

El enunciado describe la arquitectura del curso (anclas heurísticas, summarizer acumulativo,
`MAX_CONVERSATION_TURNS=6`). La nuestra en `/sessions` es otra, decidida en la sesión 5 (ADR
0002): **ventana deslizante simple sin anclas ni summary; los hechos viven en la memoria**
(`ProjectMetadata`, delta validado en código), que no se recorta.

| Enunciado | Aquí | Consecuencia para el ejercicio |
|---|---|---|
| `EstimationService.estimate_conversational()` | `app/routers/sessions.py::estimate_session` orquesta el turno (actor + extractor de memoria); `generate_estimation` no conoce HTTP | El evento se emite en el router, que es quien ve los dos gastos (actor y extractor) |
| `anchors_count` | No hay anclas | Se emite como **número de hechos en memoria** (nombre, equipo, alcance, tecnologías, restricciones, descartes). Mismo papel: lo que sobrevive a la ventana |
| `summary_chars` | No hay summary | Se emite como **caracteres de la memoria renderizada** en el prompt (`_project_metadata.j2`). Mismo papel: el resumen que viaja siempre |
| `messages_in_window` | `slide_history` recorta por tokens y `SESSION_MAX_TURNS=6` | Mensajes de historial que **de verdad** entraron al prompt (hay que sacarlo de `llm_service`) |
| `cache_hit_kind` | Exacta sí puede acertar en `/sessions` (turno 1 repetido); la semántica se salta con historial o contexto dinámico | Se mide tal cual. Los repeats del turno 1 darán aciertos exactos: el informe da latencias con y sin aciertos |
| `MAX_ATTACHMENT_CHARS=60.000` | `ATTACHMENT_MAX_CHARS=40.000` por adjunto; el extractor de memoria ve 12.000 (`METADATA_ATTACHMENT_CHARS`) | El punto de 100 KB mide el régimen truncado, y el de 50 KB probablemente también |
| Adjuntos en el historial | D2: el historial guarda la **referencia** al adjunto, no su texto | Un dato del adjunto solo sobrevive al turno 2 si el extractor lo pasó a la memoria. Es la pregunta más interesante del bloque 3 |
| `evals/run.py` (httpx / TestClient) | `evals/harness.py` (JWT por tier) + `scripts/run_experiment.py` | El runner reutiliza el token por tier del harness |
| `MetricResult(name, score, passed, details)` | `PropertyResult(name, passed, detail)` en `app/evaluation/properties.py` | Ver D18 |
| Modelo `gpt-4o-mini` | `.env`: `claude-sonnet-5.5` (≈ 100 s y 0,06 USD por turno medidos en la sesión 5) | Ver D20: la matriz literal no es asumible |

---

## 2. Decisiones

Cada una con la opción recomendada primero.

- **D17 · Dónde vive la observación.** (a) **Campo aditivo `observation: TurnObservation` en
  `SessionEstimateResponse` + evento `turn_observed` en structlog con los mismos campos.** (b) Solo el log,
  parseado con `docker compose logs`. (c) Embebido en `GET /sessions/{id}`.
  *Por qué (a):* el runner lee el JSON que ya recibe, sin reconciliar timestamps ni depender
  de Docker. El campo es aditivo, así que no rompe el contrato. El log sigue sirviendo para
  producción (Langfuse/agregadores). (c) mezcla telemetría con el estado auditable de la sesión.
- **D18 · Dónde viven las métricas.** (a) **`evals/stress/metrics.py` con su propio `MetricResult`
  (`name, score, passed, details`)**. (b) Ampliar `app/evaluation/properties.py`.
  *Por qué (a):* operan sobre la observación del turno y el snapshot de la sesión, no sobre la
  vista del tier. `properties.py` es código de producción (puntuaciones online) y esto es
  instrumentación del ejercicio. Se justifica en el `REPORT.md`, como pide el enunciado.
- **D19 · Los PDFs.** (a) **Ampliar `evals/pdf.py` a varias páginas, sin dependencias nuevas.**
  (b) `fpdf2` como dependencia dev.
  *Por qué (a):* ya tenemos un escritor de PDF mínimo y el enunciado pide evitar dependencias.
  Es determinista. Los PDFs no se versionan (`.gitignore`) y el runner los regenera.
- **D20 · Matriz de ejecución.** La literal (3 escenarios × 5 tamaños × 3 repeats × 20 turnos =
  900 turnos) cuesta ≈ 55-65 USD y ≈ 25 h con el modelo actual. (a) **Matriz por bloque, ≈ 160 filas:**
  - Bloque 2, multi-turno: 3 escenarios × 20 turnos × 2 repeats sin adjunto = 120 filas.
  - Bloque 3, adjuntos: 5 tamaños × 3 repeats × 2 turnos = 30 filas. El turno 2 es una
    pregunta corta que comprueba si el dato del adjunto sobrevivió vía memoria.
  - Ráfaga: 10 sesiones concurrentes × 1 turno = 10 filas.

  Coste ≈ 10-12 USD y ≈ 4 h en serie.
  (b) Matriz literal. (c) Matriz (a) con `gpt-4o-mini`.
  *Por qué (a):* cumple el mínimo (≥ 50 filas) con margen. Los N ∈ {1, 3, 6, 10, 20} salen de
  la misma sesión de 20 turnos, y el tamaño de adjunto es un estrés del turno inicial (así lo
  plantea el bloque 3), no algo que haya que cruzar con 20 turnos. (c) mediría otro sistema
  distinto del que corre en la app.
- **D21 · Tier de la medición.** (a) **`developer` (sin revisión) por defecto, `--tier` para
  cambiarlo.** (b) `pm` (con Actor-Critic-Boss).
  *Por qué (a):* aísla el CAG. La revisión duplica llamadas y metería ruido en la curva
  latencia-tokens. Las herramientas siguen activas según `tool_policy`, y la fila registra
  cuáles se usaron.
- **D22 · Cachés.** (a) **Encendidas, como en la app, y reportadas.** El informe da P50/P95 con y
  sin aciertos. (b) Apagadas, como en `evals/`.
  *Por qué (a):* el enunciado pide la tasa de aciertos de ambas cachés. Con (b) siempre
  saldría 0.
- **D23 · Ráfagas.** (a) **Escenario `burst` aparte, con `--concurrency`.** (b) Sin ráfagas.
  *Por qué (a):* la guía nombra tres cargas (multi-turno, adjuntos, ráfagas) y hay un solo
  worker. Es barato (10 turnos) y responde si el P95 aguanta con concurrencia. Los escenarios
  2 y 3 corren con concurrencia 1, para que la latencia no se contamine.

---

## 3. Pasos

Cada paso termina con `check_structure`, `ruff`, `pytest`, revisión externa (GPT-Sol) y
commit.

### Paso 1 · `turn_observed` (bloque 1)

- `app/schemas/session.py`: `TurnObservation` con los 13 campos y estos extras:
  `attachments_truncated`, `memory_facts`, `tools_used`, `review_decision`, `actor_ms`,
  `extractor_ms`, `cached_tokens`.
- `app/services/llm_service.py`: el resultado expone `history_messages_used`, lo que quedó
  tras `slide_history`.
- `app/routers/sessions.py`: construye la observación antes del `return`, la emite con
  `log.info("turn_observed", ...)` y la añade a la respuesta.
- Mapeo documentado en el docstring del schema y en `docs/specs/` (un escenario nuevo en
  `memoria.md` o `ciclo-de-vida.md` con su test).
- **Hecho cuando** cada `POST /sessions/{id}/estimate` emite `turn_observed` con los campos
  (test con el modelo simulado: primer turno, turno con adjunto truncado y acierto de caché
  exacta).

### Paso 2 · Escenarios y fact-tracker (bloque 2)

- `evals/stress/scenarios.py`: `ScenarioTurn(transcript, facts)` con `Fact(text, where,
  expect="present"|"absent")`. Cada escenario tiene 20 turnos.
  - `growing`: proyecto «Nimbus». Cada turno añade un requisito (auth, multi-tenant, audit log,
    CSV…). Fact: el `project_name` del turno 1. Además prueba el tope `MAX_LIST_ITEMS=20`
    de la memoria, que puede ser una fuente de deriva.
  - `pivot`: en el turno 5 pasa de React a Flutter. Facts: `Flutter` presente en
    tecnologías; `React` ausente de tecnologías y presente en `rejected_options`. Nuestro
    delta lo permite; se mide si lo hace.
  - `contradiction`: presupuesto de 30 k€ en el turno 3 y de 80 k€ en el turno 8. Facts:
    `80` presente y `30` ausente en las restricciones.
  - `burst`: un turno inicial idéntico para todas las sesiones concurrentes.
- **Hecho cuando** hay un test que valida la forma: 20 turnos, facts bien formados y el
  primer turno con ≥ 50 caracteres.

### Paso 3 · Corpus de adjuntos (bloque 3)

- `evals/pdf.py`: PDF de varias páginas.
- `evals/stress/fixtures/build_pdfs.py`: genera `attach_{5,20,50,100}kb.pdf` con texto de un
  pliego ficticio y **tres marcadores plantados** (al 10 %, 50 % y 90 % del texto, p. ej.
  «requisito de cumplimiento ISO-27001-KX7»). Así el recall del adjunto distingue entre
  «el modelo no lo usó» y «lo cortó el truncado a 40.000».
- **Hecho cuando** los tamaños quedan dentro de ±10 % y un test comprueba que el extractor
  real recupera los marcadores (y que el del 90 % del de 100 KB queda fuera).

### Paso 4 · Métricas (bloque 4)

- `evals/stress/metrics.py`:
  - `LatencyBudgetMetric(budget_ms)` y `CostBudgetMetric(budget_usd)` sobre la observación.
  - `MemoryDriftMetric(fact, where, expect)` sobre el snapshot de `GET /sessions/{id}`, más
    el texto de la última estimación cuando `where` incluye `answer`. Coincidencia exacta sin
    distinguir mayúsculas: sin embeddings ni juez.
- Presupuestos por defecto (contratos de diseño, D16 de la sesión 5): latencia 25.000 ms (el
  objetivo PM del plan anterior) y coste 0,05 USD por turno. Se pueden cambiar con flags.
- `tests/test_stress_metrics.py`: uno pasa, uno falla y uno límite (igual al presupuesto,
  fact vacío o snapshot sin memoria).

### Paso 5 · Runner (bloque 5)

- `evals/stress/run.py`, invocado como `uv run python -m evals.stress.run --http
  http://localhost:8000 --scenarios growing,pivot,contradiction,burst --attachment-sizes
  0,5,20,50,100 --repeats 2 --output evals/stress/results.csv`.
  - `--http` usa httpx contra la app en `AUTH_MODE=jwt`, con el token firmado como en
    `evals/harness.py`. Sin `--http`, usa `TestClient` (humo con el modelo simulado en tests).
  - Una fila por turno: los campos de la observación, el escenario, el tamaño, el repeat y
    una columna 0/1 por métrica.
  - Escribe en append y salta lo hecho (`--resume`): una corrida de 4 h puede cortarse.
  - Imprime el gasto acumulado y se para si supera `--max-usd` (por defecto 15).
- `tests/test_stress_runner.py`: humo en proceso con el LLM simulado (2 turnos, CSV con las
  columnas esperadas).

### Paso 6 · Ejecutar y escribir el `REPORT.md`

- Corrida en segundo plano. Es la única parte que cuesta dinero (≈ 10-12 USD).
- `evals/stress/report.py` agrega el CSV en las tablas: resumen escenario × tamaño (P50/P95,
  coste, aciertos exacta/semántica, recall medio) y las tres curvas (latencia por tramo de
  `tokens_in`, coste acumulado por turno, recall por N). Los **dos párrafos de lectura** los
  escribo yo a partir de los números, con al menos una afirmación cuantitativa concreta.
- Incluye la justificación de D17, D18 y D20 y el mapeo de §1.
- `docs/insights.md`: lo reutilizable (presupuestos como tests, fallo duro frente a
  degradación silenciosa, cómo leer las tres curvas). CHANGELOG y `check_structure` al día.

---

## 4. Lo que no entra

RAG; optimizar el CAG (ventana, prompt, `LLM_MAX_TOKENS`, modelo); comparar proveedores;
notebooks y gráficos; persistir en BBDD; juez LLM para la deriva; panel nuevo en el front.

**Anclas en `/sessions`.** El curso las enseña (anclas heurísticas + summary acumulativo) y aquí
existen en `/api/v2/conversations` (primer turno y última estimación). En `/sessions` las
sustituye la memoria por delta (ADR 0002): guarda el hecho y no el turno, resuelve
contradicciones en código y su coste es acotado. Su punto débil es depender del extractor:
si no captura un hecho, no queda turno de respaldo en la ventana. `MemoryDriftMetric` mide
exactamente eso. Si el informe muestra deriva (p. ej. `growing` con la memoria llena), las
anclas pasan a ser una **variante a comparar en la sesión 6 junto a RAG**, no un cambio del
baseline.

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| La corrida tarda horas y puede cortarse | `--resume` y append al CSV |
| Coste por encima de lo previsto | `--max-usd` y el gasto impreso por fila |
| Herramientas externas (Tavily, backend de negocio) añaden ruido de red | Se registra `tools_used`. El backend de negocio debe estar levantado. Tavily solo si el turno lo pide |
| El acierto de caché exacta en los repeats sesga el P50 | El informe separa las latencias con y sin aciertos |
| Memoria llena (`MAX_LIST_ITEMS`) en `growing` | No es un riesgo, es un hallazgo: se reporta |
