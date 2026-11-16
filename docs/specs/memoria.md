# Memoria de la sesión

| Cuando… | Entonces… | Test |
|---|---|---|
| una sesión llega al turno 8 y el turno 1 ya salió de la ventana | el nombre dicho en el turno 1 sigue en `<project_metadata>` y en la respuesta | `tests/test_sessions.py::test_fact_from_turn_one_survives_after_the_window_drops_it` |
| el usuario dice «ya no usamos Rails» | Rails sale de las tecnologías y entra en `rejected_options`, con su cambio auditado | `tests/test_sessions.py::test_user_revision_moves_technology_to_rejected_options` |
| el extractor devuelve `{}` | la memoria no cambia | `tests/test_sessions.py::test_empty_metadata_json_keeps_previous_facts` |
| el extractor falla | la memoria no cambia y su latencia se suma | `tests/test_sessions.py::test_failed_extraction_keeps_memory_and_counts_its_latency` |
| una lista de la memoria está llena | lo establecido se conserva y lo nuevo se descarta con motivo | `tests/test_memory.py::test_full_list_keeps_established_facts_and_reports_overflow` |
| el delta trae valores fuera de rango o contradictorios | se descartan antes de fusionar | `tests/test_memory.py::test_validation_discards_out_of_range_and_contradictions` |
| cambian los hechos entre dos turnos | `prompt_version` y el bloque cacheado no cambian | `tests/prompts/test_estimation_v4.py::test_prompt_version_ignores_the_memory` |
| no hay hechos | el bloque no aparece (ni «None» ni «null») | `tests/prompts/test_estimation_v4.py::test_memory_block_is_conditional_and_has_no_none` |
| pasan más turnos que la ventana | el turno de auditoría sigue contando | `tests/test_sessions.py::test_audit_turn_keeps_counting_past_the_window` |
| (modelo real) sesión de 8 turnos y revisión de Rails | la memoria final cumple lo esperado | `evals/test_hard.py::test_session_memory` |
