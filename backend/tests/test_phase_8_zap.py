"""Phase 8 ZAP Plugin tests using only mock API clients and fake Alerts."""

import inspect
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.assessment import AssessmentPluginContext, AssessmentRegistry, AssessmentRuntime
from app.exceptions import AssessmentPolicyViolation, AssessmentValidationError
from app.models import AssessmentPlugin, AssessmentTask, AuditLog
from app.plugins.zap import ZapAssessmentPlugin, ZapResultNormalizer
from app.schemas.assessment import AssessmentPlan, ZapPolicy
from app.tools.zap import (
    ZapAdapter,
    ZapExecutionRequest,
    ZapSandboxProfile,
    ZapV2ApiClient,
)
from tests.conftest import TestSessionFactory


class UnusedSandbox:
    async def execute(self, command: object) -> object:
        raise AssertionError("Daemon API mode must not launch a process per scan")


class FakeZapApi:
    def __init__(self, alerts: list[dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self._alerts = alerts or []
        self.fail_version = False

    async def version(self) -> str:
        if self.fail_version:
            raise RuntimeError("offline")
        return "2.17.0"

    async def new_session(self, name: str, *, overwrite: bool) -> None:
        self.calls.append(("new_session", overwrite))

    async def remove_session(self, name: str) -> None:
        self.calls.append(("remove_session", name))

    async def new_context(self, name: str) -> str:
        self.calls.append(("new_context", name))
        return "7"

    async def include_in_context(self, name: str, regex: str) -> None:
        self.calls.append(("include", regex))

    async def exclude_from_context(self, name: str, regex: str) -> None:
        self.calls.append(("exclude", regex))

    async def access_url(self, url: str) -> None:
        self.calls.append(("access", url))

    async def wait_for_passive_scan(self, timeout_seconds: int) -> None:
        self.calls.append(("passive", timeout_seconds))

    async def spider(self, url: str, *, context_name: str, max_depth: int, max_urls: int) -> int:
        self.calls.append(("spider", (max_depth, max_urls)))
        return 3

    async def active_scan(
        self, url: str, *, context_id: str, scan_policy: str, timeout_seconds: int
    ) -> None:
        self.calls.append(("active", (context_id, scan_policy)))

    async def alerts(self, *, base_url: str, limit: int) -> list[dict[str, object]]:
        self.calls.append(("alerts", (base_url, limit)))
        return self._alerts


def _alert() -> dict[str, object]:
    return {
        "id": "1",
        "pluginId": "10020",
        "alert": "X-Frame-Options Header Not Set",
        "risk": "Medium",
        "confidence": "High",
        "url": "https://authorized.example/app",
        "method": "GET",
        "param": "",
        "evidence": "",
        "description": "CWE-1021; OWASP A05:2021 Security Misconfiguration",
        "solution": "Set a frame-ancestors policy.",
        "reference": (
            "https://www.zaproxy.org/docs/alerts/10020/\n"
            "https://capec.mitre.org/data/definitions/103.html CAPEC-103"
        ),
        "cweid": "1021",
        "wascid": "15",
    }


def _policy(**updates: object) -> ZapPolicy:
    values: dict[str, object] = {
        "max_requests": 100,
        "timeout_seconds": 60,
        "max_scan_time_seconds": 60,
        "capability_allowlist": ["web.dast", "web.passive_scan", "web.spider"],
    }
    values.update(updates)
    return ZapPolicy(**values)


def _adapter(api: FakeZapApi) -> ZapAdapter:
    return ZapAdapter(
        api,
        UnusedSandbox(),
        profile=ZapSandboxProfile(timeout_seconds=120),
        allowed_scan_policies=frozenset({"cap-passive-baseline", "cap-active-controlled"}),
    )


def _context(policy: ZapPolicy | None = None) -> AssessmentPluginContext:
    selected = policy or _policy()
    capabilities = ["web.dast", "web.passive_scan"]
    if selected.spider_enabled:
        capabilities.append("web.spider")
    if selected.active_scan_enabled:
        capabilities.append("web.active_scan")
    return AssessmentPluginContext(
        assessment_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=uuid4(),
        trace_id="phase-8",
        capabilities=tuple(capabilities),
        policy=selected,
        input={"target": "https://authorized.example", "active_scan_authorized": False},
        granted_permissions=frozenset({"assessment.execute", "tool.invoke", "evidence.write"}),
    )


async def test_adapter_passive_default_manages_session_context_and_alerts() -> None:
    api = FakeZapApi([_alert()])
    result = await _adapter(api).execute(
        ZapExecutionRequest("https://authorized.example", _policy())
    )
    names = [name for name, _ in api.calls]
    assert result.mode == "passive"
    assert result.tool_version == "2.17.0"
    assert result.alert_summary == {"MEDIUM": 1}
    assert names == [
        "new_session",
        "new_context",
        "include",
        "access",
        "passive",
        "alerts",
        "remove_session",
    ]
    assert "active" not in names


async def test_adapter_active_scan_requires_authorization_and_allowlisted_policy() -> None:
    policy = _policy(
        active_scan_enabled=True,
        spider_enabled=True,
        scan_policy="cap-active-controlled",
        capability_allowlist=[
            "web.dast",
            "web.passive_scan",
            "web.spider",
            "web.active_scan",
        ],
    )
    with pytest.raises(AssessmentPolicyViolation, match="authorized Asset"):
        await _adapter(FakeZapApi()).execute(
            ZapExecutionRequest("https://authorized.example", policy, False)
        )
    api = FakeZapApi([_alert()])
    result = await _adapter(api).execute(
        ZapExecutionRequest("https://authorized.example", policy, True)
    )
    assert result.mode == "active"
    assert {name for name, _ in api.calls} >= {"spider", "active"}


@pytest.mark.parametrize(
    "target",
    ["ftp://authorized.example", "https://user:pass@authorized.example", "https://a.example/#x"],
)
async def test_adapter_rejects_unsafe_targets(target: str) -> None:
    with pytest.raises(AssessmentPolicyViolation):
        await _adapter(FakeZapApi()).execute(ZapExecutionRequest(target, _policy()))


def test_zap_policy_defaults_passive_and_rejects_invalid_modes() -> None:
    policy = ZapPolicy()
    assert policy.passive_scan_enabled is True
    assert policy.active_scan_enabled is False
    with pytest.raises(ValidationError, match="At least one ZAP scan mode"):
        ZapPolicy(passive_scan_enabled=False)
    with pytest.raises(ValidationError, match="web.active_scan"):
        ZapPolicy(active_scan_enabled=True)


def test_normalizer_maps_alert_to_unified_finding_and_knowledge_references() -> None:
    result = ZapResultNormalizer().assessment_result(
        (_alert(),),
        plugin_name="zap-assessment",
        plugin_version="1.0.0",
        requests_made=1,
        metadata={"mode": "passive"},
    )
    finding = result.findings[0]
    assert finding.tool == "owasp-zap"
    assert finding.rule == "10020"
    assert finding.severity.value == "MEDIUM"
    assert finding.confidence.value == "HIGH"
    assert finding.attributes["cwe"] == "CWE-1021"
    assert finding.attributes["wasc"] == "WASC-15"
    references = finding.attributes["knowledge_references"]
    assert {item["type"] for item in references} >= {"CWE", "CAPEC", "OWASP_CATEGORY"}


async def test_plugin_runs_six_phase_runtime_without_direct_api_or_subprocess() -> None:
    api = FakeZapApi([_alert()])
    plugin = ZapAssessmentPlugin(_adapter(api))
    registry = AssessmentRegistry()
    registry.register(plugin)
    context = _context()
    plan = AssessmentPlan(
        asset_id=context.asset_id,
        capabilities=list(context.capabilities),
        plugin_name=plugin.name,
        steps=[],
        limits={},
    )
    result = await AssessmentRuntime(registry).execute(plan, context)
    assert result.plugin_name == "zap-assessment"
    assert result.metadata["scan_policy"] == "cap-passive-baseline"
    assert plugin._initialized is False
    source = inspect.getsource(ZapAssessmentPlugin)
    assert "zapv2" not in source.casefold()
    assert "subprocess" not in source.casefold()


async def test_plugin_permission_and_input_validation() -> None:
    plugin = ZapAssessmentPlugin(_adapter(FakeZapApi()))
    with pytest.raises(AssessmentValidationError, match="tool.invoke"):
        await plugin.initialize(replace(_context(), granted_permissions=frozenset()))
    context = _context()
    await plugin.initialize(context)
    with pytest.raises(AssessmentValidationError, match="platform-derived"):
        await plugin.execute(await plugin.plan(context), replace(context, input={}))
    await plugin.shutdown()


async def test_adapter_status_reports_health_and_sandbox_profile() -> None:
    api = FakeZapApi()
    adapter = _adapter(api)
    status = await adapter.status()
    assert status["healthy"] is True
    assert status["profile"]["memory_limit_mb"] == 1024
    api.fail_version = True
    assert (await adapter.status())["healthy"] is False


async def test_zap_api_requires_asset_and_never_accepts_arbitrary_target(
    client: AsyncClient,
) -> None:
    policies = await client.get("/assessment/zap/policies")
    status = await client.get("/assessment/zap/status")
    assert policies.status_code == 200
    assert policies.json()[0]["active_scan_enabled"] is False
    assert status.status_code == 200

    created = await client.post(
        "/assets",
        json={
            "asset_type": "WEBSITE",
            "name": "Authorized ZAP target",
            "value": "https://authorized.example",
            "properties": {"assessment": {"active_scan_authorized": False}},
        },
    )
    assert created.status_code == 201
    response = await client.post(
        "/assessment/zap",
        json={"asset_id": created.json()["id"], "execute": False},
    )
    assert response.status_code == 201
    assert response.json()["requested_capabilities"] == ["web.dast", "web.passive_scan"]

    invalid = await client.post(
        "/assessment/zap",
        json={"asset_id": created.json()["id"], "target": "https://evil.example"},
    )
    assert invalid.status_code == 422

    async with TestSessionFactory() as session:
        task = await session.scalar(select(AssessmentTask))
        plugin = await session.scalar(
            select(AssessmentPlugin).where(AssessmentPlugin.name == "zap-assessment")
        )
        audit = list(await session.scalars(select(AuditLog)))
        assert task is not None
        assert task.task.asset_id is not None
        assert plugin is not None
        assert set(task.requested_capabilities) == {"web.dast", "web.passive_scan"}
        event_types = {item.action for item in audit}
        assert {"ZapSessionCreated", "ZapPolicyEnforced"} <= event_types


async def test_zap_active_api_rejects_asset_without_explicit_authorization(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/assets",
        json={
            "asset_type": "WEBSITE",
            "name": "Passive only target",
            "value": "https://passive.example",
        },
    )
    response = await client.post(
        "/assessment/zap",
        json={
            "asset_id": created.json()["id"],
            "execute": False,
            "policy": {
                "active_scan_enabled": True,
                "scan_policy": "cap-active-controlled",
                "capability_allowlist": [
                    "web.dast",
                    "web.passive_scan",
                    "web.active_scan",
                ],
            },
        },
    )
    assert response.status_code == 403
    assert "active_scan_authorized" in response.json()["error"]["message"]


class SyncCore:
    version = "2.17.0"

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def new_session(self, name: str, overwrite: str) -> None:
        self.calls.append(("new_session", (name, overwrite)))

    def delete_session(self, name: str) -> None:
        self.calls.append(("delete_session", name))

    def access_url(self, url: str) -> None:
        self.calls.append(("access_url", url))

    def alerts(self, *, baseurl: str, count: int) -> list[dict[str, object]]:
        self.calls.append(("alerts", (baseurl, count)))
        return [_alert()]


class SyncContext:
    def new_context(self, name: str) -> str:
        return "9"

    def include_in_context(self, name: str, regex: str) -> None:
        return None

    def exclude_from_context(self, name: str, regex: str) -> None:
        return None


class SyncSpider:
    def set_option_max_depth(self, depth: int) -> None:
        return None

    def scan(self, url: str, **kwargs: object) -> str:
        return "spider-1"

    def status(self, scan_id: str) -> str:
        return "100"

    def results(self, scan_id: str) -> list[str]:
        return ["a", "b"]


class SyncAscan:
    def scan(self, url: str, **kwargs: object) -> str:
        return "active-1"

    def status(self, scan_id: str) -> str:
        return "100"


async def test_official_client_facade_maps_all_zap_components_without_network() -> None:
    core = SyncCore()
    client = ZapV2ApiClient(api_url="http://127.0.0.1:8080", api_key="test")
    client._client = SimpleNamespace(
        core=core,
        context=SyncContext(),
        pscan=SimpleNamespace(records_to_scan="0"),
        spider=SyncSpider(),
        ascan=SyncAscan(),
    )
    assert await client.version() == "2.17.0"
    await client.new_session("session", overwrite=False)
    assert await client.new_context("context") == "9"
    await client.include_in_context("context", "^https://example")
    await client.exclude_from_context("context", "logout")
    await client.access_url("https://example")
    await client.wait_for_passive_scan(1)
    assert (
        await client.spider("https://example", context_name="context", max_depth=1, max_urls=10)
        == 2
    )
    await client.active_scan(
        "https://example", context_id="9", scan_policy="policy", timeout_seconds=1
    )
    assert (await client.alerts(base_url="https://example", limit=10))[0]["pluginId"] == "10020"
    await client.remove_session("session")
    assert {name for name, _ in core.calls} >= {
        "new_session",
        "delete_session",
        "access_url",
        "alerts",
    }


async def test_official_client_facade_handles_non_list_alerts_and_scan_timeout() -> None:
    client = ZapV2ApiClient(api_url="http://127.0.0.1:8080", api_key="test")
    client._client = SimpleNamespace(
        core=SimpleNamespace(alerts=lambda **kwargs: {}),
    )
    assert await client.alerts(base_url="https://example", limit=1) == []
    with pytest.raises(AssessmentValidationError):
        ZapResultNormalizer().assessment_result(
            ({"alert": "missing identity"},),
            plugin_name="zap-assessment",
            plugin_version="1.0.0",
            requests_made=0,
            metadata={},
        )


async def test_zap_active_api_accepts_explicitly_authorized_asset(
    client: AsyncClient,
) -> None:
    created = await client.post(
        "/assets",
        json={
            "asset_type": "APPLICATION",
            "name": "Authorized active target",
            "value": "cap-app",
            "properties": {
                "url": "https://active.example/app",
                "assessment": {"active_scan_authorized": True},
            },
        },
    )
    response = await client.post(
        "/assessment/zap",
        json={
            "asset_id": created.json()["id"],
            "execute": False,
            "policy": {
                "active_scan_enabled": True,
                "scan_policy": "cap-active-controlled",
                "capability_allowlist": [
                    "web.dast",
                    "web.passive_scan",
                    "web.active_scan",
                ],
            },
        },
    )
    assert response.status_code == 201
    assert "web.active_scan" in response.json()["requested_capabilities"]
