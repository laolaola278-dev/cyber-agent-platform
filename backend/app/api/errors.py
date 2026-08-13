"""HTTP translation and audit capture for application failures."""

from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.events import EventType, PlatformEvent
from app.events.audit import AuditSubscriber
from app.events.bus import InMemoryEventBus
from app.exceptions import PlatformError
from app.repositories import AuditRepository
from app.services.audit import AuditService


async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    """Render one stable error envelope for all platform-layer failures."""

    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Audit request validation failures before returning the stable error envelope."""

    details = {"errors": jsonable_encoder(exc.errors())}
    await _audit_http_error(
        request,
        event_type=EventType.VALIDATION_ERROR,
        error="Request validation failed",
        details=details,
    )
    return _error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request validation failed",
        details=details,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Audit unexpected failures without exposing implementation details."""

    await _audit_http_error(
        request,
        event_type=EventType.UNHANDLED_EXCEPTION,
        error=str(exc),
        details={"exception_type": type(exc).__name__},
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected platform error occurred",
        details={},
    )


async def _audit_http_error(
    request: Request,
    *,
    event_type: EventType,
    error: str,
    details: dict[str, Any],
) -> None:
    session_factory = request.app.state.audit_session_factory
    async with session_factory() as session:
        bus = InMemoryEventBus()
        AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
        resource_id = _path_uuid(request)
        await bus.publish(
            PlatformEvent(
                type=event_type,
                trace_id=getattr(request.state, "request_id", "-"),
                aggregate_id=resource_id,
                actor="api-user",
                resource=f"http:{request.method}:{request.url.path}",
                payload=details,
                error=error,
            )
        )
        await session.commit()


def _path_uuid(request: Request) -> UUID | None:
    for value in request.path_params.values():
        try:
            return UUID(str(value))
        except ValueError:
            continue
    return None


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "trace_id": getattr(request.state, "request_id", "-"),
            }
        },
    )
