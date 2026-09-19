"""Platform-wide fail-closed authorization middleware."""

from __future__ import annotations

from hmac import compare_digest

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.rbac import _principal

_PUBLIC_PATHS = frozenset({"/health", "/ready", "/metrics", "/openapi.json", "/docs", "/redoc"})
_PUBLIC_PREFIXES = ("/docs/", "/redoc/")


def _permission_for(method: str, path: str) -> str:
    read = method in {"GET", "HEAD", "OPTIONS"}
    if path == "/dashboard":
        return "dashboard.read"
    if path.startswith("/assets"):
        return "asset.read" if read else "asset.write"
    if path.startswith("/knowledge"):
        return "knowledge.read" if read else "knowledge.write"
    if path.startswith("/assessment"):
        return "assessment.read" if read else "assessment.execute"
    if path.startswith(("/detection", "/telemetry")):
        return "detection.read" if read else "detection.execute"
    if path.startswith(("/incidents", "/cases")):
        return "incident.read" if read else "incident.write"
    if path.startswith("/response"):
        if read:
            return "response.read"
        if path.endswith(("/approve", "/reject")):
            return "approval.decide"
        if path.endswith("/execute"):
            return "response.execute"
        if path.endswith("/rollback"):
            return "response.rollback"
        return "response.plan"
    if path.startswith("/playbooks"):
        if read:
            return "playbook.read"
        if path.endswith("/run") or "/executions/" in path:
            return "playbook.execute"
        return "playbook.write"
    if path.startswith(("/workers", "/health/workers")):
        return "worker.read"
    if path.startswith("/sandbox"):
        return "sandbox.read"
    if path == "/plugins":
        return "plugin.read"
    if path == "/approvals":
        return "approval.read"
    if path.startswith("/notifications") or path.startswith("/notification/plugins"):
        return "notification.read" if read else "notification.send"
    if path.startswith("/tickets"):
        return "ticket.read" if read else "ticket.write"
    if path == "/audit":
        return "audit.read"
    if path == "/settings":
        return "settings.read"
    if path.startswith("/workflow") and path.endswith("/decision"):
        # Answering an approval gate takes the approval permission, not the
        # right to manage the platform: same gate, same rule as response plans.
        return "approval.decide"
    if path in {"/roles", "/permissions", "/users"}:
        return "rbac.read"
    # The analyst/operations plane below is read by the console and written by its
    # operator roles. Before this it fell through to `platform.manage`, whose own
    # catalog description says "legacy control-plane management APIs": a read of
    # /acquisitions, /tasks or /workflow therefore demanded the whole platform,
    # which is what made the default `read-only` console answer 403 on half its
    # pages (found by K8S-GATE 33, not by a unit test).
    if path.startswith("/acquisitions"):
        return "acquisition.read" if read else "acquisition.execute"
    if path == "/agent" or path.startswith("/agent/"):
        return "agent.read" if read else "agent.execute"
    if path.startswith("/agents"):
        return "agent.read" if read else "agent.execute"
    if path.startswith("/tasks"):
        return "task.read" if read else "task.write"
    if path.startswith("/workflow"):
        return "workflow.read" if read else "workflow.execute"
    if path.startswith("/registry"):
        return "registry.read" if read else "registry.write"
    if path.startswith("/capabilities"):
        return "capability.read"
    if path.startswith("/runtime"):
        return "runtime.read" if read else "runtime.write"
    # Everything else is the legacy control plane: agent registration/heartbeat,
    # and any route added without an explicit rule. Failing closed to the
    # broadest permission is deliberate -- test_rbac_permission_mapping.py makes a
    # new console endpoint that lands here a failure, so the fallback cannot
    # quietly absorb the next page.
    return "platform.manage"


class AuthorizationMiddleware(BaseHTTPMiddleware):
    """Authenticate trusted proxy identity and enforce path-level resource permissions."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        settings = request.app.state.settings
        expected_secret = settings.rbac_trusted_proxy_secret
        supplied_secret = request.headers.get(settings.rbac_trusted_proxy_header, "")
        if not expected_secret or not compare_digest(supplied_secret, expected_secret):
            return JSONResponse(
                {"detail": "Trusted identity proxy authentication required"}, status_code=401
            )
        username = request.headers.get(settings.rbac_identity_header, "").strip()
        principal = _principal(username)
        if principal is None:
            return JSONResponse({"detail": "Unknown platform user"}, status_code=401)

        permission = _permission_for(request.method, path)
        if permission not in principal.permissions:
            return JSONResponse({"detail": f"Permission required: {permission}"}, status_code=403)
        request.state.user = principal
        return await call_next(request)
