# Revisión Actor-Critic-Boss

| Cuando… | Entonces… | Test |
|---|---|---|
| el crítico acepta a la primera | 1 actor y 1 crítico | `tests/test_review.py::test_accepted_first_time_costs_one_actor_and_one_critic` |
| pide iteración | el segundo actor recibe `<critic_feedback>` | `tests/test_review.py::test_iteration_sends_critic_feedback_to_the_actor` |
| se agota el presupuesto | se entrega el mejor candidato con los issues en `warnings` | `tests/test_review.py::test_budget_exhausted_synthesizes_with_open_issues` |
| el crítico rechaza | se sintetiza sin iterar | `tests/test_review.py::test_reject_synthesizes_without_iterating` |
| el crítico falla | se acepta al actor con aviso, sin 5xx | `tests/test_review.py::test_failed_critic_accepts_with_a_notice` |
| una propiedad determinista falla | se itera aunque el crítico acepte | `tests/test_review.py::test_deterministic_issue_forces_iteration_even_if_the_critic_accepts` |
| el tier es developer o el turno es `answer` | no hay revisión | `tests/test_review.py::test_developer_tier_has_no_review`, `tests/test_review.py::test_answers_are_not_reviewed` |
| la revisión termina | solo se cachea una aceptación limpia, bajo la clave original | `tests/test_review.py::test_only_the_final_reviewed_output_is_cached` |
| el Boss decide | reglas fijas sobre veredicto y severidad | `tests/test_review.py::test_boss_rules` |
