"""Phase 28 -- URLPolicyValidator (SSRF protection, spec 16).

Every URL that is acquired -- initial, after redirect, and after DNS
resolution -- must pass this validator. Public-only by default:

  * scheme whitelist (http/https only; file/ftp/gopher/data/javascript
    and unix sockets rejected)
  * hostname checks: localhost, loopback, RFC1918 private, link-local,
    metadata endpoints (169.254.169.254), IPv6 loopback ::1
  * userinfo (user:pass@) rejected
  * DNS resolution is re-validated (DNS rebinding defence): the IPs the
    name actually resolves to must be public.
  * redirect targets are validated again.

Authorized internal assets are a future explicit-policy feature; this
phase is public-only.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# Hosts never allowed regardless of policy (metadata / internal infra).
_BLOCKED_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
)

_BLOCKED_EXACT_HOSTS = {
    "metadata.google.internal",
    "metadata.google.internal.",
    "instance-data",
    "instance-data.",
    "kubernetes.default.svc",
    "kubernetes.default",
}


@dataclass
class URLValidationResult:
    allowed: bool
    reason: str
    final_host: str | None = None
    resolved_ips: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.allowed


def _is_private_ip(ip: ipaddress._BaseAddress) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # metadata service ranges not always flagged by stdlib
    if ip.version == 4 and ip.exploded.startswith("169.254."):
        return True
    return False


class URLPolicyValidator:
    """Deterministic public-only URL validation with DNS re-check."""

    def __init__(
        self,
        *,
        allowed_schemes: tuple[str, ...] = ("http", "https"),
        allow_private: bool = False,
        resolver: Any | None = None,
    ) -> None:
        self._allowed_schemes = allowed_schemes
        self._allow_private = allow_private
        # resolver(url) -> list[str] of IP strings; injectable for tests
        self._resolver = resolver or self._default_resolver

    @staticmethod
    def _default_resolver(host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return []
        seen: list[str] = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen:
                seen.append(ip)
        return seen

    def _host_allowed(self, host: str) -> tuple[bool, str]:
        if not host:
            return False, "empty host"
        lower = host.rstrip(".").lower()
        if lower in _BLOCKED_EXACT_HOSTS:
            return False, f"blocked host {host}"
        for suffix in _BLOCKED_HOST_SUFFIXES:
            if lower == suffix.lstrip(".") or lower.endswith(suffix):
                return False, f"blocked internal host suffix {suffix}"
        if lower in ("localhost", "localhost.", "ip6-localhost"):
            return False, "localhost"
        # IP-literal hosts are checked directly (no DNS involved)
        try:
            literal_ip = ipaddress.ip_address(lower)
        except ValueError:
            literal_ip = None
        if literal_ip is None:
            # hex/octal/decimal IP literals: 0x7f000001, 2130706433 ...
            try:
                literal_ip = ipaddress.ip_address(int(lower, 0))
            except (ValueError, TypeError):
                literal_ip = None
        if literal_ip is not None:
            if _is_private_ip(literal_ip):
                return False, f"IP literal is not public: {lower}"
            return True, "public IP literal"
        try:
            ips = self._resolver(lower)
        except Exception:  # noqa: BLE001 -- resolution failure fails closed
            return False, f"DNS resolution failed for {host}"
        if not ips:
            return False, f"no DNS records for {host}"
        for ip_text in ips:
            try:
                ip = ipaddress.ip_address(ip_text)
            except ValueError:
                continue
            if _is_private_ip(ip):
                return False, f"resolved to non-public IP {ip_text}"
        return True, "public"

    def validate_url(self, url: str) -> URLValidationResult:
        """Validate a URL string; rejects blocked schemes/hosts/userinfo."""
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            return URLValidationResult(False, f"malformed URL: {error}")
        scheme = parsed.scheme.lower()
        if scheme not in self._allowed_schemes:
            return URLValidationResult(False, f"scheme {scheme!r} not allowed")
        if parsed.username or parsed.password:
            return URLValidationResult(False, "userinfo (user:pass@) is forbidden")
        if parsed.netloc.count("@") > 0:
            return URLValidationResult(False, "userinfo present in netloc")
        host = (parsed.hostname or "").lower()
        if self._allow_private:
            return URLValidationResult(True, "explicit private allowance")
        allowed, reason = self._host_allowed(host)
        if not allowed:
            return URLValidationResult(False, reason, final_host=host)
        resolved = self._resolver(host)
        return URLValidationResult(True, reason, final_host=host, resolved_ips=resolved)

    def validate_redirect(self, url: str) -> URLValidationResult:
        """Redirect targets are re-validated (never trusted implicitly)."""
        return self.validate_url(url)


# Shared module-level default validator (stateless).
default_validator = URLPolicyValidator()
