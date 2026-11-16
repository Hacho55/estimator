# 0002 · Historial y memoria separados; memoria por delta

**Estado:** aceptada (sesión 5, fase 2).

## Contexto

El usuario espera que el sistema recuerde el nombre del proyecto, la stack o el alcance aunque
el turno donde lo dijo haya salido de la ventana. Mezclar historial y hechos en un único blob
hace que recortar uno rompa el otro.

## Decisión

- **Dos estructuras.** `ConversationHistory` (lo bruto, ventana deslizante de 6 pares sin anclas)
  y `ProjectMetadata` (lo destilado: nombre, equipo, tecnologías, alcance, restricciones,
  opciones descartadas). Se serializan por separado (`Session.to_record()`).
- **Memoria en el system, al final y fuera de la caché** (D6). Va en un segmento propio después
  del `cache_control` y fuera del hash de `prompt_version`: cambia cada turno sin romper la
  caché del prefijo ni la versión. Se renderiza campo a campo y como «hechos establecidos» que
  son datos, no instrucciones.
- **El extractor propone un delta** (`metadata/v2`, modelo barato) y `app/state/memory.py` lo
  valida y fusiona sin LLM. Retirar una tecnología la pasa a `rejected_options`. Cada cambio
  queda auditado (`memory_log`, `GET /sessions/{id}`).
- **Olvido explícito en tres sitios:** revisión (memoria), caducidad que archiva a las 24 h
  (almacén, 409) y reset que archiva la anterior y puede heredar la memoria (endpoint).

## Alternativas descartadas

- **Extractor que devuelve la memoria entera:** un descuido del modelo borra hechos.
- **Memoria en el mensaje de usuario:** protege la caché, pero el material pide autoridad de
  «hechos establecidos» en el system; el segmento final consigue las dos cosas.
- **Resumen acumulativo y anclas (la referencia):** con memoria aparte, la ventana simple basta.

## Consecuencias

Una llamada más por turno (el extractor). El presupuesto la suma. Persistencia en memoria del
proceso; pasar a Redis solo cambia el almacén (`SessionStore`).

## Revisar cuando

Conversaciones de días con matices literales (negociación): resumen o anclas sobre la ventana.
