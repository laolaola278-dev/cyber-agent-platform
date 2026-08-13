"""Phase 28.5 -- controlled egress proxy tests (layer-2 SSRF defense)."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.sandbox.egress_proxy import Allowlist, EgressProxy, target_forbidden

pytestmark = [pytest.mark.security]


# -- IP policy unit ----------------------------------------------------------


def test_private_and_metadata_targets_forbidden() -> None:
    for host in (
        "127.0.0.1",
        "localhost",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.169.253",
        "::1",
        "fe80::1",
    ):
        blocked, reason = target_forbidden(host, 80)
        assert blocked, f"{host} should be forbidden (got reason={reason!r})"


def test_public_targets_allowed() -> None:
    # public test IPs (TEST-NET ranges are publicly routable semantics)
    for host in ("93.184.216.34", "1.1.1.1", "8.8.8.8"):
        blocked, reason = target_forbidden(host, 443)
        assert not blocked, f"{host} should be allowed (got {reason!r})"


def test_allowlist() -> None:
    allow = Allowlist("127.0.0.1:9000,127.0.0.1:55432")
    assert allow.allows("127.0.0.1", 9000) is True
    assert allow.allows("127.0.0.1", 55432) is True
    assert allow.allows("127.0.0.1", 8080) is False
    assert allow.allows("192.168.1.1", 9000) is False


# -- live proxy --------------------------------------------------------------


class _Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"upstream-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def upstream():
    srv = HTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.mark.asyncio
async def test_proxy_forwards_allowed_target(upstream) -> None:
    proxy = EgressProxy(host="127.0.0.1", port=0, allowlist=Allowlist(upstream))
    # port 0 -> pick free; override with a real bound port
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proxy._port = port
    await proxy.start()
    try:
        import httpx

        async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}") as client:
            resp = await client.get(f"http://{upstream}/x")
        assert resp.status_code == 200
        assert resp.content == b"upstream-ok"
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_proxy_denies_private_target_without_allowlist() -> None:
    proxy = EgressProxy(host="127.0.0.1", port=0, allowlist=Allowlist(""))
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proxy._port = port
    await proxy.start()
    try:
        import httpx

        async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}") as client:
            # 192.168.x.y is private and NOT allowlisted -> 403
            resp = await client.get("http://192.168.50.1/x")
        assert resp.status_code == 403
    finally:
        await proxy.stop()
