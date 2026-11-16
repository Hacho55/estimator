# 0005 · Actor-Critic-Boss con el Boss en código

**Estado:** aceptada (sesión 5, fase 5).

## Contexto

Hay un techo de calidad que no se rompe refinando el prompt: el fallo es de verificación, no de
instrucción. Generar y verificar en la misma llamada degrada las dos cosas.

## Decisión

- **Tres roles.** Actor (la estimación de siempre), Critic (evalúa, no reescribe) y Boss (decide).
- **Crítico en dos capas.** Primero las propiedades deterministas, sin llamada; después un
  auditor LLM (`critic/v1`) con issues estructurados (categoría, severidad, campo) solo para lo
  que el código no ve: alcance, componentes, invenciones, memoria, riesgos y audiencia.
- **Boss en código** (D13): acepta sin bloqueantes, itera con `<critic_feedback>` si queda
  presupuesto, sintetiza el mejor candidato con los issues abiertos en `warnings`. Tope
  `BOSS_MAX_ITERATIONS = 2`.
- **Activación por tier** (D14): pm y executive; developer no. Solo en estimaciones nuevas.
- **Caché solo de aceptaciones limpias**, bajo la clave del turno original.

## Alternativas descartadas

- **Boss LLM con prompt de gobernanza:** una llamada más, más difícil de probar y con riesgo de
  sesgo; las reglas de decisión caben en código.
- **Crítico que decide (dos roles):** bucles por insatisfacción o aceptación temprana.
- **Revisión en todos los tiers:** triplica coste y latencia donde el usuario revisa por sí mismo.

## Consecuencias

Hasta 2 actores y 2 críticos por turno en pm y executive. La activación por tier es una
hipótesis: se confirma con `scripts/run_experiment.py --variant tier-` (docs/evals.md).

## Revisar cuando

Las mediciones de las variantes `tier-*` muestren que la revisión no mejora propiedades ni juez
en un tier, o que su latencia no compensa.
