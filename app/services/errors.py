"""Errores de servicio. El router no los traduce: main.py tiene un solo manejador."""


class ServiceError(Exception):
    status_code = 500
    code = "service_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class LLMUnavailableError(ServiceError):
    status_code = 502
    code = "llm_unavailable"


class LLMTimeoutError(ServiceError):
    status_code = 504
    code = "llm_timeout"


class InvalidLLMOutputError(ServiceError):
    status_code = 502
    code = "invalid_llm_output"


class LLMOutputTruncatedError(ServiceError):
    status_code = 502
    code = "llm_output_truncated"

    def __init__(self) -> None:
        super().__init__(
            "La respuesta del modelo se cortó porque alcanzó LLM_MAX_TOKENS. "
            "Sube el límite o pide una estimación más corta."
        )


class ContextBudgetExceededError(ServiceError):
    status_code = 413
    code = "context_budget_exceeded"


class AuthenticationError(ServiceError):
    status_code = 401
    code = "unauthorized"


class SessionNotFoundError(ServiceError):
    status_code = 404
    code = "session_not_found"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"No existe la sesión {session_id}. Crea una nueva con POST /sessions.")


class SessionArchivedError(ServiceError):
    status_code = 409
    code = "session_archived"

    def __init__(self, session_id: str, reason: str) -> None:
        super().__init__(
            f"La sesión {session_id} está archivada ({reason}). Abre otra con POST /sessions: "
            "con inherit_memory=true conserva los hechos del proyecto; sin él, empieza de cero."
        )


class AttachmentRejectedError(ServiceError):
    status_code = 422
    code = "attachment_rejected"


class AttachmentTooLargeError(ServiceError):
    status_code = 413
    code = "attachment_too_large"


class ConversationNotFoundError(ServiceError):
    status_code = 404
    code = "conversation_not_found"

    def __init__(self, conversation_id: str) -> None:
        super().__init__(
            f"No existe la conversación {conversation_id} o ya expiró. "
            "Crea una nueva con POST /api/v1/conversations."
        )


class PromptVersionError(ServiceError):
    status_code = 422
    code = "unknown_prompt_version"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "No existe esa versión de prompt.")


class InputRejectedError(ServiceError):
    status_code = 422
    code = "input_rejected"
