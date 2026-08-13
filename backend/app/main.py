"""FastAPI application factory and ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.api.errors import (
    platform_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.config import ConfigurationProvider, Settings, get_settings
from app.database import AsyncSessionFactory
from app.exceptions import PlatformError
from app.logging import configure_logging
from app.middleware import AuthorizationMiddleware, ObservabilityMiddleware, RequestIDMiddleware
from app.observability import MetricsRegistry, build_tracer
from app.sandbox import MemorySandboxProvider, MemorySecretProvider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated FastAPI application instance."""

    runtime_settings = settings or get_settings()
    configuration_provider = ConfigurationProvider(runtime_settings.config_directory)
    configuration_provider.load()
    configure_logging(configuration_provider.logging)
    sandbox_provider = MemorySandboxProvider()
    secret_provider = MemorySecretProvider()
    metrics_registry = MetricsRegistry()
    tracer = build_tracer(
        runtime_settings.otel_service_name,
        runtime_settings.otel_exporter_endpoint,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.configuration_provider = configuration_provider
        application.state.audit_session_factory = AsyncSessionFactory
        application.state.sandbox_provider = sandbox_provider
        application.state.secret_provider = secret_provider
        yield

    application = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        description=(
            "Control-plane API for Agent registration, task scheduling, permissions, "
            "and governance."
        ),
        docs_url="/docs" if runtime_settings.api_docs_enabled else None,
        redoc_url="/redoc" if runtime_settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if runtime_settings.api_docs_enabled else None,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.metrics_registry = metrics_registry
    application.state.configuration_provider = configuration_provider
    application.state.audit_session_factory = AsyncSessionFactory
    application.state.sandbox_provider = sandbox_provider
    application.state.secret_provider = secret_provider
    application.add_exception_handler(PlatformError, platform_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.add_middleware(RequestIDMiddleware)
    application.add_middleware(AuthorizationMiddleware)
    application.add_middleware(
        ObservabilityMiddleware,
        metrics=metrics_registry,
        tracer=tracer,
        tracing_enabled=runtime_settings.tracing_enabled,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=runtime_settings.api_prefix)
    return application


app = create_app()
