# Herramientas

| Cuando… | Entonces… | Test |
|---|---|---|
| el tier no declara una herramienta, no está configurada o el turno no da motivos | no se ofrece | `tests/test_tools.py::test_policy_needs_tier_availability_and_a_reason` |
| no se ofrece ninguna | no hay llamada de investigación | `tests/test_tools.py::test_no_tools_offered_means_no_research_call` |
| una herramienta falla, tarda o recibe argumentos inválidos | resultado con `error`, la estimación sigue | `tests/test_tools.py::test_research_turns_failures_into_results` |
| el modelo pide más invocaciones que el tope | se cortan | `tests/test_tools.py::test_research_respects_the_call_cap` |
| hay resultados | entran en `<tool_results>` y el turno no se cachea | `tests/test_tools.py::test_tool_results_reach_the_actor_as_data_and_are_not_cached` |
| un resultado trae PII | se tacha antes de volver a cualquier modelo | `tests/test_tools.py::test_tool_output_pii_is_redacted_before_the_next_research_call` |
| `find_similar_projects` se ejecuta | habla con el backend por HTTP y no ve su esquema | `tests/test_tools.py::test_similar_projects_talks_to_the_real_backend_over_http` |
