"""Platform productization APIs: RBAC catalogs and read-only aggregate projections."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from app.auth.rbac import (
    PERMISSIONS,
    ROLES,
    PermissionRead,
    RoleRead,
    UserPrincipal,
    UserRead,
    list_users,
    require_permission,
)
from app.database import get_db_session
from app.schemas.productization import (
    ApprovalCenterItem,
    AuditPageRead,
    DashboardRead,
    PluginInventoryItem,
    SettingsRead,
)
from app.services.productization import ProductizationService

router = APIRouter(tags=["productization"])


def _permission(name: str):
    return Depends(require_permission(name))


@router.get("/roles", response_model=list[RoleRead])
async def roles(_: UserPrincipal = _permission("rbac.read")) -> list[RoleRead]:
    return [ROLES[name] for name in sorted(ROLES)]


@router.get("/permissions", response_model=list[PermissionRead])
async def permissions(_: UserPrincipal = _permission("rbac.read")) -> list[PermissionRead]:
    return list(PERMISSIONS)


@router.get("/users", response_model=list[UserRead])
async def users(_: UserPrincipal = _permission("rbac.read")) -> list[UserRead]:
    return list(list_users())


@router.get("/dashboard", response_model=DashboardRead)
async def dashboard(
    _: UserPrincipal = _permission("dashboard.read"),
    session=Depends(get_db_session),
) -> DashboardRead:
    return await ProductizationService(session).dashboard()


@router.get("/audit", response_model=AuditPageRead)
async def audit(
    _: UserPrincipal = _permission("audit.read"),
    operator: str | None = None,
    event_type: str | None = None,
    resource: str | None = None,
    plugin: str | None = None,
    incident: str | None = None,
    worker: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    session=Depends(get_db_session),
) -> AuditPageRead:
    return await ProductizationService(session).audit(
        operator=operator,
        event_type=event_type,
        resource=resource,
        plugin=plugin,
        incident=incident,
        worker=worker,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )


@router.get("/plugins", response_model=list[PluginInventoryItem])
async def plugins(
    _: UserPrincipal = _permission("plugin.read"),
    session=Depends(get_db_session),
) -> list[PluginInventoryItem]:
    return await ProductizationService(session).plugins()


@router.get("/approvals", response_model=list[ApprovalCenterItem])
async def approvals(
    _: UserPrincipal = _permission("approval.read"),
    session=Depends(get_db_session),
) -> list[ApprovalCenterItem]:
    return await ProductizationService(session).approvals()


@router.get("/settings", response_model=SettingsRead)
async def settings(
    request: Request,
    _: UserPrincipal = _permission("settings.read"),
) -> SettingsRead:
    current = request.app.state.settings
    return SettingsRead(
        app_name=current.app_name,
        app_version=current.app_version,
        api_prefix=current.api_prefix,
        debug=current.debug,
        log_level=current.log_level,
        cors_origins=current.cors_origins,
        database_driver=current.database_url.split(":", 1)[0],
        redis_configured=bool(current.redis_url),
        rbac_enabled=True,
        identity_header=current.rbac_identity_header,
        trusted_proxy_header=current.rbac_trusted_proxy_header,
        metrics_enabled=current.metrics_enabled,
        tracing_enabled=current.tracing_enabled,
        otel_service_name=current.otel_service_name,
        otel_exporter_endpoint_configured=bool(current.otel_exporter_endpoint),
    )
