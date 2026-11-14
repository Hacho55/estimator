"""Punto de entrada. Conecta las piezas y no contiene lógica de estimación."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.attachments.upload_limit import UploadSizeLimit
from app.config import APP_VERSION, get_settings
from app.observability import flush_langfuse, setup_observability
from app.routers import estimations, estimations_v2, sessions
from app.schemas.estimation import ErrorResponse
from app.services.errors import ServiceError

VERSION = APP_VERSION


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    flush_langfuse()  # Observabilidad: manda las trazas pendientes al cerrar.


app = FastAPI(
    title="Estimador CAG",
    description="Sistema de estimación de software con arquitectura CAG",
    version=VERSION,
    lifespan=lifespan,
)
setup_observability(app)  # Observabilidad: logs JSON y cabecera x-request-id.
# Sesión 5 · adjuntos: corta el multipart mientras llega, antes del spool a disco.
# Va antes que CORS para que el 413 también lleve sus cabeceras.
app.add_middleware(UploadSizeLimit)
# Sesión 3 · front: Next puede caer en otro puerto si el 3000 está ocupado.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["content-type"],
)
app.include_router(estimations.router)
app.include_router(estimations_v2.router)
app.include_router(sessions.router)


@app.exception_handler(ServiceError)
async def handle_service_error(_request, exc: ServiceError) -> JSONResponse:
    body = ErrorResponse(error=exc.code, detail=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
    """Misma forma ErrorResponse que el resto de 422 del estimador."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
        msg = err.get("msg", "inválido")
        parts.append(f"{loc}: {msg}" if loc else str(msg))
    detail = "; ".join(parts) or "Datos de entrada inválidos."
    body = ErrorResponse(error="validation_error", detail=detail)
    return JSONResponse(status_code=422, content=body.model_dump())


@app.get("/health", operation_id="health")
async def health() -> dict:
    return {"status": "healthy", "version": VERSION, "model": get_settings().LLM_MODEL}
