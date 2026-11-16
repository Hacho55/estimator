# Perfiles (tiers) e identidad

| Cuando… | Entonces… | Test |
|---|---|---|
| el token dice `pm` | plantilla `system_pm.j2`, schema `pm_turn` y vista `pm` | `tests/test_tiers.py::test_token_tier_selects_template_schema_and_view` |
| el cuerpo trae `tier=executive` y el token `developer` | manda el token | `tests/test_tiers.py::test_tier_in_the_body_is_ignored` |
| el token falta, caduca, es de otra audiencia o trae un tier desconocido | 401 | `tests/test_tiers.py::test_bad_tokens_are_401` |
| `AUTH_MODE=off` | se usa `DEFAULT_TIER` | `tests/test_tiers.py::test_auth_off_uses_the_default_tier` |
| otro usuario usa un `session_id` ajeno | 404 | `tests/test_tiers.py::test_session_belongs_to_its_creator` |
| el modelo devuelve el borrador executive | el rango de coste lo calcula el código según la confianza | `tests/test_tiers.py::test_executive_view_ranges_follow_confidence` |
| el modelo pone tarifas o redondea mal | tarifa de la tabla y horas en múltiplos de 5 | `tests/test_tiers.py::test_rates_and_totals_come_from_the_rate_card_not_the_model` |
| llega un borrador de otro tier | no pasa la validación (dispara la reparación) | `tests/test_tiers.py::test_a_draft_from_another_tier_does_not_pass` |
| hay PII en un campo propio del tier | se tacha en vista, historial y cachés | `tests/test_tiers.py::test_history_and_cache_store_the_redacted_draft` |
| cambia `_rates.j2` | llega a las tres plantillas | `tests/test_tiers.py::test_templates_share_partials_and_differ_in_audience_and_contract` |
| el backend de negocio emite un token | el estimador lo acepta | `tests/test_business_backend.py::test_login_token_is_accepted_by_the_estimator` |
| se revisa el código del estimador | no importa `business_backend` | `tests/test_business_backend.py::test_the_estimator_never_imports_the_business_backend` |
