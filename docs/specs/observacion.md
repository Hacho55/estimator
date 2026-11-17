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
