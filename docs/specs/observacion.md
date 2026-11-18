# Observación por turno (stress test)

Un turno de `/sessions` deja **un único evento** `turn_observed` y lo devuelve en
`observation`: el CSV del stress test sale de una pasada, sin reconciliar logs sueltos.
`anchors_count` y `summary_chars` se refieren a la memoria (no hay anclas ni summary en
`/sessions`, ADR 0002).

| Cuando… | Entonces… | Test |
|---|---|---|
| termina un turno de sesión | se emite `turn_observed` con los 13 campos del ejercicio y la respuesta lo trae en `observation` | `tests/test_turn_observation.py::test_every_session_turn_emits_turn_observed_with_the_exercise_fields` |
| el turno trae un adjunto truncado | `attachments_total_chars` es lo que quedó tras el truncado y `attachments_truncated` lo marca | `tests/test_turn_observation.py::test_observation_reports_truncated_attachment_chars` |
| se repite un primer turno idéntico | `cache_hit_kind` es `exact` | `tests/test_turn_observation.py::test_repeated_first_turn_is_observed_as_exact_cache_hit` |
| ningún modelo del turno tiene precio | `cost_usd` es `None`, no 0 | `tests/test_turn_observation.py::test_unknown_price_is_observed_as_unknown_not_free` |

## Stress test (`evals/stress/`)

| Cuando… | Entonces… | Test |
|---|---|---|
| un turno supera el presupuesto de latencia o de coste | la métrica falla; igual al límite pasa; coste desconocido no pasa | `tests/test_stress_metrics.py::test_cost_budget_passes_fails_and_unknown_cost_does_not_pass` |
| un hecho retirado sigue en `memory_log` | la deriva lo da por ausente: solo cuenta la memoria vigente | `tests/test_stress_metrics.py::test_memory_drift_absent_fact_ignores_the_change_log` |
| un turno declara un hecho con la misma key que uno anterior | lo sustituye (pivote, contradicción) | `tests/test_stress_metrics.py::test_a_later_fact_with_the_same_key_supersedes_the_earlier_one` |
| el adjunto pasa de `ATTACHMENT_MAX_CHARS` | los marcadores que caen detrás del corte desaparecen | `tests/test_stress_metrics.py::test_attachment_sizes_are_calibrated_and_markers_follow_the_truncation` |
| se ejecuta el runner | una fila por turno con la observación y las métricas | `tests/test_stress_runner.py::test_runner_writes_one_row_per_turn_with_observation_and_metrics` |
| se reanuda una corrida cortada | se conservan las sesiones completas, las parciales pasan a `.discarded.csv` y su gasto sigue contando; no se mezcla otro tier | `tests/test_stress_runner.py::test_resume_keeps_complete_sessions_and_still_counts_the_discarded_spend` |
| el gasto llega a `--max-usd` | la corrida se para antes del siguiente turno | `tests/test_stress_runner.py::test_runner_stops_between_turns_when_the_cap_is_crossed` |
| un turno no tiene coste conocido | la corrida se para salvo `--allow-unknown-cost` | `tests/test_stress_runner.py::test_unknown_cost_stops_the_run_unless_allowed` |
| el servidor resuelve otro tier que el pedido | la fila queda como `tier_mismatch` y la sesión se corta | `tests/test_stress_runner.py::test_a_tier_other_than_requested_invalidates_the_row` |
| falla `GET /sessions/{id}` | la deriva queda vacía con el error, no como memoria perdida | `tests/test_stress_runner.py::test_failed_snapshot_leaves_drift_empty_instead_of_lost` |

## Segunda corrida: el contexto al límite (`evals/stress/context_run.py`)

| Cuando… | Entonces… | Test |
|---|---|---|
| el corpus del CAG crece por encima de `CONTEXT_MAX_EXAMPLES` | entran los primeros por orden de la lista, no los pertinentes; sin topes entra todo | `tests/test_stress_context.py::test_custom_corpus_reaches_the_cag_block_with_and_without_caps` |
| se mueve la aguja de posición | los distractores no cambian; el control es el mismo corpus sin aguja | `tests/test_stress_context.py::test_corpus_has_the_size_and_moves_only_the_needle` |
| se corre el bloque C | el control sin aguja fija las horas de la aguja y cada fila dice si entró y si se citó | `tests/test_stress_context.py::test_block_c_calibrates_the_needle_on_the_control_and_resumes` |
| se corre el bloque B | el recall se mide sobre la respuesta con denominador en los marcadores que el actor pudo ver; la observación trae `actor_input_tokens` | `tests/test_stress_context.py::test_block_b_measures_recall_over_the_markers_the_actor_could_see` |
| la petición no cabe en la ventana configurada | rechazo local (413), sin coste; un timeout o un 5xx sí cuentan como posible gasto | `tests/test_stress_context.py::test_a_local_rejection_is_not_counted_as_paid` |
