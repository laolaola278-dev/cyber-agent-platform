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


# -- CONNECT tunnel regression (v1.0.2) ---------------------------------------
#
# The CONNECT handler used to consume only the request line; the remaining
# request headers stayed in the StreamReader buffer and were piped to the
# upstream as tunnel payload, corrupting the tunneled protocol (real-world
# symptom: TLS through the proxy failed WRONG_VERSION_NUMBER, HTTP upstreams
# answered 400 "malformed HTTP request \"Host: ...\""). These tests drive a
# raw CONNECT client so the stray-header path is exercised exactly as in
# production.


async def _connect_through_proxy(proxy_port: int, target: str, *, with_headers: bool) -> bytes:
    """Open a raw CONNECT tunnel and issue one HTTP GET inside it.

    Returns the full upstream response bytes (headers + body).
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    try:
        request = f"CONNECT {target} HTTP/1.1\r\n"
        if with_headers:
            request += f"Host: {target}\r\nProxy-Connection: keep-alive\r\n"
        request += "\r\n"
        writer.write(request.encode())
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=10)
        assert b"200" in status_line, f"CONNECT rejected: {status_line!r}"
        # consume the blank line terminating the 200 response
        await asyncio.wait_for(reader.readline(), timeout=10)
        # tunnel payload: a plain HTTP request to the upstream
        writer.write(f"GET /x HTTP/1.1\r\nHost: {target}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        return await asyncio.wait_for(reader.read(), timeout=10)
    finally:
        writer.close()
        await writer.wait_closed()


async def _start_proxy_on_free_port(allowlist: Allowlist) -> tuple[EgressProxy, int]:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proxy = EgressProxy(host="127.0.0.1", port=port, allowlist=allowlist)
    await proxy.start()
    return proxy, port


@pytest.mark.asyncio
async def test_connect_tunnel_with_headers_delivers_upstream_body(upstream) -> None:
    """CONNECT carrying header lines must not leak them into the tunnel."""
    proxy, port = await _start_proxy_on_free_port(Allowlist(upstream))
    try:
        response = await _connect_through_proxy(port, upstream, with_headers=True)
    finally:
        await proxy.stop()
    first_line = response.splitlines()[0]
    assert b" 200 " in first_line, f"upstream rejected the request: {response[:200]!r}"
    assert response.endswith(b"upstream-ok"), (
        "CONNECT header leak: upstream saw stray headers instead of the GET"
    )


@pytest.mark.asyncio
async def test_connect_tunnel_without_headers_still_works(upstream) -> None:
    """A minimal CONNECT (request line + blank line) must not stall or leak."""
    proxy, port = await _start_proxy_on_free_port(Allowlist(upstream))
    try:
        response = await _connect_through_proxy(port, upstream, with_headers=False)
    finally:
        await proxy.stop()
    assert b" 200 " in response.splitlines()[0]
    assert response.endswith(b"upstream-ok")
