"""Deterministic URL to Website, Domain, and IP asset discovery."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.core.enums import AssetType


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    asset_type: AssetType
    name: str
    value: str
    canonical_value: str


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    website: ResolvedAsset
    domain: ResolvedAsset
    ips: tuple[ResolvedAsset, ...]


class DNSResolver(Protocol):
    async def resolve(self, hostname: str) -> list[str]: ...


class SystemDNSResolver:
    async def resolve(self, hostname: str) -> list[str]:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        )
        return sorted({str(record[4][0]) for record in records})


class AssetResolver:
    """Normalize a public URL and resolve its hostname without probing services."""

    def __init__(self, dns: DNSResolver | None = None) -> None:
        self._dns = dns or SystemDNSResolver()

    async def resolve_url(self, url: str) -> ResolutionResult:
        parsed = urlsplit(url.strip())
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Asset discovery requires an absolute HTTP or HTTPS URL")
        hostname = parsed.hostname.rstrip(".").casefold()
        normalized_port = parsed.port
        if (parsed.scheme.casefold(), normalized_port) in {
            ("http", 80),
            ("https", 443),
        }:
            normalized_port = None
        netloc = hostname if normalized_port is None else f"{hostname}:{normalized_port}"
        canonical_url = urlunsplit(
            (parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, "")
        )
        addresses = await self._dns.resolve(hostname)
        resolved_addresses = {str(ipaddress.ip_address(address)): address for address in addresses}
        ips = [
            ResolvedAsset(AssetType.IP, canonical, original, canonical)
            for canonical, original in sorted(resolved_addresses.items())
        ]
        return ResolutionResult(
            website=ResolvedAsset(AssetType.WEBSITE, canonical_url, url, canonical_url),
            domain=ResolvedAsset(AssetType.DOMAIN, hostname, hostname, hostname),
            ips=tuple(ips),
        )
