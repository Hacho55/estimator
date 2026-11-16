# Contexto dinámico: adjuntos y prompt v4

| Cuando… | Entonces… | Test |
|---|---|---|
| un turno trae un PDF | el texto va en `<attachment>` del mensaje y el historial guarda solo la referencia | `tests/test_sessions.py::test_attachment_goes_to_the_model_once_and_history_keeps_a_reference` |
| el texto supera `ATTACHMENT_MAX_CHARS` | se trunca y aparece `attachment_truncated` | `tests/test_sessions.py::test_truncated_attachment_is_announced` |
| se agota el cupo con varios adjuntos | los siguientes salen truncados con 0 caracteres, sin rechazar | `tests/test_sessions.py::test_exhausted_budget_reports_later_attachments_as_truncated` |
| el primer turno tiene 20 caracteres y un adjunto con texto | se acepta | `tests/test_sessions.py::test_short_first_turn_is_accepted_with_an_attachment` |
| un adjunto trae PII o una inyección | la PII se tacha y la inyección no rechaza el turno | `tests/test_sessions.py::test_attachment_pii_is_redacted_and_injection_does_not_reject` |
| el nombre del archivo contiene un email | no llega ni al modelo ni al historial | `tests/test_sessions.py::test_attachment_filename_pii_never_reaches_model_or_history` |
| un texto de cliente trae `</project_description>` | la etiqueta se neutraliza | `tests/prompts/test_estimation_v4.py::test_block_tags_inside_data_are_neutralized` |
| hay contexto dinámico | cada fuente tiene su bloque en el presupuesto | `tests/test_sessions.py::test_budget_has_a_block_per_dynamic_source` |
| la transcripción es corta pero el adjunto largo | se elige el modelo potente | `tests/test_sessions.py::test_long_attachment_routes_to_the_strong_model` |
| un PDF solo tiene imágenes | se rechaza como «sin texto» | `tests/test_attachments.py::test_pdf_without_text_is_rejected_despite_page_markers` |
| (modelo real) un adjunto pide ignorar instrucciones | la salida no lo obedece | `evals/test_hard.py::test_injected_text_does_not_leak` |
