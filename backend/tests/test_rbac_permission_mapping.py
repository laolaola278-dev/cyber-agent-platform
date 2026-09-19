"""The permission map must cover the console's own API surface.

Found by K8S-GATE 33, not by a unit test: the console's default identity is
`read-only`, `GET /acquisitions` had no rule in ``_permission_for``, and the fallback
is `platform.manage` -- whose description in the RBAC catalog is "Operate *legacy*
control-plane management APIs". So a read of operational state demanded the whole
platform, the shipped deployment answered 403 on Acquisitions, Agents, Tasks,
Workflow, Registry, Capabilities and Runtime, and the only way an operator could
"fix" it was to grant `platform.manage` -- which is the privilege escalation this
map is supposed to prevent.

These tests keep that from silently returning: every endpoint the OpenAPI document
advertises must resolve to a real permission, and the `platform.manage` fallback is
an explicit, reviewed allow-list rather than the default outcome for anything
nobody thought about.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.auth.rbac import ALL_PERMISSIONS, ROLES, _principal
from app.main import create_app
from app.middleware.authorization import _permission_for

#: Endpoints that legitimately require the legacy control-plane permission.
#: Adding a route here is a decision about who may operate the platform; adding a
#: console endpoint without a rule makes this test fail instead.
LEGACY_MANAGE_ONLY: frozenset[str] = frozenset({"POST /heartbeat"})

#: What the default console identity must be able to read. A page that 403s on the
#: shipped configuration is a defect, so each of these is one page of the console.
CONSOLE_READS: tuple[tuple[str, str], ...] = (
    ("/acquisitions", "acquisition.read"),
    ("/acquisitions/4f1f9e6a-0000-4000-8000-000000000000", "acquisition.read"),
    ("/agents", "agent.read"),
    ("/agent/evaluations", "agent.read"),
    ("/tasks", "task.read"),
    ("/workflow", "workflow.read"),
    ("/registry/tools", "registry.read"),
    ("/capabilities", "capability.read"),
    ("/runtime/status", "runtime.read"),
    ("/assets", "asset.read"),
    ("/knowledge", "knowledge.read"),
    ("/incidents", "incident.read"),
    ("/playbooks", "playbook.read"),
    ("/workers", "worker.read"),
    ("/approvals", "approval.read"),
    ("/notifications", "notification.read"),
    ("/tickets", "ticket.read"),
)

CONSOLE_WRITES: tuple[tuple[str, str], ...] = (
    ("POST", "/acquisitions", "acquisition.execute"),
    ("POST", "/tasks", "task.write"),
    ("POST", "/workflow/run", "workflow.execute"),
    ("POST", "/registry/tools", "registry.write"),
    ("POST", "/runtime/start", "runtime.write"),
)


def _advertised_endpoints() -> list[str]:
    spec = create_app().openapi()
    return sorted(
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        for method in operations
    )


def test_endpoints_are_reachable_through_the_advertising_app() -> None:
    """Guard: the derivation below has something to check."""
    assert len(_advertised_endpoints()) > 100, "OpenAPI enumeration is nearly empty"


def test_every_endpoint_maps_to_a_real_permission() -> None:
    unmapped = {
        permission
        for endpoint in _advertised_endpoints()
        for permission in {
            _permission_for(
                endpoint.split()[0],
                endpoint.split()[1].replace("{", "").replace("}", ""),
            )
        }
        if permission not in ALL_PERMISSIONS
    }
    assert not unmapped, f"the map returns permissions the catalog does not define: {unmapped}"


def test_platform_manage_fallback_is_an_explicit_allow_list() -> None:
    """Only reviewed control-plane endpoints may fall through to `platform.manage`.

    The public paths (/health, /ready, /metrics, docs) are exempted by the
    middleware before the map is consulted, so they are excluded here rather than
    added to the allow-list.
    """
    public = ("/health", "/ready", "/metrics", "/openapi", "/docs", "/redoc")
    landing = {
        endpoint
        for endpoint in _advertised_endpoints()
        if _permission_for(endpoint.split()[0], endpoint.split()[1]) == "platform.manage"
        and not endpoint.split()[1].startswith(public)
    }
    unexpected = sorted(landing - LEGACY_MANAGE_ONLY)
    missing = sorted(LEGACY_MANAGE_ONLY - landing)
    assert not unexpected, (
        "these endpoints demand the whole platform, which is what broke the "
        f"default console: {unexpected}"
    )
    assert not missing, f"allow-list entries no longer fall through: {missing} -- delete them"


@pytest.mark.parametrize("path,permission", CONSOLE_READS)
def test_read_only_identity_can_read_what_the_console_displays(path: str, permission: str) -> None:
    assert _permission_for("GET", path) == permission
    read_only = _principal("read-only")
    assert read_only is not None
    assert permission in read_only.permissions, (
        f"the console's default identity cannot GET {path}: it lacks {permission}"
    )


@pytest.mark.parametrize("method,path,permission", CONSOLE_WRITES)
def test_read_only_identity_cannot_write(method: str, path: str, permission: str) -> None:
    """The read-only default must stay read-only -- this fix is not a wide door."""
    assert _permission_for(method, path) == permission
    read_only = _principal("read-only")
    assert read_only is not None
    assert permission not in read_only.permissions


def test_soc_analyst_operates_the_analyst_plane_only() -> None:
    analyst = _principal("soc-analyst")
    assert analyst is not None
    for needed in ("acquisition.execute", "task.write", "workflow.execute", "agent.execute"):
        assert needed in analyst.permissions, f"SOC Analyst lacks {needed}"
    for denied in ("platform.manage", "registry.write", "runtime.write", "response.execute"):
        assert denied not in analyst.permissions, f"SOC Analyst must not hold {denied}"


def test_a_new_route_without_a_rule_is_caught() -> None:
    """Negative control: the fallback really is `platform.manage`.

    Without this, `test_platform_manage_fallback_is_an_explicit_allow_list` could
    pass because the map happens to answer something else for unknown paths.
    """
    assert _permission_for("GET", "/next-console-page") == "platform.manage"
    assert _permission_for("POST", "/next-console-page") == "platform.manage"
    assert _permission_for("GET", "/acquisitions") != "platform.manage"


async def test_the_shipped_routes_enforce_the_new_map(client: AsyncClient) -> None:
    """End-to-end through the middleware, not just the pure function."""
    allowed = await client.get("/acquisitions")
    assert allowed.status_code == 200, allowed.text  # fixture identity: administrator

    readonly = await client.get("/acquisitions", headers={"X-CAP-User": "read-only"})
    assert readonly.status_code == 200, readonly.text

    refused = await client.post(
        "/acquisitions",
        json={"target": "http://127.0.0.1:1/", "mode": "http"},
        headers={"X-CAP-User": "read-only"},
    )
    assert refused.status_code == 403, refused.text
    assert "acquisition.execute" in refused.json()["detail"]

    unknown = await client.get("/acquisitions", headers={"X-CAP-User": "no-such-user"})
    assert unknown.status_code == 401

    forged = await client.get("/acquisitions", headers={"X-CAP-Proxy-Secret": "wrong"})
    assert forged.status_code == 401


def test_new_permissions_are_disclosed_and_grouped() -> None:
    """/permissions is a public contract: the additions must appear with resources."""
    from app.auth.rbac import PERMISSIONS

    by_name = {permission.name: permission for permission in PERMISSIONS}
    for name in ("acquisition.read", "workflow.execute", "registry.write"):
        assert name in by_name, name
        assert by_name[name].resource == name.split(".")[0]
        assert by_name[name].description
    administrator = ROLES["Administrator"]
    assert "acquisition.read" in administrator.permissions
    assert "platform.manage" in administrator.permissions
