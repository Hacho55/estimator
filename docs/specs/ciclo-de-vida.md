# Ciclo de vida de la sesión

| Cuando… | Entonces… | Test |
|---|---|---|
| una sesión lleva 24 h sin actividad | se archiva (no se borra) | `tests/test_session_lifecycle.py::test_idle_session_is_archived_not_deleted` |
| llega un turno a una sesión archivada | 409 `session_archived` con las opciones | `tests/test_session_lifecycle.py::test_turn_on_an_archived_session_is_409` |
| pasa la retención | la archivada se purga, contando desde el plazo | `tests/test_session_lifecycle.py::test_inactivity_archive_counts_from_the_deadline` |
| `POST /sessions` con `previous_session_id` | la anterior queda archivada; con `inherit_memory`, la nueva hereda los hechos sin historial | `tests/test_session_lifecycle.py::test_reset_archives_the_previous_and_can_inherit_memory` |
| se consulta `GET /sessions/{id}` | estado, memoria y registro, sin el texto del historial | `tests/test_session_lifecycle.py::test_get_session_is_auditable_without_history_text` |
| se serializa una sesión | historial y memoria van por separado | `tests/test_session_lifecycle.py::test_record_serializes_history_and_memory_separately` |
| el cupo está lleno | se purgan antes las archivadas; nunca una con un turno en vuelo | `tests/test_session_lifecycle.py::test_capacity_purges_archived_before_active` |
| un turno espera el lock y la sesión se resetea | el turno no corre | `tests/test_sessions.py::test_queued_turn_on_a_reset_session_is_rejected` |
