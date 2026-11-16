# Evaluación

| Cuando… | Entonces… | Test |
|---|---|---|
| una salida buena de cada tier | pasa todas las propiedades online | `tests/test_properties.py::test_a_good_estimation_passes_every_online_property` |
| un riesgo menciona una opción rechazada | no cuenta como proponerla | `tests/test_properties.py::test_rejecting_an_option_in_a_risk_is_not_a_proposal` |
| hay tier | las propiedades se publican como `prop_<nombre>` | `tests/test_properties.py::test_properties_become_online_scores` |
| (modelo real) cada caso y tier del golden dataset | pasa las propiedades; en borradores, los umbrales numéricos avisan | `evals/test_hard.py::test_properties_hold` |
| (modelo real) tres ejecuciones del mismo caso | todas estiman y el CV es < 0,25 | `evals/test_soft.py::test_estimates_are_consistent_across_runs` |
| (modelo real) juez calibrado | coherencia de alcance y adecuación al tier ≥ 0,4 | `evals/test_judge.py::test_judge` |
