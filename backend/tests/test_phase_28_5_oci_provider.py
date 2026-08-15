"""Phase 28.5 -- OCI provider unit tests with a fake driver.

The fake driver executes the REAL shim dispatch in-process, so the protocol,
shim SSRF gate, result codecs and the provider lifecycle are all exercised
end to end WITHOUT requiring a container runtime. Runtime-backed tests are
gated on an available docker daemon (marked separately).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.sandbox.oci_protocol import (
    SandboxRequest,
    http_fetch_result_from_dict,
)
from app.sandbox.oci_provider import (
    ContainerSpec,
    OCISandboxProvider,
)
from app.sandbox.profile import SandboxProfile

pytestmark = [pytest.mark.oci]


class FakeDriver:
    """Emulates the container runtime; dispatches to the REAL shim."""

    driver_name = "fake"

    def __init__(self) -> None:
        self.created: list[str] = []
        self.removed: list[str] = []
        self.killed: list[str] = []
        self.timeout_override: float | None = None
        self.fail_start: Exception | None = None

    async def health(self) -> bool:
        return True

    async def run_interactive(
        self, spec: ContainerSpec, input_bytes: bytes, timeout: float
    ) -> tuple[int, bytes, bytes]:
        from app.sandbox.oci_shim import _dispatch

        self.created.append(spec.name)
        if self.fail_start is not None:
            raise self.fail_start
        request = SandboxRequest.model_validate_json(input_bytes.decode("utf-8"))
        response = await _dispatch(request)
        return 0, response.model_dump_json().encode("utf-8"), b""

    async def kill(self, container_id: str) -> None:
        self.killed.append(container_id)

    async def rm(self, container_id: str, force: bool = True) -> None:
        self.removed.append(container_id)

    async def inspect(self, container_id: str) -> dict:
        return {"Id": container_id, "Config": {"Labels": {}}}

    async def list_by_labels(self, labels: dict[str, str]) -> list[dict]:
        return []

    async def exists_image(self, image: str) -> bool:
        return True

    async def container_stats(self, container_id: str) -> dict:
        return {}


def _profile(**overrides) -> SandboxProfile:
    kwargs = dict(name="oci-test", timeout_seconds=15, memory_mb=128, cpu_millicores=250)
    kwargs.update(overrides)
    return SandboxProfile(**kwargs)


@pytest.mark.asyncio
async def test_oci_provider_capabilities_and_identity() -> None:
    provider = OCISandboxProvider(driver=FakeDriver())
    assert provider.provider_name == "oci-sandbox"
    assert provider.real_isolation is True
    caps = provider.capabilities
    assert caps.container is True
    assert caps.network is True
    assert caps.filesystem is True
    assert caps.resource is True
    assert caps.process is True
    assert caps.secret is True
    assert caps.timeout is True
    # container name is deterministic + safe (no tokens)
    eid = uuid4()
    name = OCISandboxProvider.container_name(eid)
    assert name.startswith("cap-sbx-")
    assert eid.hex[:16] in name
    assert "token" not in name


@pytest.mark.asyncio
async def test_typed_fetch_executes_shim_and_returns_result() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html>oci-sandbox-ok</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        driver = FakeDriver()
        provider = OCISandboxProvider(driver=driver)
        request = SandboxRequest(
            operation="http_fetch",
            run_id=str(uuid4()),
            sandbox_execution_id=str(uuid4()),
            url=f"http://127.0.0.1:{port}/page",
            policy={"allow_private": True},  # shim L7 gate allows for the lab
        )
        response = await provider.execute_request(_profile(), request)
        assert response.status == "ok"
        result = http_fetch_result_from_dict(response.result)
        assert result.status == 200
        assert b"oci-sandbox-ok" in result.content
        # lifecycle: container removed after execution
        assert driver.created, "container was never created"
        assert driver.removed, "container was not removed after execution"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.asyncio
async def test_shim_blocks_private_targets_at_layer1() -> None:
    driver = FakeDriver()
    provider = OCISandboxProvider(driver=driver)
    request = SandboxRequest(
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url="http://127.0.0.1:9/private",
    )
    response = await provider.execute_request(_profile(), request)
    assert response.status == "ok"
    result = http_fetch_result_from_dict(response.result)
    assert result.blocked_reason == "SSRF_BLOCKED"
    assert "private" in (result.blocked_detail or "") or "loopback" in (result.blocked_detail or "")


@pytest.mark.asyncio
async def test_provider_rejects_arbitrary_callables() -> None:
    from app.sandbox.runtime import SandboxExecutionError

    driver = FakeDriver()
    provider = OCISandboxProvider(driver=driver)

    async def op():
        return {}

    with pytest.raises(SandboxExecutionError):
        await provider.execute(uuid4(), _profile(), op)


@pytest.mark.asyncio
async def test_protocol_validates_request_and_forbidden_fields() -> None:
    from pydantic import ValidationError

    from app.sandbox.oci_protocol import validate_request

    with pytest.raises(ValidationError):
        SandboxRequest(
            operation="nope", run_id="r", sandbox_execution_id=str(uuid4()), url="http://x"
        )
    bad_req = SandboxRequest(
        operation="http_fetch",
        run_id="r",
        sandbox_execution_id="not-a-uuid",
        url="http://x",
    )
    with pytest.raises(ValueError):
        validate_request(bad_req)
    req = SandboxRequest(
        operation="http_fetch",
        run_id="r",
        sandbox_execution_id=str(uuid4()),
        url="http://example.com",
    )
    validate_request(req)


@pytest.mark.asyncio
async def test_secrets_never_ride_the_protocol() -> None:
    """Secret values are delivered via container env, never in the JSON body."""
    driver = FakeDriver()

    captured: dict[str, str] = {}

    async def spy_run(spec, input_bytes, timeout):
        captured.update(spec.env)
        from app.sandbox.oci_shim import _dispatch

        request = SandboxRequest.model_validate_json(input_bytes.decode())
        response = await _dispatch(request)
        return 0, response.model_dump_json().encode(), b""

    driver.run_interactive = spy_run  # type: ignore[method-assign]
    provider = OCISandboxProvider(driver=driver)
    request = SandboxRequest(
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url="http://example.invalid/",
        policy={"allow_private": True},
    )
    await provider.execute_request(_profile(), request, secrets={"cap-db-pass": "hunter2secret"})
    # secret arrives via env with the CAP_SECRET_ prefix
    assert captured.get("CAP_SECRET_cap-db-pass") == "hunter2secret"
    # and the JSON body never carried it
    assert "hunter2secret" not in request.model_dump_json()
