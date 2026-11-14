# Arquitectura de un turno de sesión

> **Estado: objetivo de la sesión 5.** Describe el turno tal como queda al
> terminar [el plan](plan-session-5.md). Hasta entonces, las piezas marcadas
> con ★ todavía no existen. En el paso 10 este documento pasa a describir lo
> construido.

## Recorrido de `POST /sessions/{id}/estimate`

```mermaid
sequenceDiagram
    autonumber
    participant F as Front (Next)
    participant B as business_backend
    participant R as routers/sessions.py
    participant S as state/sessions.py + memory.py
    participant L as services/llm_service.py
    participant T as tools/ ★
    participant C as critic.py + boss.py ★
    participant M as llm_client.py (LiteLLM)

    F->>B: demo-login ★
    B-->>F: JWT {sub, tier} ★
    F->>R: multipart (transcript, adjuntos) + Bearer
    R->>R: security/caller.py valida el JWT → tier ★
    R->>R: attachments/ extrae texto (hilo aparte)
    R->>S: get(session) · lock
    R->>L: generate(turno, historial, memoria, tier)
    L->>L: guard_input (PII) · tiers.py → plantilla, schema, pasos ★
    opt tier.research ★
        L->>T: research.py: bucle de tool calls (≤ 3)
        T->>M: complete_with_tools
        T->>B: POST /api/internal/similar_projects
        T-->>L: tool_results (datos)
    end
    L->>M: actor (system por tier + memoria al final, user con adjuntos y tool_results)
    L->>L: postprocessor: aritmética, tarifas, build_view(tier) ★
    opt tier.review = acb ★
        L->>C: propiedades deterministas + crítico LLM
        C->>M: critic (CriticFeedback)
        C-->>L: Boss: accept / iterate (≤ 2) / synthesize
    end
    L-->>R: vista del tier, warnings, review, uso
    R->>M: extractor de metadatos (delta) ★
    R->>S: apply_update (fusión en código) + registro de cambios ★
    R->>S: historial += (turno con referencia a adjuntos, salida corregida)
    R-->>F: respuesta (tier, vista, memoria, review, uso, latencia)
```

## Dónde vive cada responsabilidad

| Capa | Archivo | Decide | No hace |
|---|---|---|---|
| HTTP | `app/routers/sessions.py` | Forma de la petición y la respuesta, orden del turno | Prompt ni llamadas al modelo |
| Identidad ★ | `app/security/caller.py` | Quién llama y con qué tier | Leer el tier del cuerpo |
| Perfil ★ | `app/tiers.py` | Plantilla, schemas, herramientas y revisión por tier | Lógica de negocio |
| Estado | `app/state/sessions.py`, `app/state/memory.py` ★ | Historial, memoria, fusión, olvido | Llamar al modelo |
| Orquestación | `app/services/llm_service.py`, `review_loop.py` ★ | Pasos del turno, cachés, presupuesto | HTTP |
| Herramientas ★ | `app/tools/`, `app/services/research.py` | Qué herramienta se ejecuta y cómo | Acceder a la BBDD del negocio |
| Revisión ★ | `app/services/critic.py`, `app/services/boss.py` | Issues estructurados; aceptar, iterar o sintetizar | Reescribir la estimación |
| Validación | `app/services/postprocessor.py` | Aritmética, tarifas y vista por tier | Juicio del modelo |
| Modelo | `app/services/llm_client.py` | Única salida hacia LiteLLM | Decidir nada del producto |
| Negocio ★ | `business_backend/` | Usuarios, tier, proyectos históricos | Nada del prompt |

## Spans de una traza

Una traza por turno, con un span por paso. Los nombres son estables para
poder filtrar en Langfuse:

`extract-attachment` (uno por archivo) · `build-context` · `tool:web_search`
y `tool:find_similar_projects` ★ · `generate-estimate` (`actor-N` con
revisión) ★ · `postprocess` · `critic-N` ★ · `boss-N` ★ · `extract-metadata`.

## Topes por turno

Ver «Presupuesto por turno» en el plan: cuántas llamadas hace cada tier como
máximo y los objetivos de latencia que se miden en la evaluación.
