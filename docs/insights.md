# Insights (sesiones 2–5)

Guía corta al empezar otro proyecto con LLMs o agentes. Una práctica y por qué.

## Sesión 2 — Contexto y CAG

- Lleva el contexto en la llamada cuando el corpus cabe y es estable: evita retrieval cuando no aporta.
- Menos es más: anonimiza y precalcula fuera del modelo; no le pidas aritmética que ya sabes.
- Orden fijo del prefijo (tarifas y ejemplos): habilita la caché del proveedor entre peticiones.
- Mide coste y tokens cacheados al elegir formato y detalle: la calidad no siempre mejora con más texto.

## Sesión 3 — Fiabilidad y producto

- Reserva de modelo ante fallos transitorios: un solo reintento con otro modelo, no un bucle opaco.
- Caché exacta de respuestas idénticas: ahorra llamadas cuando la petición no cambió.
- Streaming del markdown visible, no del JSON crudo: el usuario ve progreso sin romper el contrato.
- Observabilidad desde el día uno (trazas, scores, request id): sin eso no se itera el prompt con datos.

## Sesión 4 — Prompt, contrato y guardrails

- El prompt es un artefacto versionado fuera del código de ensamblado: se lee y se compara como un documento.
- Entrada tipada (formulario) en lugar de texto libre que reescribe instrucciones: mismo formulario, mismo system prompt.
- Salida con schema como contrato; validar forma y contenido en entrada y salida.
- Cada guardrail declara qué hace al fallar; registrar antes de bloquear evita falsos positivos en producción.
- Caché semántica con la versión del prompt en la clave y escrita solo tras validar: un acierto no sirve basura ni mezcla variantes.

## Sesión 5 — Sesiones, memoria y adjuntos

- Decide la política de la ventana antes de implementarla: una ventana que descarta los pares más viejos no es lo mismo que una que conserva el primer turno como ancla. Cambia el contrato y los tests.
- Separa historial y hechos del proyecto. Los hechos van en un bloque del system prompt que se regenera en cada turno, así que no se pierden al recortar la ventana.
- Extraer hechos con una segunda llamada: si falla, la estimación sigue y se conservan los hechos anteriores. Suma sus tokens y su coste a la respuesta, o el coste queda subestimado.
- Adjuntos acotados antes de llegar al prompt: lee en trozos hasta el límite de bytes, limita el texto extraído y sanea el nombre que entra en el modelo.
- Una versión publicada de plantilla no se edita. Una versión nueva es otra carpeta, con su propio hash en `prompt_version`.
