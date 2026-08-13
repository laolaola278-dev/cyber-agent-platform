"""Governed OWASP ZAP daemon API adapter."""

import re
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from app.exceptions import AssessmentExecutionError, AssessmentPolicyViolation
from app.sandbox import SandboxProvider
from app.tools.zap.contracts import (
    ZapApiClient,
    ZapExecutionRequest,
    ZapExecutionResult,
    ZapSandboxProfile,
)


class ZapAdapter:
    """Manage ZAP session/context/scan lifecycle through an injected API client."""

    def __init__(
        self,
        client: ZapApiClient,
        sandbox: SandboxProvider,
        *,
        profile: ZapSandboxProfile,
        allowed_scan_policies: frozenset[str],
    ) -> None:
        self._client = client
        self._sandbox = sandbox
        self._profile = profile
        self._allowed_policies = allowed_scan_policies

    async def execute(self, request: ZapExecutionRequest) -> ZapExecutionResult:
        target = self._validate_target(request.target)
        self._validate_policy(request)
        suffix = uuid4().hex
        session_name = f"cap-{suffix}"
        context_name = f"cap-context-{suffix}"
        started = monotonic()
        context_id = ""
        try:
            await self._client.new_session(session_name, overwrite=False)
            context_id = await self._client.new_context(context_name)
            include_regex = self._scope_regex(target)
            await self._client.include_in_context(context_name, include_regex)
            for excluded in request.policy.exclude_regexes:
                await self._client.exclude_from_context(context_name, excluded)
            await self._client.access_url(target)
            urls_discovered = 1
            if request.policy.spider_enabled:
                urls_discovered = await self._client.spider(
                    target,
                    context_name=context_name,
                    max_depth=request.policy.spider_depth,
                    max_urls=request.policy.max_urls,
                )
                if urls_discovered > request.policy.max_urls:
                    raise AssessmentPolicyViolation("ZAP spider exceeded maximum URL count")
            if request.policy.passive_scan_enabled:
                await self._client.wait_for_passive_scan(request.policy.max_scan_time_seconds)
            if request.policy.active_scan_enabled:
                await self._client.active_scan(
                    target,
                    context_id=context_id,
                    scan_policy=request.policy.scan_policy,
                    timeout_seconds=request.policy.max_scan_time_seconds,
                )
            alerts = await self._client.alerts(base_url=target, limit=request.policy.max_urls)
            version = await self._client.version()
        except AssessmentPolicyViolation:
            raise
        except Exception as error:
            raise AssessmentExecutionError(
                "ZAP API execution failed", details={"session": session_name}
            ) from error
        finally:
            try:
                await self._client.remove_session(session_name)
            except Exception:
                pass
        summary: dict[str, int] = {}
        for alert in alerts:
            risk = str(alert.get("risk") or alert.get("riskdesc") or "Informational")
            key = risk.split()[0].upper()
            summary[key] = summary.get(key, 0) + 1
        mode = "active" if request.policy.active_scan_enabled else "passive"
        requests_made = min(urls_discovered, request.policy.max_urls)
        if request.policy.active_scan_enabled:
            requests_made = min(request.policy.max_requests, max(requests_made, len(alerts)))
        return ZapExecutionResult(
            alerts=tuple(alerts),
            session_name=session_name,
            context_name=context_name,
            tool_version=version,
            mode=mode,
            scan_policy=request.policy.scan_policy,
            scan_scope=(self._scope_regex(target),),
            duration_seconds=monotonic() - started,
            urls_discovered=urls_discovered,
            requests_made=requests_made,
            alert_summary=summary,
        )

    async def status(self) -> dict[str, object]:
        try:
            version = await self._client.version()
        except Exception as error:
            return {"healthy": False, "version": None, "error": str(error)}
        return {
            "healthy": True,
            "version": version,
            "profile": {
                "cpu_limit": self._profile.cpu_limit,
                "memory_limit_mb": self._profile.memory_limit_mb,
                "timeout_seconds": self._profile.timeout_seconds,
                "network_policy": self._profile.network_policy,
            },
        }

    def _validate_policy(self, request: ZapExecutionRequest) -> None:
        policy = request.policy
        if policy.scan_policy not in self._allowed_policies:
            raise AssessmentPolicyViolation("ZAP scan policy is not allowlisted")
        if policy.max_scan_time_seconds > self._profile.timeout_seconds:
            raise AssessmentPolicyViolation("ZAP policy exceeds sandbox timeout")
        if policy.active_scan_enabled and not request.active_scan_authorized:
            raise AssessmentPolicyViolation("ZAP Active Scan requires authorized Asset approval")

    @staticmethod
    def _validate_target(target: str) -> str:
        value = target.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AssessmentPolicyViolation("ZAP target must be one HTTP(S) Asset URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise AssessmentPolicyViolation("ZAP target contains forbidden URL components")
        if any(character in value for character in {"\n", "\r", "\x00", ","}):
            raise AssessmentPolicyViolation("ZAP target contains forbidden characters")
        return value

    @staticmethod
    def _scope_regex(target: str) -> str:
        parsed = urlsplit(target)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        return f"^{re.escape(origin + path)}(?:/.*)?(?:\\?.*)?$"
