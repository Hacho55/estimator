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

---

## 6. Segunda corrida · llevar el contexto al límite

### Por qué

La primera corrida rompió el sistema, pero por la salida: el 37 % de turnos con 502
`llm_output_truncated`, que RAG no arregla. Lo que justificaría RAG quedó sin medir, porque
nuestros propios topes acotaron la entrada a unos 37 k tokens:

- **Contexto de entrada.** El tope de adjuntos (`ATTACHMENT_MAX_CHARS=40.000`) y la ventana de
  6 turnos dejan la entrada lejos de la ventana configurada (`MODEL_CONTEXT_WINDOW=128.000`).
  La curva de latencia frente a `tokens_in` salió plana porque el rango era estrecho.
- **Corpus del CAG.** Las tarifas y los 5 ejemplos históricos (≈ 1.300 tokens) viajan en cada
  llamada. Con más ejemplos, `build_context_block` se queda con los primeros
  `CONTEXT_MAX_EXAMPLES=7` **por orden de la lista, no por relevancia**, y baja el detalle o
  descarta hasta `CONTEXT_MAX_TOKENS=6.000`. Esta es la pregunta central de CAG frente a RAG:
  qué pasa cuando el conocimiento de referencia crece.

Sigue sin optimizarse nada: los valores por defecto de producción no cambian. Cada bloque es
una **variante de experimento**, declarada en la fila del CSV.

### Puntos de rotura que se esperan

Se anotan antes de correr, para que el informe diga si se confirmaron:

| Punto | Dónde | Qué pasaría |
|---|---|---|
| Salida | `LLM_MAX_TOKENS` | 502 `llm_output_truncated` (ya medido) |
| Tiempo | `LLM_TIMEOUT_SECONDS=60` | Con entradas de 100 k+ o salidas de 16 k, la llamada puede superar el timeout |
| Ventana configurada | `_prepare` comprueba `budget.fits` (entrada + `LLM_MAX_TOKENS` + margen de 5.000 frente a `MODEL_CONTEXT_WINDOW`) | Rechazo **local** 413 `context_budget_exceeded`, antes de llamar al actor y sin coste. Es un fallo duro y declarado |
| Ventana del proveedor | LiteLLM / modelo | Solo se alcanza si la variante sube `MODEL_CONTEXT_WINDOW` a la ventana documentada del modelo. Rechazo del proveedor, probablemente como 5xx |
| Relevancia | `CONTEXT_MAX_EXAMPLES`, `CONTEXT_MAX_TOKENS` | Sin error: el ejemplo pertinente queda fuera en silencio |

### Umbrales de viabilidad, fijados antes de correr

Un punto (tamaño de pliego o N del corpus) es **viable para el CAG** si cumple las tres
condiciones:

- **Coste y latencia:** cumple los presupuestos de la primera corrida en la mediana. Es decir,
  latencia ≤ 25 s y coste ≤ 0,05 USD por turno; en C, medidos en caliente.
- **Recuperación:** en B, recall de los marcadores en la respuesta ≥ 80 %. En C, la aguja se
  cita en ≥ 2 de 3 repeticiones.
- **Fallos:** sin fallos duros en ninguna repetición.

El informe da el primer punto que deja de ser viable y por cuál de las tres condiciones.

### Decisiones

- **D24 · Variantes por entorno, en proceso.** (a) **El runner en proceso (TestClient) fija los
  topes de la variante con variables de entorno y limpia la caché de settings; cada fila
  guarda `variant` y los topes efectivos.** (b) Un segundo servidor con otro `.env`.
  *Por qué (a):* no toca los valores por defecto ni el servidor de la demo, y la fila dice con
  qué topes se midió.
- **D25 · Cómo entra el corpus sintético.** (a) **Campo aditivo `estimates` (y `max_tokens`) en
  `ContextOverrides`, que `_context_for` pasa a `build_context_block`**, igual que ya hace con
  `max_examples`. (b) Parchear `load_estimates` desde el runner.
  *Por qué (a):* es explícito, tiene test y no depende de detalles internos. En producción
  nadie lo rellena, así que el comportamiento no cambia.
- **D26 · Corpus sintético con aguja, y dos medidas distintas.**
  - **El generador.** (a) **Generador determinista con semilla
    (`evals/stress/fixtures/build_corpus.py`).** (b) Pedir el corpus a un LLM.
    - Los distractores son de sectores distintos al de la consulta.
    - La aguja es una estimación casi idéntica al proyecto consultado. Lleva un **dato
      exclusivo** que no aparece en ningún otro sitio: su id y título, y una integración con
      nombre propio.
    - *Por qué (a):* es reproducible, gratis y sin juez.
  - **Recuperación (`needle_cited`).** La transcripción pide explícitamente indicar qué proyecto
    histórico se usa como referencia. Se cumple si la respuesta nombra el id o el título de la
    aguja. Es la prueba de que el modelo la encontró.
  - **Influencia (`hours_shift`).** Desplazamiento de las horas totales frente a un **control
    emparejado sin aguja, con los mismos distractores y el mismo N**. Se reporta como
    influencia, no como prueba de recuperación. La aguja lleva el doble de la mediana de horas
    del control de N = 5 para que el desplazamiento se distinga.
- **D27 · Marcadores densos para el bloque B, comprobados en la respuesta.**
  - Nueve requisitos marcados al 10, 20 … 90 % del documento, aparte de los tres del bloque 3,
    que no cambian.
  - La transcripción pide enumerar los requisitos obligatorios del pliego. El recall se mide
    **sobre la respuesta del actor**.
  - Por marcador se registra si sobrevivió a la extracción del PDF, al truncado y al guardrail,
    para que el denominador sean los marcadores que el actor pudo ver.
  - La captura en memoria se mide aparte. Su denominador son solo los marcadores dentro de los
    12.000 caracteres que ve el extractor (`METADATA_ATTACHMENT_CHARS`).
- **D28 · Cachés: aisladas, verificadas y etiquetadas.**
  - **Caché de respuestas.** Se borra antes de cada llamada en B y en C. Una fila con
    `cache_hit_kind ≠ none` se invalida.
  - **Caché del prefijo.** Es la defensa principal del CAG (en la primera corrida el token
    cacheado salió ≈ 30 veces más barato), así que C se mide en frío y en caliente.
    - **Frío:** cada muestra fría lleva un nonce en el primer ejemplo del corpus (un id
      distinto, mismo contenido), para que el proveedor no pueda tener ese prefijo cacheado.
    - **Caliente:** la siguiente llamada repite el prefijo exacto.
  - El estado se **verifica con lo que reporta el proveedor**: `cached_tokens` y, si llega,
    tokens de creación de caché. Una muestra fría con tokens cacheados, o una caliente sin
    ellos, se marca como "parcial" y no entra en la comparación.
- **D29 · Qué es el tamaño de contexto.**
  - **Eje de las curvas:** los tokens de entrada **de la primera llamada al actor**. Hace falta
    un campo aditivo en la observación, `actor_input_tokens`, y la estimación del presupuesto
    (`budget.total_input`).
  - **Coste del turno:** se sigue usando `tokens_in` agregado (actor, investigación, extractor
    y reparaciones), con el desglose por llamada.
- **D30 · Gasto.** Unos 17–20 USD con precio conocido, `--max-usd 30`. Además, los turnos sin
  precio se estiman con los precios de la regresión de la primera corrida.

### Bloques

**A · Atribución del truncado (control emparejado).**
- Qué se corre:
  - `pivot`, una repetición con `LLM_MAX_TOKENS=8000` y otra con `16000`. Las dos con
    `LLM_TIMEOUT_SECONDS=300` y herramientas como en la primera corrida (el backend de negocio
    levantado).
  - 40 turnos, ≈ 3 USD.
- Por qué cambiar una sola variable: así la diferencia se atribuye a la salida.
- Qué se registra: `messages_in_window`. Con unos 24 k de entrada, la reserva de salida no
  debería recortar el historial, y se comprueba.
- Qué se separa en el informe: truncados, timeouts, otros errores y deriva.

**B · Barrido de contexto de entrada.**
- Qué se corre:
  - Un turno con pliego de 50, 100, 150, 200, 250 y 300 KB, tres repeticiones.
  - Variante sin tope efectivo de adjuntos (`ATTACHMENT_MAX_CHARS` y
    `ATTACHMENT_MAX_EXPANDED_BYTES`), con `LLM_MAX_TOKENS=16000` y `LLM_TIMEOUT_SECONDS=300`.
  - Dos subvariantes:
    - **B1, guarda tal cual:** encuentra el tamaño en que el estimador responde 413, sin coste.
    - **B2, `MODEL_CONTEXT_WINDOW` a la ventana documentada del modelo:** encuentra el límite
      del proveedor o del timeout. Solo para los tamaños que B1 rechaza.
  - ≈ 24 llamadas, ≈ 6 USD.
- Qué se mide:
  - `actor_input_tokens`, latencia y coste.
  - Recall por marcador en la respuesta (D27).
  - Origen del rechazo: local, proveedor o timeout. Y si llegó a invocarse el actor.

**C · Barrido del corpus del CAG.**
- Qué se corre (≈ 60 llamadas, 8–10 USD):
  - N = 5, 25, 50, 100, 200 y 400 estimaciones; 400 ≈ 100 k tokens de contexto.
  - Aguja al principio, en medio y al final de la lista.
  - Tier `developer` sin herramientas (sin backend ni Tavily), para que el corpus sea la única
    referencia.
  - Modo **CAG tal cual** (topes por defecto): N = 50 y 200, las tres posiciones, una
    repetición. Qué ejemplos entran se calcula en código, sin modelo. Las llamadas solo
    muestran la consecuencia.
  - Modo **CAG sin topes** (todo el corpus en el prompt):
    - Cada N y posición, en frío y en caliente.
    - Un control emparejado sin aguja por N.
    - Tres repeticiones independientes de `needle_cited` en la N donde aparezca el primer
      deterioro y en la anterior, para reportar proporciones y no un fallo aislado.
- Qué se mide:
  - `actor_input_tokens`, `cached_tokens`, latencia y coste, en frío y en caliente.
  - `needle_cited`, `hours_shift` y si la aguja entró en el prompt.

### Pasos

- **Paso 7 · Variantes.**
  - `--variant` en el runner con los presets A, B1, B2 y C.
  - Columnas `variant`, `max_tokens_out`, `context_window`, `attachment_cap`, `corpus_size`,
    `needle_position`, `needle_included`, `needle_cited`, `hours_shift`, `cache_state`,
    `rejection_origin` y `actor_input_tokens`.
  - `ContextOverrides.estimates` y `max_tokens` (D25); `actor_input_tokens` en
    `TurnObservation`, aditivo (D29).
  - Tests con el modelo simulado.
- **Paso 8 · Corpus.** `build_corpus.py` con la aguja y el nonce. `--dry-run` imprime sin coste
  los tokens de contexto por N y si la aguja entra en el modo tal cual. Corrida del control.
- **Paso 9 · Marcadores densos** (D27) y los PDFs de 150–300 KB, regenerados y fuera de git.
- **Paso 10 · Ejecutar A, luego B1, B2 y C,** en segundo plano, con `--resume`, a un CSV aparte:
  `results_context.csv`. A va primero porque es el más barato y confirma la causa del primer
  informe.
- **Paso 11 · Informe.** Sección "Segunda corrida" en `REPORT.md`:
  - Tablas de A (8 k frente a 16 k), de B (tamaño frente a tokens, latencia, coste, recall por
    posición y origen del rechazo) y de C (N frente a tokens, coste frío y caliente, latencia,
    `needle_cited` por posición y modo).
  - El primer punto no viable según los umbrales y por qué.
  - El contrafactual de RAG (k fragmentos, tokens fijos) como **estimación con los precios
    medidos**, no como medición.
  - Después, insights, CHANGELOG, revisión con GPT-Sol y commit.

### Ejecución (desvíos frente al plan)

- **B ampliado hasta 1.000 KB.** El relleno repetitivo tokeniza a unos 0,23 tokens por carácter, así que 300 KB eran solo 70 k tokens. B1 se amplió a 400–1.000 KB para llegar a la guarda (413 desde 500 KB) y B2 corrió 500, 750 y 1.000 KB.
- **C se cayó en la segunda llamada** porque cerraba el event loop entre llamadas. Se corrigió con un único `asyncio.Runner` y se reanudó desde el control ya hecho.
- **Repeticiones extra solo en N = 25, aguja en medio.** Fue el único fallo de cita, y no se repitió.
- **Gasto:** 21,59 USD conocidos más unos 2–3 USD estimados de turnos truncados sin precio, dentro del tope de 30.

### Riesgos de la segunda corrida

| Riesgo | Mitigación |
|---|---|
| Subir `LLM_MAX_TOKENS` convierte truncados en timeouts | `LLM_TIMEOUT_SECONDS=300` en todas las variantes, y el error se registra por tipo |
| La ventana documentada del proveedor no es la real | B2 anota el tamaño y el mensaje del rechazo: es un resultado |
| La aguja no se cita ni mueve las horas, ni siquiera con N = 5 | El control lo detecta antes de gastar en C. Entonces C se queda en coste y latencia, y se dice |
| El proveedor no reporta la caché del prefijo | Las muestras quedan como "parcial" y la comparación frío/caliente se marca como no medida |
| Gasto por encima de lo previsto | `--max-usd 30`, y A primero |
