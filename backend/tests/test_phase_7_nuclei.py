"""Phase 7 Nuclei Plugin tests using only fake sandbox results and local fixtures."""

import importlib.util
import inspect
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.assessment import AssessmentPluginContext, AssessmentRegistry, AssessmentRuntime
from app.assessment.finding_state import FindingStateMachine
from app.assessment.fingerprint import SHA256FingerprintProvider
from app.assessment.knowledge_mapper import FindingKnowledgeMapper
from app.core.enums import FindingConfidence, FindingSeverity, FindingStatus
from app.database import Base
from app.exceptions import (
    AssessmentExecutionError,
    AssessmentPolicyViolation,
    AssessmentValidationError,
    InvalidStateTransition,
)
from app.models import (
    AssessmentReport,
    AuditLog,
    Finding,
    FindingComment,
    FindingHistory,
    FindingTransition,
)
from app.plugins.nuclei import NucleiAssessmentPlugin, NucleiResultNormalizer
from app.sandbox import (
    DockerSandbox,
    FirecrackerSandbox,
    LocalProcessSandbox,
    RemoteWorkerSandbox,
    SandboxCommand,
    SandboxResult,
)
from app.schemas.assessment import AssessmentPlan, AssessmentPolicy, RawFinding
from app.tools.nuclei import ApprovedNucleiTemplate, NucleiAdapter, NucleiExecutionRequest
from tests.conftest import TestSessionFactory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / "tools" / "nuclei" / "templates"
TEMPLATE_PATH = TEMPLATE_ROOT / "cap-http-missing-content-type-options.yaml"
TEMPLATE_ID = "cap-http-missing-content-type-options"


class FakeSandbox:
    def __init__(self, result: SandboxResult) -> None:
        self.result = result
        self.commands: list[SandboxCommand] = []

    async def execute(self, command: SandboxCommand) -> SandboxResult:
        self.commands.append(command)
        return self.result


def _record() -> dict[str, object]:
    return {
        "template-id": TEMPLATE_ID,
        "template-path": str(TEMPLATE_PATH),
        "type": "http",
        "host": "https://authorized.example",
        "matched-at": "https://authorized.example/",
        "matcher-name": "missing-header",
        "matcher-status": True,
        "request": "GET / HTTP/1.1",
        "response": "HTTP/1.1 200 OK",
        "timestamp": "2026-07-30T12:00:00Z",
        "info": {
            "name": "Missing X-Content-Type-Options",
            "severity": "medium",
            "description": "The response omits a defensive header.",
            "tags": ["CVE-2026-1234", "CWE-693", "T1190"],
            "reference": ["https://example.invalid/reference"],
            "classification": {
                "cve-id": ["CVE-2026-1234"],
                "cwe-id": ["CWE-693"],
                "cpe": ["cpe:2.3:a:example:app:1.0:*:*:*:*:*:*:*"],
            },
        },
    }


def _policy(**updates: object) -> AssessmentPolicy:
    values: dict[str, object] = {
        "max_concurrency": 2,
        "max_requests": 2,
        "rate_limit_per_second": 1.5,
        "timeout_seconds": 30,
        "capability_allowlist": ["template.scan", "web.scan"],
    }
    values.update(updates)
    return AssessmentPolicy(**values)


def _adapter(
    sandbox: FakeSandbox,
    *,
    digest: str | None = None,
    template_requests: int = 1,
) -> NucleiAdapter:
    approved = ApprovedNucleiTemplate(
        template_id=TEMPLATE_ID,
        path=TEMPLATE_PATH,
        sha256=digest or sha256(TEMPLATE_PATH.read_bytes()).hexdigest(),
        max_requests=template_requests,
    )
    return NucleiAdapter(
        sandbox,
        executable="nuclei",
        template_root=TEMPLATE_ROOT,
        approved_templates={TEMPLATE_ID: approved},
        max_output_bytes=50_000,
    )


def _context(policy: AssessmentPolicy | None = None) -> AssessmentPluginContext:
    return AssessmentPluginContext(
        assessment_task_id=uuid4(),
        task_id=uuid4(),
        asset_id=uuid4(),
        trace_id="phase-7",
        capabilities=("template.scan", "web.scan"),
        policy=policy or _policy(),
        input={
            "target": "https://authorized.example",
            "templates": [TEMPLATE_ID],
        },
        granted_permissions=frozenset({"assessment.execute", "tool.invoke", "evidence.write"}),
    )


async def test_adapter_builds_governed_cli_and_parses_jsonl() -> None:
    import json

    sandbox = FakeSandbox(
        SandboxResult(
            exit_code=0,
            stdout=json.dumps(_record()),
            stderr="",
            timed_out=False,
            duration_seconds=0.2,
        )
    )
    result = await _adapter(sandbox).execute(
        NucleiExecutionRequest(
            target="https://authorized.example",
            templates=(TEMPLATE_ID,),
            policy=_policy(),
        )
    )

    assert result.records == (_record(),)
    assert result.request_budget == 1
    command = sandbox.commands[0]
    assert command.executable == "nuclei"
    assert command.arguments[:2] == ("-u", "https://authorized.example")
    for required in ("-jsonl", "-duc", "-ni", "-no-stdin", "-no-httpx", "-or", "-ot"):
        assert required in command.arguments
    assert command.arguments.count("-t") == 1
    assert "ENABLE_CLOUD_UPLOAD" in command.environment
    assert command.environment["ENABLE_CLOUD_UPLOAD"] == "false"


async def test_adapter_rejects_unapproved_tampered_and_over_budget_templates() -> None:
    sandbox = FakeSandbox(SandboxResult(0, "", "", False, 0.1))
    request = NucleiExecutionRequest(
        target="https://authorized.example",
        templates=("not-approved",),
        policy=_policy(),
    )
    with pytest.raises(AssessmentPolicyViolation, match="not approved"):
        await _adapter(sandbox).execute(request)

    with pytest.raises(AssessmentPolicyViolation, match="integrity"):
        await _adapter(sandbox, digest="0" * 64).execute(
            request.__class__(
                target=request.target,
                templates=(TEMPLATE_ID,),
                policy=request.policy,
            )
        )

    with pytest.raises(AssessmentPolicyViolation, match="request budget"):
        await _adapter(sandbox, template_requests=2).execute(
            request.__class__(
                target=request.target,
                templates=(TEMPLATE_ID,),
                policy=_policy(max_requests=1),
            )
        )


@pytest.mark.parametrize(
    "stdout, message",
    [("not-json", "invalid JSONL"), ("[]", "must be an object")],
)
def test_adapter_rejects_invalid_jsonl(stdout: str, message: str) -> None:
    with pytest.raises(AssessmentExecutionError, match=message):
        NucleiAdapter.parse_jsonl(stdout)


async def test_adapter_rejects_failed_or_truncated_sandbox_results() -> None:
    failed = FakeSandbox(SandboxResult(2, "", "failed", False, 0.1))
    with pytest.raises(AssessmentExecutionError, match="execution failed"):
        await _adapter(failed).execute(
            NucleiExecutionRequest("https://authorized.example", (TEMPLATE_ID,), _policy())
        )

    truncated = FakeSandbox(SandboxResult(0, "{}", "", False, 0.1, True))
    with pytest.raises(AssessmentExecutionError, match="exceeded"):
        await _adapter(truncated).execute(
            NucleiExecutionRequest("https://authorized.example", (TEMPLATE_ID,), _policy())
        )


async def test_plugin_lifecycle_runs_through_runtime_without_real_process() -> None:
    import json

    sandbox = FakeSandbox(SandboxResult(0, json.dumps(_record()), "", False, 0.1))
    plugin = NucleiAssessmentPlugin(_adapter(sandbox))
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

    assert result.plugin_name == "nuclei-assessment"
    assert result.requests_made == 1
    assert result.findings[0].tool == "nuclei"
    assert plugin._initialized is False
    assert "subprocess" not in inspect.getsource(NucleiAssessmentPlugin)


async def test_plugin_requires_tool_permission_and_platform_derived_input() -> None:
    plugin = NucleiAssessmentPlugin(_adapter(FakeSandbox(SandboxResult(0, "", "", False, 0.1))))
    denied = replace(_context(), granted_permissions=frozenset())
    with pytest.raises(AssessmentValidationError, match="tool.invoke"):
        await plugin.initialize(denied)

    context = _context()
    await plugin.initialize(context)
    invalid = replace(context, input={"target": "arbitrary"})
    with pytest.raises(AssessmentValidationError, match="target/templates"):
        await plugin.execute(await plugin.plan(context), invalid)
    await plugin.shutdown()


def test_nuclei_normalizer_preserves_evidence_and_knowledge_references() -> None:
    finding = NucleiResultNormalizer().normalize((_record(),))[0]

    assert finding.severity is FindingSeverity.MEDIUM
    assert finding.confidence is FindingConfidence.HIGH
    assert finding.rule == TEMPLATE_ID
    assert finding.attributes["evidence"]["request"] == "GET / HTTP/1.1"
    references = finding.attributes["knowledge_references"]
    assert {item["type"] for item in references} == {
        "CVE",
        "CWE",
        "CPE",
        "ATTACK_TECHNIQUE",
    }
    assert any("nvd.nist.gov" in item for item in finding.references)
    assert any("cwe.mitre.org" in item for item in finding.references)


@pytest.mark.parametrize("severity", ["unsupported", 123])
def test_nuclei_normalizer_rejects_unsupported_severity(severity: object) -> None:
    record = _record()
    record["info"] = {**record["info"], "severity": severity}
    with pytest.raises(AssessmentValidationError, match="unsupported severity"):
        NucleiResultNormalizer().normalize((record,))


def test_fingerprint_provider_is_stable_and_allows_plugin_material_override() -> None:
    provider = SHA256FingerprintProvider()
    asset_id = uuid4()
    raw = RawFinding(
        title="Finding",
        severity=FindingSeverity.HIGH,
        affected_asset="https://authorized.example",
        tool="nuclei",
        rule="template-1",
    )
    assert provider.fingerprint(raw, "Plugin", asset_id) == provider.fingerprint(
        raw, "plugin", asset_id
    )

    overridden = raw.model_copy(
        update={"attributes": {"fingerprint_material": {"scanner_id": "stable-id"}}}
    )
    assert provider.fingerprint(overridden, "one", uuid4()) == provider.fingerprint(
        overridden, "two", uuid4()
    )


def test_finding_state_machine_accepts_declared_paths_and_rejects_illegal_jump() -> None:
    FindingStateMachine.validate(FindingStatus.NEW, FindingStatus.TRIAGED)
    FindingStateMachine.validate(FindingStatus.TRIAGED, FindingStatus.CONFIRMED)
    FindingStateMachine.validate(FindingStatus.CONFIRMED, FindingStatus.FIXED)
    FindingStateMachine.validate(FindingStatus.FIXED, FindingStatus.REOPENED)
    with pytest.raises(InvalidStateTransition, match="NEW to FIXED"):
        FindingStateMachine.validate(FindingStatus.NEW, FindingStatus.FIXED)


async def test_local_process_sandbox_enforces_allowlist_limits_timeout_and_cwd(
    tmp_path: Path,
) -> None:
    executable = sys.executable
    sandbox = LocalProcessSandbox({executable})
    result = await sandbox.execute(
        SandboxCommand(
            executable=executable,
            arguments=("-c", "print('sandbox-ok')"),
            working_directory=tmp_path,
            timeout_seconds=10,
            max_output_bytes=1_000,
            environment={"CAP_TEST_ENV": "minimal"},
        )
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "sandbox-ok"
    assert result.output_truncated is False

    truncated = await sandbox.execute(
        SandboxCommand(
            executable=executable,
            arguments=("-c", "print('x' * 100)"),
            timeout_seconds=2,
            max_output_bytes=10,
        )
    )
    assert truncated.output_truncated is True
    assert len(truncated.stdout.encode()) <= 10

    with pytest.raises(AssessmentPolicyViolation, match="not allowlisted"):
        await sandbox.execute(SandboxCommand(executable="not-approved"))
    with pytest.raises(AssessmentPolicyViolation, match="positive"):
        await sandbox.execute(SandboxCommand(executable=executable, timeout_seconds=0))
    with pytest.raises(AssessmentPolicyViolation, match="does not exist"):
        await sandbox.execute(
            SandboxCommand(executable=executable, working_directory=tmp_path / "missing")
        )
    with pytest.raises(AssessmentExecutionError, match="timed out"):
        await sandbox.execute(
            SandboxCommand(
                executable=executable,
                arguments=("-c", "import time; time.sleep(2)"),
                timeout_seconds=1,
            )
        )


class FakeKnowledgeRepository:
    def __init__(self, rows: dict[tuple[str, str], tuple[object, object]]) -> None:
        self.rows = rows

    async def get_by_external_id(self, kind: str, external_id: str) -> object | None:
        pair = self.rows.get((kind, external_id))
        return pair[0] if pair else None

    async def get_current_version(self, row: object) -> object | None:
        for candidate, version in self.rows.values():
            if candidate is row:
                return version
        return None


async def test_knowledge_mapper_links_existing_versions_and_keeps_unknown_references() -> None:
    from types import SimpleNamespace

    cve = SimpleNamespace(id=uuid4())
    cve_version = SimpleNamespace(id=uuid4())
    kev = SimpleNamespace(id=uuid4())
    kev_version = SimpleNamespace(id=uuid4())
    repository = FakeKnowledgeRepository(
        {
            ("CVE", "CVE-2026-1234"): (cve, cve_version),
            ("CISA_KEV", "CVE-2026-1234"): (kev, kev_version),
        }
    )
    result = NucleiResultNormalizer().assessment_result(
        (_record(),),
        plugin_name="nuclei-assessment",
        plugin_version="1.0.0",
        requests_made=1,
    )
    mapped = await FindingKnowledgeMapper(repository).enrich(result)

    assert result.findings[0].knowledge_ids == [cve.id, kev.id]
    assert mapped[cve.id] == (cve, cve_version)
    assert mapped[kev.id] == (kev, kev_version)
    assert any("definitions/693.html" in item for item in result.findings[0].references)


@pytest.mark.parametrize(
    "provider",
    [DockerSandbox(), FirecrackerSandbox(), RemoteWorkerSandbox()],
)
async def test_future_sandbox_providers_are_explicitly_reserved(provider: object) -> None:
    with pytest.raises(NotImplementedError, match="reserved"):
        await provider.execute(SandboxCommand(executable="nuclei"))


async def test_transition_report_history_comment_and_audit_persistence(
    client: AsyncClient,
) -> None:
    asset_response = await client.post(
        "/assets",
        json={
            "asset_type": "APPLICATION",
            "name": "Phase 7 lifecycle app",
            "value": "Phase 7 lifecycle app",
            "criticality": "HIGH",
        },
    )
    request = {
        "name": "Lifecycle fixture",
        "asset_id": asset_response.json()["id"],
        "capabilities": ["header.scan"],
        "execute": True,
        "input": {
            "fake_findings": [
                {
                    "title": "Lifecycle finding",
                    "severity": "HIGH",
                    "affected_asset": "phase 7 lifecycle app",
                    "tool": "fake-tool",
                    "rule": "PHASE7-LIFECYCLE",
                }
            ]
        },
    }
    assessment_response = await client.post("/assessment/tasks", json=request)
    assert assessment_response.status_code == 201, assessment_response.text
    assessment_id = assessment_response.json()["id"]

    findings_response = await client.get(
        "/assessment/findings", params={"asset_id": asset_response.json()["id"]}
    )
    finding_id = findings_response.json()["items"][0]["id"]
    transition = await client.post(
        f"/assessment/findings/{finding_id}/transition",
        json={"status": "TRIAGED", "actor": "security-reviewer", "reason": "reviewed"},
    )
    assert transition.status_code == 201, transition.text
    assert transition.json()["from_status"] == "NEW"
    assert transition.json()["to_status"] == "TRIAGED"

    async with TestSessionFactory() as session:
        report = await session.scalar(
            select(AssessmentReport).where(
                AssessmentReport.assessment_task_id == UUID(assessment_id)
            )
        )
        assert report is not None
        report_id = report.id
        assert report.content["findings"][0]["template"] == "PHASE7-LIFECYCLE"
        assert await session.scalar(select(func.count()).select_from(FindingHistory)) == 2
        assert await session.scalar(select(func.count()).select_from(FindingTransition)) == 1
        finding = await session.get(Finding, UUID(finding_id))
        assert finding is not None
        session.add(
            FindingComment(
                finding_id=finding.id,
                author="security-reviewer",
                body="Reserved triage collaboration record",
            )
        )
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(FindingComment)) == 1
        actions = set(await session.scalars(select(AuditLog.action)))
        assert {"AssessmentReportGenerated", "FindingTransitioned"} <= actions

    report_response = await client.get(f"/assessment/reports/{report_id}")
    assert report_response.status_code == 200
    assert report_response.json()["assessment_task_id"] == assessment_id

    illegal = await client.post(
        f"/assessment/findings/{finding_id}/transition",
        json={"status": "FIXED", "actor": "security-reviewer"},
    )
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


async def test_nuclei_target_policy_and_soft_deleted_asset_boundaries(
    client: AsyncClient,
) -> None:
    application = await client.post(
        "/assets",
        json={
            "asset_type": "APPLICATION",
            "name": "Missing URL app",
            "value": "Missing URL app",
            "criticality": "HIGH",
        },
    )
    missing_url = await client.post(
        "/assessment/nuclei",
        json={
            "asset_id": application.json()["id"],
            "templates": [TEMPLATE_ID],
            "execute": False,
        },
    )
    assert missing_url.status_code == 403
    assert missing_url.json()["error"]["code"] == "ASSESSMENT_POLICY_VIOLATION"

    website = await client.post(
        "/assets",
        json={
            "asset_type": "WEBSITE",
            "name": "Soft deleted fixture",
            "value": "https://authorized.example",
            "criticality": "HIGH",
        },
    )
    delete_response = await client.delete(f"/assets/{website.json()['id']}")
    assert delete_response.status_code == 204
    deleted = await client.post(
        "/assessment/nuclei",
        json={
            "asset_id": website.json()["id"],
            "templates": [TEMPLATE_ID],
            "execute": False,
        },
    )
    assert deleted.status_code == 404
    assert deleted.json()["error"]["code"] == "ASSET_NOT_FOUND"

    domain = await client.post(
        "/assets",
        json={
            "asset_type": "DOMAIN",
            "name": "Denied policy domain",
            "value": "authorized.example",
            "criticality": "HIGH",
        },
    )
    denied = await client.post(
        "/assessment/nuclei",
        json={
            "asset_id": domain.json()["id"],
            "templates": [TEMPLATE_ID],
            "execute": False,
            "policy": {
                "asset_denylist": [domain.json()["id"]],
                "capability_allowlist": ["template.scan", "web.scan"],
            },
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ASSESSMENT_POLICY_VIOLATION"


async def test_phase_7_models_and_migration_module_are_registered() -> None:
    assert {
        "finding_history",
        "finding_comments",
        "finding_transitions",
        "assessment_reports",
    } <= set(Base.metadata.tables)
    assert {
        AssessmentReport,
        FindingHistory,
        FindingComment,
        FindingTransition,
    }
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260731_0010_nuclei_assessment_plugin.py"
    )
    spec = importlib.util.spec_from_file_location("phase7_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "20260731_0010"
    assert module.down_revision == "20260731_0009"


async def test_nuclei_api_uses_existing_asset_and_can_plan_without_execution(
    client: AsyncClient,
) -> None:
    asset_response = await client.post(
        "/assets",
        json={
            "asset_type": "WEBSITE",
            "name": "Authorized local fixture",
            "value": "https://authorized.example",
            "criticality": "HIGH",
        },
    )
    assert asset_response.status_code == 201
    response = await client.post(
        "/assessment/nuclei",
        json={
            "asset_id": asset_response.json()["id"],
            "templates": [TEMPLATE_ID],
            "execute": False,
        },
    )
    assert response.status_code == 201
    assert response.json()["requested_capabilities"] == ["template.scan", "web.scan"]
    assert response.json()["status"] == "PLANNED"

    unknown = await client.post(
        "/assessment/nuclei",
        json={
            "asset_id": str(uuid4()),
            "templates": [TEMPLATE_ID],
            "execute": False,
        },
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "ASSET_NOT_FOUND"
