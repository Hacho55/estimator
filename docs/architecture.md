# Arquitectura de un turno de sesión

Cómo se atiende `POST /sessions/{id}/estimate` al cierre de la sesión 5. El porqué de cada
pieza está en [docs/decisions/](decisions/README.md) y su comportamiento esperado, con el test
que lo cubre, en [docs/specs/](specs/README.md).

## Recorrido

```mermaid
sequenceDiagram
    autonumber
    participant F as Front (Next)
    participant B as business_backend
    participant R as routers/sessions.py
    participant S as state/sessions.py + memory.py
    participant L as services/llm_service.py
    participant T as tools/ + research.py
    participant C as critic.py + boss.py
    participant M as llm_client.py (LiteLLM)

    F->>B: POST /api/auth/demo-login
    B-->>F: JWT {sub, tier, aud, exp}
    F->>R: multipart (transcript, adjuntos) + Bearer
    R->>R: security/caller.py valida el JWT → tier
    R->>S: get(session, owner=sub) · lock · ¿sigue activa?
    R->>R: attachments/ extrae texto (hilo aparte, cupo compartido)
    R->>L: generate(turno, historial, memoria, adjuntos, tier)
    L->>L: guardrails: PII e inyección; adjuntos como dato
    opt el tier declara herramientas y el turno da motivos
        L->>T: research.py: bucle de tool calls (≤ 3 vueltas, ≤ 4 invocaciones)
        T->>M: complete_with_tools
        T->>B: POST /api/internal/similar_projects (token de servicio)
        T-->>L: tool_results tachados (dato)
    end
    L->>M: actor: system del tier + memoria al final · user con adjuntos y tool_results
    L->>L: tier_views: motor común (tarifas, costes, totales) y vista del tier
    opt tier con review = acb (pm, executive)
        L->>C: propiedades deterministas + auditor LLM (critic/v1)
        C->>M: critic
        C-->>L: Boss: accept / iterate con critic_feedback (≤ 2 actores) / synthesize
    end
    L-->>R: vista, avisos, revisión, herramientas, uso y coste
    R->>M: extractor de memoria (delta, metadata/v2)
    R->>S: apply_update (fusión en código) + memory_log
    R->>S: historial += (turno con referencia a adjuntos, borrador corregido y tachado)
    R-->>F: respuesta (tier, vista, memoria, cambios, revisión, herramientas, uso, latencia)
```

## Dónde vive cada responsabilidad

| Capa | Archivo | Decide | No hace |
|---|---|---|---|
| HTTP | `app/routers/sessions.py` | Forma de la petición y la respuesta, orden del turno, adjuntos y memoria | Prompt ni llamadas al modelo |
| Identidad | `app/security/caller.py` | Quién llama y con qué tier | Leer el tier del cuerpo |
| Perfil | `app/tiers.py` | Plantilla, schemas, herramientas y revisión por tier | Lógica de negocio |
| Estado | `app/state/sessions.py`, `app/state/memory.py` | Historial, memoria, fusión, olvido, propiedad de la sesión | Llamar al modelo |
| Orquestación | `app/services/llm_service.py`, `review_loop.py`, `research.py` | Pasos del turno, cachés, presupuesto, topes | HTTP |
| Prompt | `app/prompts/estimation/v4/`, `app/services/prompt_builder.py` | Texto por tier, segmentos de caché, memoria al final | Datos de negocio |
| Herramientas | `app/tools/`, `app/services/tool_policy.py` | Qué se ofrece y cómo se ejecuta | Acceder a la BBDD del negocio |
| Revisión | `app/services/critic.py`, `app/services/boss.py` | Issues estructurados; aceptar, iterar o sintetizar | Reescribir la estimación |
| Validación | `app/services/postprocessor.py`, `app/services/tier_views.py` | Aritmética, tarifas, rangos y vista por tier | Juicio del modelo |
| Guardrails | `app/services/guardrails.py` | PII e inyección en entrada, contexto dinámico y salida | Rechazar datos externos por una frase |
| Modelo | `app/services/llm_client.py` | Única salida hacia LiteLLM (también *function calling*) | Decidir nada del producto |
| Negocio | `business_backend/` | Usuarios, tier, proyectos históricos y qué es «similar» | Nada del prompt |

## Spans de una traza

Una traza por turno, con nombres estables para filtrar en Langfuse:

`extract-attachment` (uno por archivo) · `build-context` · `build-prompt` · `research`
(`research-N` por vuelta, `tool:<nombre>` por invocación) · `generate-estimate` (`actor-N` en
la revisión, `repair-estimate` si hubo que reparar el JSON) · `postprocess` · `critic-N` ·
`boss-N` · `extract-metadata`.

## Topes y coste por turno

| Tier | Llamadas típicas | Máximas | Medido (landing, 2026-11-16) |
|---|---|---|---|
| developer | 2 | 6 | 105 s, 0,054 USD |
| pm / executive | 3 | 10 | 107 s, 0,069 USD (pm) |

Los objetivos del plan eran < 12 s (developer) y < 25 s (pm). La primera medición los supera
con creces porque el actor genera unos 5.000 tokens de salida: es lo primero que hay que
revisar (`docs/evals.md`).
