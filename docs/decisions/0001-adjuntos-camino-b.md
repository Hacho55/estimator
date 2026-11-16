# 0001 · Adjuntos por extracción local y como dato

**Estado:** aceptada (sesión 5, fase 1).

## Contexto

Nadie estima con una transcripción aislada: llegan PDF de especificación, propuestas en Word,
resultados de búsqueda o datos del histórico. Ese contexto dinámico tiene tres riesgos: es una
vía de inyección (regla 1), cuesta en cada llamada (regla 2) y añade latencia (regla 3).

## Decisión

- **Camino B**: el texto se extrae en el proceso (`pypdf`, `python-docx`, tablas incluidas, con
  marca de página) y solo el texto llega al modelo. No se usa la Files API del proveedor.
- Todo contexto dinámico va **delimitado y declarado como dato**: `<attachments>`,
  `<tool_results>`, `<project_metadata>`. Las etiquetas de bloque dentro de un dato se
  neutralizan (`as_data`). Adjuntos y resultados de herramientas pasan un guardrail propio:
  PII tachada; la inyección se registra pero no bloquea.
- **Un adjunto se paga una vez** (D2): el historial guarda `[adjunto: nombre, N caracteres]` y
  lo importante pasa a la memoria.
- **Truncar con aviso** (D3) en lugar de rechazar: `ATTACHMENT_MAX_CHARS` compartido por turno;
  los topes de bytes y de expansión del zip siguen rechazando porque protegen el proceso.
- Cada fuente tiene su bloque en el presupuesto de tokens y su span.

## Alternativas descartadas

- **Camino A (Files API, multimodal):** sin código de extracción y lee diagramas, pero ata el
  wrapper a un proveedor, cuesta más tokens y es todo o nada. Se probó contra el proxy: un PDF
  viaja, un `.docx` no.
- **Rechazar adjuntos largos:** protege el prompt pero obliga al usuario a recortar a mano.
- **Implementar los dos caminos:** el material lo desaconseja; es una decisión, no una suma.

## Consecuencias

- Se pierde lo visual (diagramas, PDF escaneados: se rechazan como «sin texto»).
- La extracción es la primera pieza del chunking del módulo de RAG.

## Revisar cuando

Lleguen PDF escaneados o con diagramas críticos: Docling o MarkItDown, sin cambiar el contrato.
