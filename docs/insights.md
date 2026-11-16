# Insights para otros proyectos con LLMs

Prácticas aprendidas en las sesiones 2–5 del estimador, ordenadas por tema. Cada una sigue la
plantilla **principio · por qué · cuándo no**: el «cuándo no» es lo que la hace útil en otro
proyecto. Entre paréntesis, la sesión de origen.

## Contexto y prompt

- **Lleva el contexto en la llamada cuando el corpus cabe y es estable** (2). Por qué: evita
  montar retrieval cuando no aporta. Cuándo no: corpus grande o cambiante; ahí, RAG.
- **Menos es más: anonimiza y precalcula fuera del modelo** (2). Por qué: el modelo no es fiable
  con la aritmética y cada token cuesta. Cuándo no: cuando el cálculo es el juicio que se pide.
- **Orden fijo del prefijo** (2). Por qué: habilita la caché del proveedor entre peticiones.
  Cuándo no: si el proveedor no cachea, el orden importa solo para la legibilidad.
- **Mide coste y tokens cacheados al elegir formato y detalle** (2). Por qué: la calidad no
  siempre mejora con más texto. Cuándo no: no hay excepción; se mide antes de decidir.
- **El prompt es un artefacto versionado fuera del código** (4). Por qué: se lee y se compara
  como un documento, y una versión publicada no se edita: la nueva es otra carpeta con su hash.
  Cuándo no: prototipos de un día.
- **Lo que cambia en cada petición va después del último punto de caché y fuera del hash de
  versión** (5). Por qué: si no, la caché se rompe en cada turno y la versión deja de identificar
  la plantilla. Cuándo no: si el proveedor no cachea y la versión no se usa para comparar.
- **Parciales `include` desde la primera variante** (5). Por qué: plantillas que comparten el
  80 % divergen en semanas si se copian. Cuándo no: con una sola plantilla.

## Contexto dinámico

- **Lo que llega de fuera es dato, no programa** (5). Por qué: concatenarlo sin delimitar es una
  vía de inyección; las etiquetas de bloque dentro del dato se neutralizan. Cuándo no: nunca; lo
  que varía es cuánto se sanea.
- **Cada fuente de contexto tiene su bloque en el presupuesto y su span** (5). Por qué: sin
  medir por fuente no se sabe qué recortar ni qué es lento. Cuándo no: con una sola fuente
  estable basta el total.
- **Un adjunto se paga una vez** (5). Por qué: reenviar un documento en cada turno multiplica el
  coste; el historial guarda la referencia y lo importante pasa a la memoria. Cuándo no: si cada
  turno necesita el texto literal (revisión de un contrato cláusula a cláusula).
- **Acota los adjuntos antes de que lleguen al prompt; trunca con aviso** (5). Por qué: los
  topes de bytes protegen el proceso, el de texto protege el prompt, y truncar con aviso sirve
  más al usuario que rechazar. Cuándo no: documentos donde perder el final cambia la respuesta.
- **Los servicios del negocio se consultan por contrato HTTP, nunca por su BBDD** (5). Por qué:
  evita acoplar esquemas, credenciales amplias y lógica duplicada. Cuándo no: el almacén propio
  del servicio IA (vectores, cachés) sí vive a su lado.
- **Las herramientas, solo con motivo** (5). Por qué: cada una añade latencia y tokens; si no se
  ofrece ninguna, no hay llamada. Cuándo no: asistentes de investigación, donde buscar es la tarea.

## Memoria y conversación

- **Historial y memoria son estructuras distintas** (5). Por qué: el historial responde «¿qué se
  dijo?» y la memoria «¿qué sabemos?»; la memoria sobrevive al recorte y es auditable. Cuándo no:
  conversaciones de un solo turno.
- **Con memoria aparte, la ventana deslizante simple basta** (5). Por qué: lo que importa ya no
  depende de los turnos viejos. Cuándo no: cuando los matices literales importan (negociación,
  soporte legal); ahí, resumen o anclas.
- **Pide al extractor un delta y fusiona en código** (5). Por qué: un descuido del modelo no
  borra hechos y cada cambio queda registrado. Cuándo no: si los hechos son pocos y se pueden
  reextraer enteros cada turno sin riesgo.
- **La llamada auxiliar no tumba la principal y su gasto se suma** (5). Por qué: si el extractor
  falla, la estimación sigue; si no se suma su coste, el coste real queda oculto. Cuándo no: si
  la auxiliar es imprescindible para responder.
- **Olvidar es diseño: revisión, caducidad y reset viven en tres sitios** (5). Por qué: sin
  políticas explícitas, los hechos viejos contaminan decisiones nuevas. Cuándo no: sesiones
  efímeras sin estado.

## Contratos, perfiles y seguridad

- **Entrada tipada en lugar de texto libre que reescribe instrucciones** (4). Por qué: mismo
  formulario, mismo system prompt. Cuándo no: asistentes conversacionales abiertos.
- **Salida con schema como contrato, validada en entrada y salida** (4). Por qué: un error de
  forma se detecta antes de que llegue al usuario. Cuándo no: texto libre que solo lee una persona.
- **Un perfil cambia la estructura de la salida, no solo el tono** (5). Por qué: si el schema es
  el mismo, la adaptación es cosmética. Cuándo no: perfiles que solo difieren en idioma o registro.
- **El perfil lo decide el servidor y viaja firmado; el cliente nunca lo elige en el cuerpo** (5).
  Por qué: si no, cualquiera pide el modo más caro. Cuándo no: preferencias puramente visuales.
- **Separa el schema del modelo del schema que ve el cliente** (5). Por qué: el modelo aporta
  juicio y tareas; el código calcula totales y rangos. Cuándo no: salidas sin cifras derivadas.
- **Cada guardrail declara qué hace al fallar; registrar antes de bloquear** (4). Por qué: evita
  falsos positivos en producción. Cuándo no: datos personales, que se tachan siempre.
- **Tacha en un único punto lo que se guarda** (5). Por qué: la vista, el historial y las cachés
  deben salir del mismo objeto ya filtrado; filtrar cada copia deja huecos. Cuándo no: no hay
  excepción.

## Fiabilidad y cachés

- **Reserva de modelo ante fallos transitorios: un reintento con otro modelo** (3). Por qué: un
  bucle opaco multiplica coste y latencia. Cuándo no: errores de validación, que se reparan.
- **Caché exacta de respuestas idénticas** (3). Por qué: ahorra llamadas cuando nada cambió.
  Cuándo no: respuestas con datos que caducan (resultados de herramientas).
- **La clave de caché incluye todo lo que cambia la respuesta** (5). Por qué: memoria, adjuntos,
  versión o modo de revisión fuera de la clave sirven la respuesta de otro contexto. Cuándo no:
  no hay excepción.
- **Caché semántica con la versión en la clave y escrita solo tras validar** (4). Por qué: un
  acierto no sirve basura ni mezcla variantes. Cuándo no: con contexto dinámico o revisión.
- **Streaming del contenido visible, no del JSON crudo** (3). Por qué: el usuario ve progreso sin
  romper el contrato. Cuándo no: clientes máquina, que esperan el objeto completo.
- **Un cliente HTTP asíncrono queda atado a su bucle** (5). Por qué: mezclar bucles (scripts con
  varios `asyncio.run`, un framework de evals con el suyo) rompe con errores poco claros; o un
  bucle compartido, o un cliente por bucle. Cuándo no: código síncrono.

## Evaluación

- **Con LLMs se testean propiedades, no igualdades** (5). Por qué: la igualdad da falsos
  negativos masivos y, relajada, falsos positivos silenciosos. Cuándo no: las partes
  deterministas, que siguen con tests clásicos.
- **Pirámide: muchas comprobaciones deterministas, algunas estadísticas y pocas con juez** (5).
  Por qué: coste, velocidad y no depender de un único punto de fallo. Cuándo no: no hay
  excepción; lo que se comprueba con código no va al juez.
- **Separa los tests del código de las evaluaciones del sistema** (5). Por qué: una suite rápida
  y fiable se corre; una lenta y cara, no. Cuándo no: prototipos sin CI.
- **Las propiedades como funciones puras sirven a la vez para la suite y para producción** (5).
  Por qué: una definición, dos usos, sin coste extra. Cuándo no: propiedades que necesitan el
  caso esperado (solo offline).
- **El juez se calibra con veredictos humanos y es de otra familia que el generador** (5). Por
  qué: el juez también se equivoca y su umbral no es universal. Cuándo no: criterios binarios y
  verificables, que no necesitan juez.
- **El golden dataset lleva sus criterios de éxito y se revisa cada trimestre** (5). Por qué:
  una lista de entradas sin criterios no evalúa nada y el tráfico real cambia. Mientras un caso
  sea borrador, sus umbrales avisan pero no bloquean. Cuándo no: no hay excepción.
- **Observabilidad desde el día uno** (3). Por qué: sin trazas ni scores no se itera con datos.
  Cuándo no: no hay excepción.

## Orquestación y calidad

- **Cuando el fallo es de verificación, separa generar de evaluar** (5). Por qué: el mismo modelo
  en la misma llamada defiende lo que acaba de escribir. Cuándo no: tareas simples o ya
  cubiertas por comprobaciones deterministas.
- **Evaluar y decidir son roles distintos; el que decide puede ser código** (5). Por qué: rompe
  los bucles y la aceptación temprana, y es demostrable. Cuándo no: si la decisión requiere un
  juicio que no cabe en reglas.
- **Feedback estructurado (categoría, severidad y campo), nunca prosa** (5). Por qué: quien
  decide no tiene que interpretar texto. Cuándo no: feedback destinado solo a una persona.
- **Siempre con tope de iteraciones; al agotarse, lo mejor con los avisos a la vista** (5). Por
  qué: coste y latencia acotados. Cuándo no: procesos sin presupuesto de latencia, que aun así
  necesitan un tope de coste.
- **Los patrones caros solo en los caminos críticos, y midiendo si compensan** (5). Por qué:
  triplicar las llamadas en todo arruina coste y experiencia. Cuándo no: si el coste del error
  domina en todos los caminos.
- **Mide la latencia real antes de prometerla** (5). Por qué: el plan suponía < 25 s y la primera
  medición dio ~105 s: el tamaño de la salida manda más que el número de llamadas. Cuándo no:
  no hay excepción.
