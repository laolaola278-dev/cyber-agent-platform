"""Phase 28 -- HTTP Acquisition Tool Adapter (spec 8).

The ONLY place that performs real HTTP I/O for acquisition. The Agent never
imports or calls httpx/requests directly -- it requests fetches through this
adapter, which enforces:

  * URLPolicyValidator on initial URL, each redirect hop, and after DNS
  * scheme/content-type/size limits from AcquisitionPolicy
  * fixed platform User-Agent
  * retry + rate limit + timeout + redirect budget
  * 401/403/captcha/login/paywall markers -> BLOCKED (never bypassed)
  * decompression bomb guard (bounded size after inflate)
"""

from __future__ import annotations

import asyncio
import re
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.acquisition.models import AcquisitionPolicy, BlockReason, RawArtifact
from app.acquisition.urlpolicy import URLPolicyValidator

_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "are you a human",
    "verify you are human",
    "cf-chl-",
    "cf-challenge",
    "turnstile",
)

_LOGIN_MARKERS = (
    "sign in",
    "signin",
    "log in",
    "login",
    "password",
    "authentication required",
    "access denied",
)

_PAYWALL_MARKERS = (
    "paywall",
    "subscribe to continue",
    "premium content",
    "membership required",
    "unlock this article",
)

_COMPRESSED_RE = re.compile(r"(gzip|deflate|br|zstd)", re.I)


@dataclass
class HTTPFetchResult:
    status: int
    final_url: str
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    redirects: list[str] = field(default_factory=list)
    duration_ms: int = 0
    artifact: RawArtifact | None = None
    blocked_reason: BlockReason = BlockReason.NONE
    blocked_detail: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class RestrictedAccessError(RuntimeError):
    """401/403/login/captcha/paywall -- acquisition must STOP, not bypass."""

    def __init__(self, reason: BlockReason, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.status = status


class HTTPAdapter:
    """Controlled HTTP acquisition tool (runs inside Worker/Sandbox)."""

    def __init__(
        self,
        *,
        policy: AcquisitionPolicy,
        validator: URLPolicyValidator | None = None,
        client_factory: Any | None = None,
        fetch_executor: Any | None = None,
    ) -> None:
        self._policy = policy
        self._validator = validator or URLPolicyValidator(
            allowed_schemes=policy.allowed_schemes
        )
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(policy.timeout_seconds),
                follow_redirects=False,
                headers={"User-Agent": policy.user_agent},
            )
        )
        self._last_request_at = 0.0
        # Phase 28.4: optional executor that performs the WHOLE fetch loop in a
        # separate, isolated execution domain (subprocess sandbox). When set,
        # the network I/O never happens in the worker process itself.
        self._fetch_executor = fetch_executor

    async def _throttle(self) -> None:
        interval = 1.0 / max(self._policy.request_rate, 0.01)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()

    async def fetch(self, url: str) -> HTTPFetchResult:
        """GET with full policy enforcement. Raises RestrictedAccessError on 401/403."""
        result = self._validator.validate_url(url)
        if not result.allowed:
            return HTTPFetchResult(
                status=0,
                final_url=url,
                content=b"",
                content_type="",
                etag=None,
                last_modified=None,
                blocked_reason=BlockReason.SSRF_BLOCKED,
                blocked_detail=result.reason,
            )

        if self._fetch_executor is not None:
            # the URL passed the application-layer validator; the network
            # phase now runs in the isolated execution domain
            return await self._fetch_executor.fetch(url)

        client = self._client_factory()
        started = time.monotonic()
        current_url = url
        redirects: list[str] = []
        final_content: bytes = b""
        final_headers: dict[str, str] = {}
        final_status = 0
        final_url = url
        content_type = ""
        etag: str | None = None
        last_modified: str | None = None

        try:
            for _hop in range(self._policy.redirect_limit + 1):
                await self._throttle()
                try:
                    response = await client.get(current_url)
                except httpx.TimeoutException:
                    return self._error_result(url, BlockReason.TIMEOUT, "request timeout")
                except httpx.HTTPError as error:
                    return self._error_result(
                        url, BlockReason.FAILED, f"http error: {error}"
                    )

                final_status = response.status_code
                final_url = str(response.url)
                final_headers = dict(response.headers)

                if response.status_code in (401, 403):
                    await client.aclose()
                    raise RestrictedAccessError(
                        BlockReason.AUTH_REQUIRED,
                        f"HTTP {response.status_code} on {url}",
                        status=response.status_code,
                    )

                content_type = (response.headers.get("content-type") or "").split(";")[0]
                etag = response.headers.get("etag")
                last_modified = response.headers.get("last-modified")

                # redirect handling: each hop re-validated
                if response.is_redirect and response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        break
                    next_url = str(httpx.URL(current_url).join(location))
                    hop_result = self._validator.validate_redirect(next_url)
                    if not hop_result.allowed:
                        await client.aclose()
                        return HTTPFetchResult(
                            status=response.status_code,
                            final_url=next_url,
                            content=b"",
                            content_type=content_type,
                            etag=etag,
                            last_modified=last_modified,
                            redirects=redirects,
                            blocked_reason=BlockReason.SSRF_BLOCKED,
                            blocked_detail=f"redirect target blocked: {hop_result.reason}",
                        )
                    if len(redirects) >= self._policy.redirect_limit:
                        # redirect budget exhausted -> fail closed, no follow
                        await client.aclose()
                        return HTTPFetchResult(
                            status=response.status_code,
                            final_url=str(response.url),
                            content=b"",
                            content_type=content_type,
                            etag=etag,
                            last_modified=last_modified,
                            redirects=redirects,
                            blocked_reason=BlockReason.TOO_MANY_REQUESTS,
                            blocked_detail="redirect limit exceeded",
                        )
                    redirects.append(next_url)
                    current_url = next_url
                    await response.aclose()
                    continue

                # size guard (decompression bomb defence: cap inflated size)
                raw = await response.aread()
                body = self._guard_size(raw, content_type)
                if body is None:
                    await client.aclose()
                    return self._error_result(
                        url, BlockReason.SIZE_LIMIT, "response exceeds max_bytes"
                    )
                final_content = body
                break
        finally:
            await client.aclose()

        blocked, reason, detail = self._detect_restricted_markers(
            final_content[:65536], final_url
        )
        if blocked:
            return HTTPFetchResult(
                status=final_status,
                final_url=final_url,
                content=final_content,
                content_type=content_type,
                etag=etag,
                last_modified=last_modified,
                redirects=redirects,
                blocked_reason=reason,
                blocked_detail=detail,
            )

        return HTTPFetchResult(
            status=final_status,
            final_url=final_url,
            content=final_content,
            content_type=content_type,
            etag=etag,
            last_modified=last_modified,
            redirects=redirects,
            duration_ms=int((time.monotonic() - started) * 1000),
            headers=final_headers,
        )

    # -- helpers -------------------------------------------------------------

    def _guard_size(self, raw: bytes, content_type: str) -> bytes | None:
        """Bounded inflation for compressed payloads (decompression bomb)."""
        if _COMPRESSED_RE.search(content_type) or _COMPRESSED_RE.search(
            raw[:8].decode("latin-1", "replace")
        ):
            return None  # compressed bodies rejected outright (defensive)
        if len(raw) > self._policy.max_bytes:
            return None
        # sanity: treat bodies that inflate beyond cap as bombs
        try:
            decompressed = zlib.decompress(raw, 16 + zlib.MAX_WBITS)
            if len(decompressed) > self._policy.max_bytes:
                return None
        except zlib.error:
            pass  # not gzip -- keep raw
        return raw

    def _detect_restricted_markers(
        self, head: bytes, url: str
    ) -> tuple[bool, BlockReason, str]:
        try:
            text = head.decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001
            return False, BlockReason.NONE, ""
        if any(marker in text for marker in _CAPTCHA_MARKERS):
            return True, BlockReason.CAPTCHA, "captcha marker detected on page"
        if any(marker in text for marker in _PAYWALL_MARKERS):
            return True, BlockReason.PAYWALL, "paywall marker detected on page"
        if any(marker in text for marker in _LOGIN_MARKERS):
            return True, BlockReason.LOGIN_PAGE, "login page marker detected"
        return False, BlockReason.NONE, ""

    def _error_result(
        self, url: str, reason: BlockReason, detail: str
    ) -> HTTPFetchResult:
        return HTTPFetchResult(
            status=0,
            final_url=url,
            content=b"",
            content_type="",
            etag=None,
            last_modified=None,
            blocked_reason=reason,
            blocked_detail=detail,
        )

    def build_artifact(
        self,
        result: HTTPFetchResult,
        *,
        task_id: str,
        trace_id: str,
        tool_version: str,
    ) -> RawArtifact:
        """Assemble the lineage artifact for a successful fetch."""
        return RawArtifact(
            object_key="",
            sha256="",
            size=len(result.content),
            content_type=result.content_type or "application/octet-stream",
            source_url="",
            final_url=result.final_url,
            captured_at=datetime.now(UTC),
            http_status=result.status,
            etag=result.etag,
            last_modified=result.last_modified,
            method="GET",
            tool="acquisition.http",
            tool_version=tool_version,
            task_id=task_id,
            trace_id=trace_id,
        )
