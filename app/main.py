"""Punto de entrada. Conecta las piezas y no contiene lógica de estimación."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import APP_VERSION, get_settings
from app.observability import flush_langfuse, setup_observability
from app.routers import estimations
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
app.include_router(estimations.router)


@app.exception_handler(ServiceError)
async def handle_service_error(_request, exc: ServiceError) -> JSONResponse:
    body = ErrorResponse(error=exc.code, detail=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.get("/health", operation_id="health")
async def health() -> dict:
    return {"status": "healthy", "version": VERSION, "model": get_settings().LLM_MODEL}
