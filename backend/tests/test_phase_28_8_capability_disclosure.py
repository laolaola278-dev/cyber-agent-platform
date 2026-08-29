"""Phase 28.8 (v1.0.1) -- capability disclosure and fail-closed response config.

PATCH-GATE 5   Proxy unhealthy: no direct fallback
PATCH-GATE 9   Response mock-only disclosed
PATCH-GATE 10  mock_only=False rejected
PATCH-GATE 11  Zeek JSONL-only disclosed
PATCH-GATE 12  Zeek TSV actionable error
PATCH-GATE 21  Reserved interfaces disclosed
PATCH-GATE 22  AssessmentScheduler recorded as unimplemented
"""

from __future__ import annotations

import asyncio
import inspect
import socket
from pathlib import Path

import pytest

from app.acquisition.health import WorkerHealth
from app.tools.edr.policy import EDRPolicy
from app.tools.firewall.policy import FirewallPolicy
from app.tools.waf.policy import WAFPolicy
from app.tools.zeek.adapter import ZeekAdapter
from app.tools.zeek.contracts import ZeekSandboxProfile

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN_ISSUES = REPO_ROOT / "docs" / "known-issues.md"


def _unused_port() -> int:
    """Reserve then release a port so connecting to it is refused."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --------------------------------------------------------------------------
# PATCH-GATE 5 -- proxy unhealthy has no direct fallback
# --------------------------------------------------------------------------


async def test_egress_enforcement_fails_when_proxy_unreachable() -> None:
    """Unreachable proxy -> not ready. Nothing here may switch to direct egress."""

    health = WorkerHealth(
        egress_proxy_url=f"http://127.0.0.1:{_unused_port()}",
        require_egress_enforcement=True,
        egress_probe_timeout_seconds=2.0,
    )
    assert await health._check_egress_enforcement() is False


async def test_egress_enforcement_passes_when_proxy_listens() -> None:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        health = WorkerHealth(
            egress_proxy_url=f"http://127.0.0.1:{port}",
            require_egress_enforcement=True,
            egress_probe_timeout_seconds=2.0,
        )
        assert await health._check_egress_enforcement() is True
    finally:
        server.close()
        await server.wait_closed()


async def test_egress_enforcement_not_required_outside_production() -> None:
    """A development worker is not blocked by an unconfigured proxy."""

    health = WorkerHealth(
        egress_proxy_url="",
        require_egress_enforcement=False,
    )
    assert await health._check_egress_enforcement() is True


async def test_egress_enforcement_required_but_empty_is_not_ready() -> None:
    health = WorkerHealth(
        egress_proxy_url="",
        require_egress_enforcement=True,
    )
    assert await health._check_egress_enforcement() is False


async def test_egress_enforcement_rejects_unparseable_url() -> None:
    health = WorkerHealth(
        egress_proxy_url="not-a-url",
        require_egress_enforcement=True,
        egress_probe_timeout_seconds=1.0,
    )
    assert await health._check_egress_enforcement() is False


def test_readiness_surface_includes_egress_enforcement() -> None:
    """The check is wired into readiness, not just defined."""

    source = inspect.getsource(WorkerHealth.readiness)
    assert "egress_enforcement" in source
    assert "_check_egress_enforcement" in source


# --------------------------------------------------------------------------
# PATCH-GATE 10 -- mock_only cannot be switched off by configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [EDRPolicy, WAFPolicy, FirewallPolicy],
    ids=["edr", "waf", "firewall"],
)
def test_mock_only_cannot_be_disabled(policy: type) -> None:
    """A production operator cannot turn a simulation into a real provider."""

    with pytest.raises(ValueError, match="mock-only"):
        policy(mock_only=False)


def test_default_policies_are_mock_only() -> None:
    assert EDRPolicy().mock_only is True
    assert WAFPolicy().mock_only is True
    assert FirewallPolicy().mock_only is True


def test_no_real_response_provider_implementation_exists() -> None:
    """Disclosure must not drift: if a real provider lands, the docs must move.

    A provider whose class name does not announce itself as a mock is exactly
    the thing `docs/known-issues.md` says does not exist.
    """

    tools_root = Path(__file__).resolve().parents[1] / "app" / "tools"
    offenders: list[str] = []
    for provider_file in tools_root.glob("*/provider.py"):
        for line in provider_file.read_text(encoding="utf-8").splitlines():
            if not line.startswith("class "):
                continue
            name = line[len("class ") :].split("(")[0].split(":")[0].strip()
            if "Provider" not in name:
                continue
            if any(marker in name for marker in ("Mock", "Policy", "Base", "Abstract", "Stub")):
                continue
            offenders.append(f"{provider_file.parent.name}/{name}")
    assert offenders == [], (
        "a non-mock response provider exists; docs/known-issues.md response-plane "
        f"disclosure is now stale: {offenders}"
    )


# --------------------------------------------------------------------------
# PATCH-GATE 9 / 11 / 21 / 22 -- the disclosures themselves
# --------------------------------------------------------------------------


def _known_issues() -> str:
    return KNOWN_ISSUES.read_text(encoding="utf-8")


def test_response_plane_mock_only_is_disclosed() -> None:
    """PATCH-GATE 9: operators must learn this from the release notes."""

    text = _known_issues()
    assert "MockEDRProvider" in text
    assert "MockWAFProvider" in text
    assert "MockFirewallProvider" in text
    assert "mock-edr" in text and "mock-waf" in text and "mock-firewall" in text
    # it must say what they are, not just name them
    assert "integrated with any production EDR, WAF or firewall device" in text
    assert "simulation/test providers only" in text


def test_edr_action_detail_is_disclosed_accurately() -> None:
    """No capability may be claimed beyond the code."""

    text = _known_issues()
    assert "process.terminate" in text
    assert "collect.package" in text
    assert "Reserved / unavailable" in text
    # host.isolate/unisolate are simulated, not real
    assert "host.isolate" in text and "Simulated" in text


def test_zeek_jsonl_only_is_disclosed() -> None:
    """PATCH-GATE 11: JSONL is supported; the doc must not call Zeek unsupported."""

    text = _known_issues()
    assert "JSONL" in text
    assert "LogAscii::use_json=T" in text
    assert "not `Zeek is unsupported`" not in text
    assert "not implemented" in text


def test_reserved_interfaces_are_disclosed() -> None:
    """PATCH-GATE 21: an import name must not read as a capability."""

    text = _known_issues()
    assert "FirecrackerSandbox" in text
    assert "DockerSandbox" in text
    assert "RemoteWorkerSandbox" in text
    assert "never instantiated at runtime" in text


def test_assessment_scheduler_is_recorded() -> None:
    """PATCH-GATE 22."""

    text = _known_issues()
    assert "AssessmentScheduler" in text
    assert "never called" in text


def test_egress_threat_model_is_stated_precisely() -> None:
    """Missing proxy is not 'no SSRF protection'; layer 1 still runs."""

    from app.sandbox.production import validate_production_egress

    settings = type(
        "S",
        (),
        {
            "sandbox_provider": "oci-sandbox",
            "egress_proxy_url": "",
        },
    )()
    violations = validate_production_egress(settings)
    assert violations
    assert "no SSRF protection" not in violations[0]
    assert "Application-layer URL validation (layer 1) remains active" in violations[0]


# --------------------------------------------------------------------------
# PATCH-GATE 12 -- Zeek TSV error is actionable
# --------------------------------------------------------------------------


def _zeek_adapter() -> ZeekAdapter:
    return ZeekAdapter(sources={}, profile=ZeekSandboxProfile())


def test_zeek_tsv_error_names_the_remediation() -> None:
    adapter = _zeek_adapter()
    with pytest.raises(Exception) as error:
        adapter.parse_tsv("#fields\tts\tuid\n")
    message = str(error.value)
    assert "TSV" in message
    assert "not supported" in message
    assert "JSON" in message
    assert "LogAscii::use_json=T" in message


def test_zeek_tsv_error_carries_structured_details() -> None:
    from app.exceptions import DetectionPolicyViolation

    adapter = _zeek_adapter()
    with pytest.raises(DetectionPolicyViolation) as error:
        adapter.parse_tsv("#fields\tts\tuid\n")
    details = error.value.details
    assert details["supported_input_format"] == "jsonl"
    assert details["rejected_input_format"] == "tsv"
    assert "LogAscii::use_json=T" in details["remediation"]


def test_zeek_jsonl_parsing_still_works() -> None:
    """Disclosing a boundary must not imply the adapter is broken."""

    adapter = _zeek_adapter()
    lines = [
        '{"ts": 1.0, "uid": "C1", "id.orig_h": "10.0.0.1"}',
        '{"ts": 2.0, "uid": "C2", "id.orig_h": "10.0.0.2"}',
    ]
    envelopes = adapter.parse_jsonl("\n".join(lines), source_id="zeek", source_sha256="abc")
    assert len(envelopes) == 2
    assert envelopes[0].payload["uid"] == "C1"
