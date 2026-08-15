"""Phase 28.5 -- controlled egress proxy (network-layer SSRF second line).

The sandbox container's ONLY egress is this proxy (the sandbox joins an
isolated bridge network where this proxy is the sole gateway to the outside).
The proxy validates every destination:

  * loopback / RFC1918 / link-local / cloud-metadata / reserved /
    non-global IPv6  -> 403 (never forwarded)
  * public targets   -> forwarded
  * explicit test allowlist (CAP_EGRESS_ALLOW, e.g. a local lab server) ->
    forwarded ONLY for the listed host:port

This is layer 2: URLPolicyValidator (layer 1) still runs inside the sandbox
shim. A validator bypass that connects straight to a private IP still fails
here -- that is the defense-in-depth certification.

Only CONNECT (HTTPS) and forwardable HTTP methods are supported; the proxy
never terminates TLS and never decrypts traffic.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

logger = logging.getLogger("cap.sandbox.egress")

_FORBIDDEN_META = {"169.254.169.254", "169.254.169.253", "169.254.169.123"}


def target_forbidden(host: str, port: int) -> tuple[bool, str]:
    """IP-level policy: is this destination forbidden? (layer 2)"""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return True, "resolution failed"
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True, "unparseable IP"
        if str(ip) in _FORBIDDEN_META:
            return True, "cloud metadata"
        if addr.is_loopback:
            return True, "loopback"
        if addr.is_private:
            return True, "private (RFC1918)"
        if addr.is_link_local:
            return True, "link-local"
        if addr.is_multicast:
            return True, "multicast"
        if addr.is_reserved:
            return True, "reserved"
        if addr.version == 6 and addr.is_global is False:
            return True, "non-global IPv6"
    return False, ""


class Allowlist:
    """Explicit test allowlist (host:port). Production default: empty."""

    def __init__(self, raw: str | None = None) -> None:
        self._entries: set[tuple[str, int]] = set()
        for entry in (raw or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                host, _, port = entry.rpartition(":")
                try:
                    self._entries.add((host.strip(), int(port)))
                except ValueError:
                    continue

    def allows(self, host: str, port: int) -> bool:
        return (host, port) in self._entries


class EgressProxy:
    """Asyncio forward proxy (CONNECT + plain HTTP) with SSRF filtering."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
        allowlist: Allowlist | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._allowlist = allowlist or Allowlist(os.environ.get("CAP_EGRESS_ALLOW"))
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)
        logger.info("egress proxy listening on %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- connection handling -------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await asyncio.wait_for(reader.readline(), timeout=10)
            if not first:
                return
            parts = first.decode("latin-1", errors="replace").split()
            if not parts:
                return
            method, target = parts[0].upper(), parts[1]
            if method == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_http(reader, writer, method, target, first)
        except Exception as error:  # noqa: BLE001
            logger.debug("egress proxy connection error: %s", error)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str
    ) -> None:
        host, _, port_s = target.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            await self._deny(writer, "bad target")
            return
        if await self._deny_if_forbidden(writer, host, port):
            return
        # forward the TCP stream
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except OSError:
            await self._deny(writer, "upstream unreachable")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        first: bytes,
    ) -> None:
        parsed = urlparse(target if "://" in target else f"http://{target}")
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if await self._deny_if_forbidden(writer, host, port):
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except OSError:
            await self._deny(writer, "upstream unreachable")
            return
        # forward the request verbatim + drain the rest of the request body
        up_writer.write(first)
        await up_writer.drain()
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _deny_if_forbidden(self, writer: asyncio.StreamWriter, host: str, port: int) -> bool:
        if self._allowlist.allows(host, port):
            return False
        blocked, reason = target_forbidden(host, port)
        if blocked:
            await self._deny(writer, f"forbidden by egress policy: {reason}")
            return True
        return False

    async def _deny(self, writer: asyncio.StreamWriter, reason: str) -> None:
        logger.warning("egress proxy denied %s", reason)
        try:
            writer.write(
                f"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(reason) + 1}\r\n\r\n{reason}\n".encode()
            )
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    async def _pipe(
        c_reader: asyncio.StreamReader,
        c_writer: asyncio.StreamWriter,
        u_reader: asyncio.StreamReader,
        u_writer: asyncio.StreamWriter,
    ) -> None:
        async def _forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:  # noqa: BLE001
                    pass

        tasks = [
            asyncio.create_task(_forward(c_reader, u_writer)),
            asyncio.create_task(_forward(u_reader, c_writer)),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_egress_proxy(*, port: int = 8080) -> None:
    proxy = EgressProxy(port=port)
    await proxy.start()
    try:
        await asyncio.Event().wait()  # run until cancelled
    finally:
        await proxy.stop()
