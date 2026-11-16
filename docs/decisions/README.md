# Decisiones de arquitectura

Una por decisión que cuesta revertir. Cada una dice el contexto, qué se decidió, qué se
descartó y cuándo revisarla. Las decisiones D1–D16 del plan (`docs/plan-session-5.md`) están
agrupadas aquí por tema.

| Nº | Decisión | Plan |
|---|---|---|
| [0001](0001-adjuntos-camino-b.md) | Adjuntos por extracción local (camino B) y como dato | D2, D3 |
| [0002](0002-historial-y-memoria.md) | Historial y memoria separados; memoria por delta | D6 |
| [0003](0003-tiers.md) | Perfiles con plantilla, schema y vista propios; tier firmado | D7, D8, D9, D15 |
| [0004](0004-evaluacion.md) | Propiedades, pirámide de evals y juez calibrado | D10, D11, D12 |
| [0005](0005-actor-critic-boss.md) | Actor-Critic-Boss con el Boss en código | D13, D14 |

Las herramientas (D1, D4, D5) están en 0001 (contexto dinámico) y en 0003 (backend de negocio).
