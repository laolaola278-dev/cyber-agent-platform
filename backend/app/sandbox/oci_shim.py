"""Phase 28.5 -- in-container execution shim (SELF-CONTAINED).

Runs INSIDE the OCI sandbox container with ZERO dependencies on the worker
codebase: only ``sandbox/oci_protocol.py`` (copied into the image) and the
minimal HTTP/browser libraries. No DB, no sessions, no services.

The shim re-applies the application-layer URL policy from the request's
policy snapshot (defense-in-depth layer 1); the container network layer
(enforced by the runtime OUTSIDE this process) is layer 2.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import sys
from urllib.parse import urlparse

try:  # image layout: /sandbox/oci_protocol.py (self-contained copy)
    from sandbox.oci_protocol import (
        PROTOCOL_VERSION,
        SandboxRequest,
        SandboxResponse,
    )
except ModuleNotFoundError:  # worker-side layout (tests / local run)
    from app.sandbox.oci_protocol import (
        PROTOCOL_VERSION,
        SandboxRequest,
        SandboxResponse,
    )


def _host_ips(host: str) -> list[str]:
    try:
        return [i[4][0] for i in socket.getaddrinfo(host, None)]
    except OSError:
        return []


def _is_forbidden(ip: str) -> tuple[bool, str]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True, "unparseable IP"
    if addr.is_loopback:
        return True, "loopback"
    if addr.is_private:
        return True, "private (RFC1918)"
    if addr.is_link_local:
        return True, "link-local"
    if addr.is_multicast:
        return True, "multicast"
    if str(addr) in ("169.254.169.254", "169.254.169.253"):
        return True, "cloud metadata"
    if addr.is_reserved:
        return True, "reserved"
    if addr.version == 6 and addr.is_global is False:
        return True, "non-global IPv6"
    return False, ""


def _validate_url(request: SandboxRequest) -> tuple[bool, str]:
    """L7 policy re-check inside the container (snapshot of the worker's
    validator). Mirrors URLPolicyValidator's public-only gate."""
    policy = request.policy
    parsed = urlparse(request.url)
    if parsed.scheme not in policy.allowed_schemes:
        return False, f"scheme {parsed.scheme} not allowed"
    if policy.allow_private:
        return True, ""
    for ip in _host_ips(parsed.hostname or ""):
        blocked, reason = _is_forbidden(ip)
        if blocked:
            return False, f"{parsed.hostname} resolves to {reason} ({ip})"
    return True, ""


def _blocked_result(url: str, reason: str) -> dict:
    return {
        "status": 0,
        "final_url": url,
        "content_b64": "",
        "content_type": "",
        "etag": None,
        "last_modified": None,
        "blocked_reason": "SSRF_BLOCKED",
        "blocked_detail": reason,
        "duration_ms": 0,
    }


def _failed_result(url: str, error: str) -> dict:
    return {
        "status": 0,
        "final_url": url,
        "content_b64": "",
        "content_type": "",
        "etag": None,
        "last_modified": None,
        "blocked_reason": None,
        "blocked_detail": error,
        "duration_ms": 0,
    }


async def _http_fetch(request: SandboxRequest) -> dict:
    import httpx

    ok, reason = _validate_url(request)
    if not ok:
        return _blocked_result(request.url, reason)

    # egress proxy: configured by the runtime (HTTP(S)_PROXY env). The proxy
    # is the network-layer second line and rejects private/metadata targets.
    proxy_url = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )
    timeout = httpx.Timeout(request.policy.timeout_seconds)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": request.policy.user_agent},
        proxy=proxy_url,
        limits=httpx.Limits(max_connections=8),
    ) as client:
        try:
            resp = await client.get(request.url)
        except httpx.HTTPError as error:
            reason_blocked = "TIMEOUT" if isinstance(error, httpx.TimeoutException) else None
            result = _failed_result(request.url, str(error)[:300])
            result["blocked_reason"] = reason_blocked
            return result
        content = resp.content[: request.policy.max_response_bytes]
        return {
            "status": resp.status_code,
            "final_url": str(resp.url),
            "content_b64": __import__("base64").b64encode(content).decode("ascii"),
            "content_type": resp.headers.get("content-type", ""),
            "etag": resp.headers.get("etag"),
            "last_modified": resp.headers.get("last-modified"),
            "blocked_reason": None,
            "blocked_detail": None,
            "duration_ms": 0,
        }


async def _browser_browse(request: SandboxRequest) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:  # pragma: no cover -- browser image only
        return {
            "url": request.url,
            "final_url": request.url,
            "status": None,
            "html": "",
            "title": "",
            "endpoints": [],
            "available": False,
            "error": f"playwright unavailable: {error}",
        }

    ok, reason = _validate_url(request)
    if not ok:
        return {
            "url": request.url,
            "final_url": request.url,
            "status": None,
            "html": "",
            "title": "",
            "endpoints": [],
            "available": False,
            "error": f"blocked by URL policy: {reason}",
        }

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"],  # the container is the sandbox
        )
        try:
            page = await browser.new_page()
            endpoints: list[dict] = []

            async def _on_request(req) -> None:
                if req.resource_type in ("xhr", "fetch") and req.url.startswith("http"):
                    endpoints.append({"url": req.url, "method": req.method})

            page.on("request", _on_request)
            response = await page.goto(request.url, wait_until="domcontentloaded")
            if request.wait_network_idle_ms:
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=request.wait_network_idle_ms
                    )
                except Exception:  # noqa: BLE001 -- best effort
                    pass
            title = await page.title()
            html = await page.content()
            return {
                "url": request.url,
                "final_url": response.url if response else request.url,
                "status": response.status if response else None,
                "html": html,
                "title": title,
                "endpoints": endpoints,
                "available": True,
                "error": "",
            }
        finally:
            await browser.close()


async def _dispatch(request: SandboxRequest) -> SandboxResponse:
    try:
        if request.operation == "http_fetch":
            result = await _http_fetch(request)
        elif request.operation == "browser_browse":
            result = await _browser_browse(request)
        else:  # pragma: no cover -- validated upstream
            return SandboxResponse(
                version=request.version,
                status="error",
                error=f"unknown operation {request.operation}",
            )
        return SandboxResponse(version=request.version, status="ok", result=result)
    except Exception as error:  # noqa: BLE001 -- report, never crash the shim
        return SandboxResponse(
            version=request.version,
            status="error",
            error=str(error)[:500],
            error_type=type(error).__name__,
        )


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8")
    try:
        request = SandboxRequest.model_validate_json(raw)
    except Exception as error:  # noqa: BLE001
        response = SandboxResponse(
            version=PROTOCOL_VERSION,
            status="error",
            error=f"invalid request: {error}",
        )
        sys.stdout.write(response.model_dump_json())
        return 1
    response = asyncio.run(_dispatch(request))
    sys.stdout.write(response.model_dump_json())
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
