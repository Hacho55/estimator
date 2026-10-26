# Conclusiones del experimento de contexto

Este es el documento que hay que revisar. Resume la corrida del 26 de octubre de 2026 y la estrategia que queda elegida. El README apunta aquí.

La orden fue `uv run python scripts/run_experiment.py --name contexto`. Son 8 variantes y 5 transcripciones, 40 llamadas. No se publicó en Langfuse: esa pasada no llevó `--langfuse`.

## Estrategia elegida

Baseline, con la caché del prompt encendida.

Eso es markdown, detalle `standard`, los cinco ejemplos vigentes y `openrouter/anthropic/claude-sonnet-5.5`. En configuración: `CONTEXT_FORMAT=markdown`, `CONTEXT_DETAIL_LEVEL=standard`, `LLM_PROMPT_CACHE=auto`.

Las ocho variantes estiman dentro del rango de horas de los cinco casos, y las ocho devuelven 0 horas en el caso que no es un proyecto de software. El rango esperado es ancho, sobre todo en los requisitos vagos (20 a 500 horas), así que no separa la calidad. Lo que sí se mide es el coste de repetir la llamada.

Apagar la caché usa el mismo prompt que baseline y la suma de los cinco casos pasa de 0,124 USD a 0,160 USD. Desde la segunda llamada, baseline reutiliza 4.912 tokens. JSON agranda el bloque a 1.571 tokens y sube la app móvil a 330 horas, el valor más alto de esa fila. Tres ejemplos dejan el bloque en 771 tokens y suben el caso vago a 300 horas. Haiku cuesta 0,042 USD, el caso fuera de alcance necesitó un reintento de JSON y la primera llamada tardó 79 segundos.

## Cómo está puntuado

Hay que distinguir tres capas. Solo la tercera es la comparación con una respuesta esperada, y esa comparación se hizo al leer la tabla, no la escribió el script.

1. Cada traza, si Langfuse está encendido, recibe los scores de `app/evaluation/metrics.py`: JSON válido a la primera, avisos, correcciones, latencia, tokens, ratio de caché, coste, horas múltiplo de 5, tarifas dentro de la tabla y secciones completas.
2. El informe de la corrida guarda medidas en bruto: `json_valid_first_try`, `warnings`, `total_hours`, `cost_usd`, `latency_ms` y `cached_tokens`. No llama a `collect_metrics`.
3. El rango de horas y `expected_estimable` viven en la cabecera de `data/transcripciones/`. `hours_in_expected_range` e `is_estimable_matches_expected` existen en `collect_metrics`, y esta corrida no los calculó: `generate_estimation` no recibe el rango. La columna «en rango» de la tabla de abajo sale de comparar `total_hours` con esa cabecera.

Rangos usados:

| Caso | Horas | Estimable |
| --- | --- | --- |
| 01 landing HubSpot | 40–250 | sí |
| 02 app móvil de reservas | 80–400 | sí |
| 03 integración ERP | 60–300 | sí |
| 04 requisitos vagos | 20–500 | sí |
| 05 fuera de alcance | 0 | no |

## Resumen por variante

Coste sumado de los cinco casos. «Caché» es `cached_tokens` a partir de la segunda llamada de esa variante.

| Variante | En rango | JSON a la primera | Avisos | Coste USD | Caché | Lectura |
| --- | --- | --- | --- | ---: | ---: | --- |
| baseline | 5/5 | 5/5 | 5 | 0,124 | 4.912 | Punto de operación |
| format-plain | 5/5 | 5/5 | 4 | 0,123 | 4.740 | Mismo resultado, 1.160 tokens de contexto |
| detail-summary | 5/5 | 5/5 | 4 | 0,124 | 4.383 | Estima más bajo y sigue dentro del rango |
| detail-full | 5/5 | 5/5 | 5 | 0,121 | 5.238 | Más detalle, sin salir del rango |
| examples-3 | 5/5 | 5/5 | 4 | 0,123 | 4.014 | Bloque más corto; el caso vago sube a 300 h |
| format-json | 5/5 | 5/5 | 5 | 0,134 | 5.289 | Bloque más grande; la app móvil sube a 330 h |
| cache-off | 5/5 | 5/5 | 4 | 0,160 | 0 | Misma calidad que baseline y más cara |
| model-haiku | 5/5 | 4/5 | 5 | 0,042 | 0 | La más barata; un reintento y sin caché observada |

## Tabla completa de la corrida

Copia de la corrida local `experiments/results/20261026T154927Z_contexto.md`. Esa carpeta está en `.gitignore`; esta tabla es la que queda en el repositorio.

| variant | case | context_tokens | examples | detail | format | model | json_valid_first_try | warnings | total_hours | cost_usd | latency_ms | cached_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 01_landing_hubspot | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 185.0 | 0.04335 | 21800.8 | 0 |
| baseline | 02_app_movil_reservas | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 270.0 | 0.0270152 | 18403.8 | 4912 |
| baseline | 03_integracion_erp | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 135.0 | 0.0174292 | 10793.2 | 4912 |
| baseline | 04_requisitos_vagos | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 275.0 | 0.0314732 | 20086.8 | 4912 |
| baseline | 05_fuera_de_alcance | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.0050892 | 3204.4 | 4912 |
| format-plain | 01_landing_hubspot | 1160 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | plain | openrouter/anthropic/claude-sonnet-5.5 | 1 | 0 | 195.0 | 0.04251 | 20881.0 | 0 |
| format-plain | 02_app_movil_reservas | 1160 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | plain | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 240.0 | 0.027038 | 17967.9 | 4740 |
| format-plain | 03_integracion_erp | 1160 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | plain | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 125.0 | 0.018142 | 12116.6 | 4740 |
| format-plain | 04_requisitos_vagos | 1160 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | plain | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 230.0 | 0.029586 | 19261.9 | 4740 |
| format-plain | 05_fuera_de_alcance | 1160 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | plain | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.005322 | 3987.8 | 4740 |
| format-json | 01_landing_hubspot | 1571 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | json | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 190.0 | 0.0452525 | 22038.7 | 0 |
| format-json | 02_app_movil_reservas | 1571 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | json | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 330.0 | 0.0319129 | 21669.3 | 5289 |
| format-json | 03_integracion_erp | 1571 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | json | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 140.0 | 0.0209769 | 14386.3 | 5289 |
| format-json | 04_requisitos_vagos | 1571 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | json | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 210.0 | 0.0310709 | 21993.4 | 5289 |
| format-json | 05_fuera_de_alcance | 1571 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | json | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.0045569 | 2957.5 | 5289 |
| detail-summary | 01_landing_hubspot | 954 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | summary | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 0 | 160.0 | 0.0408375 | 20351.8 | 0 |
| detail-summary | 02_app_movil_reservas | 954 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | summary | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 210.0 | 0.0284923 | 17839.9 | 4383 |
| detail-summary | 03_integracion_erp | 954 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | summary | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 125.0 | 0.0242263 | 16323.6 | 4383 |
| detail-summary | 04_requisitos_vagos | 954 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | summary | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 165.0 | 0.0266003 | 17742.4 | 4383 |
| detail-summary | 05_fuera_de_alcance | 954 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | summary | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.0043163 | 3445.5 | 4383 |
| detail-full | 01_landing_hubspot | 1509 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | full | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 165.0 | 0.038325 | 17849.6 | 0 |
| detail-full | 02_app_movil_reservas | 1509 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | full | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 325.0 | 0.0300678 | 20433.5 | 5238 |
| detail-full | 03_integracion_erp | 1509 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | full | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 125.0 | 0.0184818 | 12629.6 | 5238 |
| detail-full | 04_requisitos_vagos | 1509 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | full | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 225.0 | 0.0276358 | 17620.6 | 5238 |
| detail-full | 05_fuera_de_alcance | 1509 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | full | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.0065118 | 4669.2 | 5238 |
| examples-3 | 01_landing_hubspot | 771 | inv-2024,reservas-2025,erp-2024 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 0 | 205.0 | 0.040225 | 20224.6 | 0 |
| examples-3 | 02_app_movil_reservas | 771 | inv-2024,reservas-2025,erp-2024 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 295.0 | 0.0269254 | 17050.5 | 4014 |
| examples-3 | 03_integracion_erp | 771 | inv-2024,reservas-2025,erp-2024 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 130.0 | 0.0206194 | 14183.9 | 4014 |
| examples-3 | 04_requisitos_vagos | 771 | inv-2024,reservas-2025,erp-2024 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 300.0 | 0.0308534 | 19654.7 | 4014 |
| examples-3 | 05_fuera_de_alcance | 771 | inv-2024,reservas-2025,erp-2024 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.0043994 | 2823.6 | 4014 |
| cache-off | 01_landing_hubspot | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 0 | 175.0 | 0.040432 | 20257.4 | 0 |
| cache-off | 02_app_movil_reservas | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 265.0 | 0.040596 | 21027.0 | 0 |
| cache-off | 03_integracion_erp | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 125.0 | 0.02735 | 12701.5 | 0 |
| cache-off | 04_requisitos_vagos | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 225.0 | 0.037564 | 18572.3 | 0 |
| cache-off | 05_fuera_de_alcance | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-sonnet-5.5 | 1 | 1 | 0.0 | 0.01425 | 3463.3 | 0 |
| model-haiku | 01_landing_hubspot | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-haiku-4.5 | 1 | 1 | 115.0 | 0.007678 | 79492.3 | 0 |
| model-haiku | 02_app_movil_reservas | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-haiku-4.5 | 1 | 1 | 240.0 | 0.008233 | 21147.3 | 0 |
| model-haiku | 03_integracion_erp | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-haiku-4.5 | 1 | 1 | 125.0 | 0.007502 | 18431.2 | 0 |
| model-haiku | 04_requisitos_vagos | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-haiku-4.5 | 1 | 1 | 180.0 | 0.008461 | 20798.6 | 0 |
| model-haiku | 05_fuera_de_alcance | 1265 | inv-2024,reservas-2025,erp-2024,rrhh-2023,ecom-2025 | standard | markdown | openrouter/anthropic/claude-haiku-4.5 | 0 | 1 | 0.0 | 0.009769 | 25366.1 | 0 |
