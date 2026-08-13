"""Platform-owned local RBAC catalog and trusted-header authentication boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    resource: str
    action: str
    description: str


class RoleRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    permissions: tuple[str, ...]


class UserRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str
    display_name: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    username: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]


_PERMISSION_SPECS = {
    "dashboard.read": ("dashboard", "read", "View platform aggregate dashboard"),
    "asset.read": ("asset", "read", "View assets"),
    "asset.write": ("asset", "write", "Create or modify assets"),
    "knowledge.read": ("knowledge", "read", "View knowledge"),
    "knowledge.write": ("knowledge", "write", "Import knowledge"),
    "evidence.read": ("evidence", "read", "View evidence"),
    "assessment.read": ("assessment", "read", "View assessments and findings"),
    "assessment.execute": ("assessment", "execute", "Run assessments"),
    "detection.read": ("detection", "read", "View detections and events"),
    "detection.execute": ("detection", "execute", "Run detections"),
    "incident.read": ("incident", "read", "View incidents"),
    "incident.write": ("incident", "write", "Create or update incidents"),
    "response.read": ("response", "read", "View response plans"),
    "response.plan": ("response", "plan", "Create response plans"),
    "response.execute": ("response", "execute", "Execute approved responses"),
    "response.rollback": ("response", "rollback", "Rollback responses"),
    "playbook.read": ("playbook", "read", "View playbooks and execution history"),
    "playbook.write": ("playbook", "write", "Create immutable playbook versions"),
    "playbook.execute": ("playbook", "execute", "Run or resume playbooks"),
    "worker.read": ("worker", "read", "View workers"),
    "sandbox.read": ("sandbox", "read", "View sandbox executions"),
    "plugin.read": ("plugin", "read", "View plugin inventory and health"),
    "approval.read": ("approval", "read", "View approval decisions"),
    "approval.decide": ("approval", "decide", "Approve or reject protected actions"),
    "notification.read": ("notification", "read", "View notification delivery"),
    "notification.send": ("notification", "send", "Create governed notifications"),
    "ticket.read": ("ticket", "read", "View tickets"),
    "ticket.write": ("ticket", "write", "Create or update tickets"),
    "platform.manage": ("platform", "manage", "Operate legacy control-plane management APIs"),
    "audit.read": ("audit", "read", "Query immutable audit history"),
    "settings.read": ("settings", "read", "View sanitized system settings"),
    "rbac.read": ("rbac", "read", "View local users, roles, and permissions"),
}

PERMISSIONS = tuple(
    PermissionRead(name=name, resource=resource, action=action, description=description)
    for name, (resource, action, description) in sorted(_PERMISSION_SPECS.items())
)
ALL_PERMISSIONS = frozenset(_PERMISSION_SPECS)

_READ_PERMISSIONS = frozenset(name for name in ALL_PERMISSIONS if name.endswith(".read"))
_SOC_PERMISSIONS = frozenset(
    {
        "dashboard.read",
        "asset.read",
        "knowledge.read",
        "evidence.read",
        "assessment.read",
        "assessment.execute",
        "detection.read",
        "detection.execute",
        "incident.read",
        "incident.write",
        "response.read",
        "response.plan",
        "playbook.read",
        "playbook.execute",
        "worker.read",
        "sandbox.read",
        "plugin.read",
        "approval.read",
        "notification.read",
        "notification.send",
        "ticket.read",
        "ticket.write",
    }
)
_RESPONDER_PERMISSIONS = frozenset(
    {
        "dashboard.read",
        "asset.read",
        "knowledge.read",
        "evidence.read",
        "assessment.read",
        "detection.read",
        "incident.read",
        "incident.write",
        "response.read",
        "response.plan",
        "response.execute",
        "response.rollback",
        "playbook.read",
        "playbook.execute",
        "worker.read",
        "sandbox.read",
        "plugin.read",
        "approval.read",
        "approval.decide",
        "notification.read",
        "notification.send",
        "ticket.read",
        "ticket.write",
    }
)
_AUDITOR_PERMISSIONS = _READ_PERMISSIONS

ROLES = {
    "Administrator": RoleRead(
        name="Administrator",
        description="Full platform administration authority",
        permissions=tuple(sorted(ALL_PERMISSIONS)),
    ),
    "SOC Analyst": RoleRead(
        name="SOC Analyst",
        description="Analyze security data and prepare governed actions",
        permissions=tuple(sorted(_SOC_PERMISSIONS)),
    ),
    "Incident Responder": RoleRead(
        name="Incident Responder",
        description="Operate approved incident response and rollback workflows",
        permissions=tuple(sorted(_RESPONDER_PERMISSIONS)),
    ),
    "Auditor": RoleRead(
        name="Auditor",
        description="Read platform state, settings, RBAC, and immutable audit history",
        permissions=tuple(sorted(_AUDITOR_PERMISSIONS)),
    ),
    "Read Only": RoleRead(
        name="Read Only",
        description="Read operational domain state without audit, settings, or RBAC access",
        permissions=tuple(sorted(_READ_PERMISSIONS - {"audit.read", "settings.read", "rbac.read"})),
    ),
}

_USER_ROLES = {
    "administrator": ("Platform Administrator", ("Administrator",)),
    "soc-analyst": ("SOC Analyst", ("SOC Analyst",)),
    "incident-responder": ("Incident Responder", ("Incident Responder",)),
    "auditor": ("Platform Auditor", ("Auditor",)),
    "read-only": ("Read Only User", ("Read Only",)),
}


def _principal(username: str) -> UserPrincipal | None:
    entry = _USER_ROLES.get(username)
    if entry is None:
        return None
    display_name, role_names = entry
    permissions = frozenset(
        permission for role_name in role_names for permission in ROLES[role_name].permissions
    )
    return UserPrincipal(
        username=username,
        display_name=display_name,
        roles=frozenset(role_names),
        permissions=permissions,
    )


def list_users() -> tuple[UserRead, ...]:
    users = []
    for username in sorted(_USER_ROLES):
        principal = _principal(username)
        assert principal is not None
        users.append(
            UserRead(
                username=principal.username,
                display_name=principal.display_name,
                roles=tuple(sorted(principal.roles)),
                permissions=tuple(sorted(principal.permissions)),
            )
        )
    return tuple(users)


def get_current_user(request: Request) -> UserPrincipal:
    """Authenticate identity supplied by a trusted reverse proxy; fail closed otherwise."""

    settings = request.app.state.settings
    expected_secret = settings.rbac_trusted_proxy_secret
    supplied_secret = request.headers.get(settings.rbac_trusted_proxy_header, "")
    if not expected_secret or not compare_digest(supplied_secret, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Trusted identity proxy authentication required",
        )
    username = request.headers.get(settings.rbac_identity_header, "").strip()
    principal = _principal(username)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown platform user",
        )
    request.state.user = principal
    return principal


CurrentUserDependency = Annotated[UserPrincipal, Depends(get_current_user)]


def require_permission(permission: str) -> Callable[[UserPrincipal], UserPrincipal]:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"Unknown permission: {permission}")

    def dependency(user: CurrentUserDependency) -> UserPrincipal:
        if permission not in user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission}",
            )
        return user

    return dependency
