"""Phase 28.8 (v1.0.1) -- targeted security coverage for the isolation plane.

PATCH-GATE 14/15/16: the sandbox, egress and pod providers were previously
exercised almost exclusively along their happy paths (47-72% coverage). The
uncovered branches are exactly the ones that matter when something goes
wrong: denial paths, timeouts, cleanup-on-failure, fail-closed errors. This
file drives those branches directly, without any external infrastructure:

* ``egress_proxy``   -- every denial class, CONNECT tunnel, upstream failure,
                        malformed input, IPv4-first resolution failures
* ``oci_provider``   -- DockerCLIDriver subprocess surface (timeout/health/
                        inspect/labels), provider timeout (exit 124), non-zero
                        exit, invalid shim JSON, egress env, terminate, health
* ``k8s_provider``   -- pod-create failure, readiness timeout with stall
                        diagnostics, missing pod IP, shim non-200, cleanup on
                        every path, terminate failure, health probe semantics,
                        orphan reconciliation, secret refusal

All network targets are loopback listeners or monkeypatched resolvers; no
test contacts the public internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import uuid4

import pytest

from app.sandbox import egress_proxy as egress_mod
from app.sandbox import k8s_provider as k8s_mod
from app.sandbox.egress_proxy import Allowlist, EgressProxy, target_forbidden
from app.sandbox.k8s_provider import (
    LABEL_EXECUTION,
    LABEL_MANAGED,
    KubernetesSandboxError,
    KubernetesSandboxProvider,
)
from app.sandbox.oci_protocol import PROTOCOL_VERSION, SandboxRequest
from app.sandbox.oci_provider import (
    ContainerRuntimeError,
    ContainerSpec,
    DockerCLIDriver,
    OCISandboxProvider,
)
from app.sandbox.profile import SandboxProfile
from app.sandbox.runtime import SandboxExecutionError

pytestmark = [pytest.mark.security]


# ---------------------------------------------------------------------------
# egress proxy -- IP policy denial classes
# ---------------------------------------------------------------------------


def test_resolution_failure_is_forbidden(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    blocked, reason = target_forbidden("anything.example", 80)
    assert blocked is True
    assert reason == "resolution failed"


def test_unparseable_address_is_forbidden(monkeypatch) -> None:
    def _bad_ip(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _bad_ip)
    blocked, reason = target_forbidden("weird.example", 80)
    assert blocked is True
    assert reason == "unparseable IP"


class _FakeAddr:
    """Version-independent stand-in for ``ipaddress.ip_address``.

    Python 3.13 widened ``is_private`` to swallow link-local, reserved and
    ULA ranges, so on that interpreter those addresses hit the ``private``
    branch before the more specific ones. The security invariant under test
    is that each address CLASS is denied; driving the flags directly keeps
    every denial branch reachable regardless of interpreter semantics.
    """

    def __init__(
        self,
        *,
        loopback: bool = False,
        private: bool = False,
        link_local: bool = False,
        multicast: bool = False,
        reserved: bool = False,
        version: int = 4,
        is_global: bool = True,
    ) -> None:
        self.is_loopback = loopback
        self.is_private = private
        self.is_link_local = link_local
        self.is_multicast = multicast
        self.is_reserved = reserved
        self.version = version
        self.is_global = is_global


def _force_addr(monkeypatch, fake: _FakeAddr) -> None:
    """Resolve to a parseable public IP but classify it with ``fake`` flags."""

    def _addrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo)
    monkeypatch.setattr(egress_mod.ipaddress, "ip_address", lambda ip: fake)


def test_link_local_denied(monkeypatch) -> None:
    _force_addr(monkeypatch, _FakeAddr(link_local=True))
    blocked, reason = target_forbidden("linklocal.example", 80)
    assert blocked is True
    assert reason == "link-local"


def test_multicast_denied(monkeypatch) -> None:
    _force_addr(monkeypatch, _FakeAddr(multicast=True))
    blocked, reason = target_forbidden("mcast.example", 80)
    assert blocked is True
    assert reason == "multicast"


def test_reserved_denied(monkeypatch) -> None:
    _force_addr(monkeypatch, _FakeAddr(reserved=True))
    blocked, reason = target_forbidden("reserved.example", 80)
    assert blocked is True
    assert reason == "reserved"


def test_non_global_ipv6_denied(monkeypatch) -> None:
    _force_addr(monkeypatch, _FakeAddr(version=6, is_global=False))
    blocked, reason = target_forbidden("v6.example", 80)
    assert blocked is True
    assert reason == "non-global IPv6"


def test_real_link_local_reserved_ula_addresses_still_denied() -> None:
    """Interpreter-semantics guard: whatever branch Python's ``ipaddress``
    picks for these real addresses, the proxy must deny them all."""
    for host in ("169.254.1.1", "240.0.0.1", "100::1", "2001:db8::1", "fdff::1"):
        blocked, reason = target_forbidden(host, 80)
        assert blocked is True, f"{host} must be denied (got reason={reason!r})"
        assert reason, f"{host} denial must carry a reason"


def test_allowlist_skips_malformed_entries() -> None:
    allow = Allowlist("bad-host:not-a-port, , 127.0.0.1:9000")
    assert allow.allows("127.0.0.1", 9000) is True
    assert allow.allows("bad-host", 0) is False
    assert Allowlist(None).allows("127.0.0.1", 9000) is False


# ---------------------------------------------------------------------------
# egress proxy -- live CONNECT / HTTP behaviour
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _start_proxy(allowlist: str) -> tuple[EgressProxy, int]:
    port = _free_port()
    proxy = EgressProxy(host="127.0.0.1", port=port, allowlist=Allowlist(allowlist))
    await proxy.start()
    assert proxy.port == port
    return proxy, port


async def test_connect_to_forbidden_target_denied() -> None:
    proxy, port = await _start_proxy("")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT 10.0.0.1:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert b"403" in line
        body = await asyncio.wait_for(reader.read(), timeout=5)
        assert b"forbidden by egress policy" in body
        writer.close()
    finally:
        await proxy.stop()


async def test_connect_bad_port_denied() -> None:
    proxy, port = await _start_proxy("")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT example.com:not-a-port HTTP/1.1\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert b"403" in line
        body = await asyncio.wait_for(reader.read(), timeout=5)
        assert b"bad target" in body
        writer.close()
    finally:
        await proxy.stop()


async def test_connect_tunnel_forwards_allowlisted_target() -> None:
    async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read(1024)
        writer.write(b"echo:" + data)
        await writer.drain()
        writer.close()

    echo = await asyncio.start_server(_echo, "127.0.0.1", 0)
    echo_port = echo.sockets[0].getsockname()[1]
    proxy, port = await _start_proxy(f"127.0.0.1:{echo_port}")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        # Standard CONNECT: request line, headers, terminating blank line --
        # what every real client (curl, httpx, browsers) sends. The proxy
        # consumes those headers before tunneling; if it did not, they would
        # reach the echo server as tunnel payload and the assertion below
        # would see "echo:Host: ..." instead of "echo:ping".
        writer.write(
            f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{echo_port}\r\n"
            f"\r\n".encode()
        )
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert b"200" in line and b"Established" in line
        await reader.readline()  # blank separator line of the 200 response
        writer.write(b"ping")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(1024), timeout=5)
        assert data == b"echo:ping"
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        await proxy.stop()
        echo.close()
        await echo.wait_closed()


async def test_connect_unreachable_upstream_denied() -> None:
    dead_port = _free_port()
    proxy, port = await _start_proxy(f"127.0.0.1:{dead_port}")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT 127.0.0.1:{dead_port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=20)
        assert b"403" in line
        body = await asyncio.wait_for(reader.read(), timeout=5)
        assert b"upstream unreachable" in body
        writer.close()
    finally:
        await proxy.stop()


async def test_http_method_unreachable_upstream_denied() -> None:
    dead_port = _free_port()
    proxy, port = await _start_proxy(f"127.0.0.1:{dead_port}")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET http://127.0.0.1:{dead_port}/x HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=20)
        assert b"403" in line
        body = await asyncio.wait_for(reader.read(), timeout=5)
        assert b"upstream unreachable" in body
        writer.close()
    finally:
        await proxy.stop()


async def test_empty_and_blank_request_lines_are_handled() -> None:
    proxy, port = await _start_proxy("")
    try:
        # empty first line -> connection dropped without error
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\r\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        writer.close()
        # blank (whitespace-only) request line -> no parts -> dropped
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        writer2.write(b"   \r\n")
        await writer2.drain()
        assert await asyncio.wait_for(reader2.read(), timeout=5) == b""
        writer2.close()
    finally:
        await proxy.stop()


async def test_handler_exception_never_crashes_the_proxy() -> None:
    proxy, port = await _start_proxy("")

    class _ExplodingReader:
        async def readline(self):
            raise ConnectionError("client exploded")

    class _SilentWriter:
        def close(self):
            pass

    try:
        # must not raise even though the reader blows up
        await proxy._handle(_ExplodingReader(), _SilentWriter())
        # proxy still serves afterwards
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT 10.0.0.1:80 HTTP/1.1\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert b"403" in line
        writer.close()
    finally:
        await proxy.stop()


async def test_deny_swallows_broken_writer() -> None:
    proxy, _ = await _start_proxy("")

    class _BrokenWriter:
        def write(self, data):
            raise ConnectionError("gone")

        async def drain(self):
            pass

    try:
        await proxy._deny(_BrokenWriter(), "test reason")  # must not raise
    finally:
        await proxy.stop()


# ---------------------------------------------------------------------------
# egress proxy -- IPv4-first upstream resolution
# ---------------------------------------------------------------------------


async def test_v4_first_resolution_failure_raises_oserror(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(OSError, match="resolution failed"):
        await egress_mod._open_connection_v4_first("nothing.example", 80)


async def test_v4_first_all_addresses_failed_raises_oserror(monkeypatch) -> None:
    dead_port = _free_port()

    def _dead(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", dead_port))]

    monkeypatch.setattr(socket, "getaddrinfo", _dead)
    with pytest.raises(OSError, match="all upstream addresses failed"):
        await egress_mod._open_connection_v4_first("127.0.0.1", dead_port)


async def test_v4_first_prefers_ipv4_ordering(monkeypatch) -> None:
    """IPv6 entries sort after IPv4 so kind/CI nodes (no IPv6 route) do not
    blackhole the tunnel on an AAAA pick."""
    echo = await asyncio.start_server(
        lambda r, w: w.close(), "127.0.0.1", 0
    )
    good_port = echo.sockets[0].getsockname()[1]
    real_getaddrinfo = socket.getaddrinfo

    def _mixed(host, port, **kwargs):
        # IPv6 first (the bad ordering), IPv4 second (the routable one)
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("100::1", port, 0, 0)),
            *real_getaddrinfo(host, port, **kwargs),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _mixed)
    try:
        reader, writer = await egress_mod._open_connection_v4_first("127.0.0.1", good_port)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    finally:
        echo.close()
        await echo.wait_closed()


async def test_run_egress_proxy_starts_and_stops() -> None:
    port = _free_port()
    task = asyncio.create_task(egress_mod.run_egress_proxy(port=port))
    try:
        for _ in range(50):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise AssertionError("run_egress_proxy never listened")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# OCI provider -- DockerCLIDriver subprocess surface
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        communicate_delay: float = 0.0,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._delay = communicate_delay
        self.killed = False
        self.stdin_received: bytes | None = None

    async def communicate(self, input_bytes: bytes | None = None):
        self.stdin_received = input_bytes
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _patch_subprocess(monkeypatch, proc: _FakeProc) -> list[tuple]:
    calls: list[tuple] = []

    async def _fake_exec(binary, *args, **kwargs):
        calls.append((binary, args, kwargs))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return calls


async def test_driver_run_success_and_stdin(monkeypatch) -> None:
    proc = _FakeProc(stdout=b"hello", stderr=b"warn", returncode=0)
    calls = _patch_subprocess(monkeypatch, proc)
    driver = DockerCLIDriver(binary="docker", timeout=30.0)
    result = await driver._run(["info"], input_bytes=b"ping")
    assert result.returncode == 0
    assert result.stdout == b"hello"
    assert proc.stdin_received == b"ping"
    assert calls[0][0] == "docker"


async def test_driver_run_timeout_raises_container_runtime_error(monkeypatch) -> None:
    proc = _FakeProc(communicate_delay=5.0)
    _patch_subprocess(monkeypatch, proc)
    driver = DockerCLIDriver(timeout=0.05)
    with pytest.raises(ContainerRuntimeError, match="timed out"):
        await driver._run(["run", "-i", "img"])


async def test_driver_health_true_false_and_exception(monkeypatch) -> None:
    driver = DockerCLIDriver()

    async def _ok(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 0, b"27.1.0\n", b"")

    monkeypatch.setattr(driver, "_run", _ok)
    assert await driver.health() is True

    async def _rc1(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 1, b"", b"daemon down")

    monkeypatch.setattr(driver, "_run", _rc1)
    assert await driver.health() is False

    async def _boom(args, input_bytes=None):
        raise ContainerRuntimeError("timeout")

    monkeypatch.setattr(driver, "_run", _boom)
    assert await driver.health() is False


async def test_driver_run_interactive_builds_hardened_args(monkeypatch) -> None:
    proc = _FakeProc(stdout=b"{}", returncode=0)
    calls = _patch_subprocess(monkeypatch, proc)
    driver = DockerCLIDriver()
    spec = ContainerSpec(
        name="cap-sbx-test",
        image="cap-sandbox-http:latest",
        command=["python", "-m", "sandbox.shim"],
        env={"PYTHONUNBUFFERED": "1"},
        labels={"cap.sandbox.execution_id": "x"},
        network="cap-egress",
        memory_mb=128,
        cpu_millicores=250,
        pids_limit=64,
        read_only_rootfs=True,
        tmpfs=("/tmp",),
        user="10001",
    )
    code, stdout, stderr = await driver.run_interactive(spec, b"payload", timeout=30)
    assert (code, stdout) == (0, b"{}")
    args = list(calls[0][1])
    joined = " ".join(args)
    # hardening surface must be present on every container start
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--read-only" in joined
    assert "--memory 128m" in joined
    assert "--pids-limit 64" in joined
    assert "--network cap-egress" in joined
    assert "--user 10001" in joined
    assert "--stop-signal SIGTERM" in joined
    assert "--tmpfs /tmp" in joined
    assert "cap.sandbox.execution_id=x" in joined
    # stdin carried the typed payload
    assert proc.stdin_received == b"payload"


async def test_driver_run_interactive_timeout_force_terminates(monkeypatch) -> None:
    proc = _FakeProc(communicate_delay=5.0)
    _patch_subprocess(monkeypatch, proc)
    driver = DockerCLIDriver(timeout=30.0)
    killed: list[str] = []
    removed: list[str] = []

    async def _kill(cid):
        killed.append(cid)

    async def _rm(cid, force=True):
        removed.append(cid)

    monkeypatch.setattr(driver, "kill", _kill)
    monkeypatch.setattr(driver, "rm", _rm)
    spec = ContainerSpec(
        name="cap-sbx-timeout",
        image="img",
        command=["python"],
        env={},
        labels={},
    )
    code, stdout, stderr = await driver.run_interactive(spec, b"", timeout=0.05)
    assert code == 124
    assert b"force-terminated" in stderr
    assert killed == ["cap-sbx-timeout"]
    assert removed == ["cap-sbx-timeout"]


async def test_driver_kill_rm_warn_but_never_raise(monkeypatch) -> None:
    driver = DockerCLIDriver()

    async def _rc1(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 1, b"", b"No such container")

    monkeypatch.setattr(driver, "_run", _rc1)
    await driver.kill("deadbeef")  # must not raise
    await driver.rm("deadbeef")  # must not raise

    async def _timeout(args, input_bytes=None):
        raise ContainerRuntimeError("timed out")

    monkeypatch.setattr(driver, "_run", _timeout)
    await driver.kill("deadbeef")
    await driver.rm("deadbeef")


async def test_driver_inspect_error_and_invalid_json(monkeypatch) -> None:
    driver = DockerCLIDriver()

    async def _rc1(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 1, b"", b"no such container")

    monkeypatch.setattr(driver, "_run", _rc1)
    with pytest.raises(ContainerRuntimeError, match="inspect failed"):
        await driver.inspect("deadbeef")

    async def _bad_json(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 0, b"not-json{", b"")

    monkeypatch.setattr(driver, "_run", _bad_json)
    with pytest.raises(ContainerRuntimeError, match="invalid json"):
        await driver.inspect("deadbeef")

    async def _ok(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 0, json.dumps([{"Id": "abc"}]).encode(), b"")

    monkeypatch.setattr(driver, "_run", _ok)
    assert (await driver.inspect("abc"))["Id"] == "abc"


async def test_driver_list_by_labels_skips_vanished_containers(monkeypatch) -> None:
    driver = DockerCLIDriver()
    seen: list[list[str]] = []

    async def _run(args, input_bytes=None):
        seen.append(list(args))
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, b"aaa\nbbb\n", b"")
        if args[0] == "inspect" and args[1] == "aaa":
            return subprocess.CompletedProcess(args, 0, json.dumps([{"Id": "aaa"}]).encode(), b"")
        return subprocess.CompletedProcess(args, 1, b"", b"gone")  # bbb vanished

    monkeypatch.setattr(driver, "_run", _run)
    result = await driver.list_by_labels(
        {"cap.sandbox.worker_id": "w1", "cap.sandbox.lease_id": ""}
    )
    assert [item["Id"] for item in result] == ["aaa"]
    ps_args = seen[0]
    assert "label=cap.sandbox.worker_id=w1" in ps_args
    assert "label=cap.sandbox.lease_id" in ps_args  # empty value = key-exists filter


async def test_driver_exists_image_and_stats(monkeypatch) -> None:
    driver = DockerCLIDriver()

    async def _ok(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 0, b"100MiB|1%|3\n", b"")

    monkeypatch.setattr(driver, "_run", _ok)
    assert await driver.exists_image("img") is True
    stats = await driver.container_stats("cid")
    assert stats["line"].startswith("100MiB")

    async def _rc1(args, input_bytes=None):
        return subprocess.CompletedProcess(args, 1, b"", b"no image")

    monkeypatch.setattr(driver, "_run", _rc1)
    assert await driver.exists_image("img") is False
    assert await driver.container_stats("cid") == {}

    async def _timeout(args, input_bytes=None):
        raise ContainerRuntimeError("t")

    monkeypatch.setattr(driver, "_run", _timeout)
    assert await driver.container_stats("cid") == {}


# ---------------------------------------------------------------------------
# OCI provider -- lifecycle branches via scripted driver
# ---------------------------------------------------------------------------


class _ScriptedDriver:
    driver_name = "scripted"

    def __init__(self, exit_code: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.specs: list[ContainerSpec] = []
        self.removed: list[str] = []
        self.killed: list[str] = []
        self.healthy = True
        self.image_exists = True

    async def health(self) -> bool:
        return self.healthy

    async def run_interactive(self, spec, input_bytes, timeout):
        self.specs.append(spec)
        return self.exit_code, self.stdout, self.stderr

    async def kill(self, container_id):
        self.killed.append(container_id)

    async def rm(self, container_id, force=True):
        self.removed.append(container_id)

    async def inspect(self, container_id):
        return {}

    async def list_by_labels(self, labels):
        return []

    async def exists_image(self, image) -> bool:
        return self.image_exists

    async def container_stats(self, container_id):
        return {}


class _Metrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def inc(self, name, labels=None):
        self.calls.append((name, labels))


def _profile() -> SandboxProfile:
    return SandboxProfile(name="p288", timeout_seconds=15, memory_mb=128, cpu_millicores=250)


def _request(url: str = "http://example.invalid/") -> SandboxRequest:
    return SandboxRequest(
        version=PROTOCOL_VERSION,
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url=url,
    )


async def test_oci_timeout_exit_124_is_typed_sandbox_timeout() -> None:
    metrics = _Metrics()
    driver = _ScriptedDriver(124, b"", b"sandbox timed out and was force-terminated")
    provider = OCISandboxProvider(driver=driver, metrics=metrics)
    response = await provider.execute_request(_profile(), _request())
    assert response.status == "error"
    assert response.error_type == "SandboxTimeout"
    assert ("sandbox_forced_termination_total", None) in metrics.calls
    assert driver.removed, "container must be removed on the timeout path"


async def test_oci_nonzero_exit_is_typed_sandbox_exit_with_stderr_tail() -> None:
    driver = _ScriptedDriver(2, b"partial stdout", b"boom: permission denied")
    provider = OCISandboxProvider(driver=driver)
    response = await provider.execute_request(_profile(), _request())
    assert response.status == "error"
    assert response.error_type == "SandboxExit"
    assert "boom: permission denied" in (response.error or "")
    assert driver.removed


async def test_oci_invalid_shim_json_is_typed_protocol_error() -> None:
    driver = _ScriptedDriver(0, b"this is not json")
    provider = OCISandboxProvider(driver=driver)
    response = await provider.execute_request(_profile(), _request())
    assert response.status == "error"
    assert response.error_type == "SandboxProtocol"
    assert "invalid shim response" in (response.error or "")


async def test_oci_success_increments_execution_metric_and_removes() -> None:
    metrics = _Metrics()
    ok_body = json.dumps({"version": 1, "status": "ok", "result": {"status": 200}})
    driver = _ScriptedDriver(0, ok_body.encode())
    provider = OCISandboxProvider(driver=driver, metrics=metrics)
    response = await provider.execute_request(_profile(), _request())
    assert response.status == "ok"
    assert ("sandbox_execution_total", {"provider": "oci-sandbox"}) in metrics.calls
    assert driver.removed


async def test_oci_egress_proxy_env_is_injected_and_empty_no_proxy() -> None:
    driver = _ScriptedDriver(124, b"")
    provider = OCISandboxProvider(driver=driver, egress_proxy_url="http://egress:8080")
    await provider.execute_request(_profile(), _request())
    env = driver.specs[0].env
    assert env["HTTPS_PROXY"] == "http://egress:8080"
    assert env["HTTP_PROXY"] == "http://egress:8080"
    assert env["NO_PROXY"] == ""  # never bypass the proxy

    driver2 = _ScriptedDriver(124, b"")
    provider2 = OCISandboxProvider(driver=driver2, egress_proxy_url=None)
    provider2._egress_proxy = None
    await provider2.execute_request(_profile(), _request())
    assert "HTTPS_PROXY" not in driver2.specs[0].env


async def test_oci_terminate_kills_removes_and_forgets() -> None:
    driver = _ScriptedDriver(0, b"{}")
    provider = OCISandboxProvider(driver=driver)
    execution_id = uuid4()
    provider._active[str(execution_id)] = OCISandboxProvider.container_name(execution_id)
    assert await provider.terminate(execution_id) is True
    name = OCISandboxProvider.container_name(execution_id)
    assert driver.killed == [name]
    assert driver.removed == [name]
    assert str(execution_id) not in provider._active


async def test_oci_health_requires_daemon_and_image() -> None:
    driver = _ScriptedDriver(0, b"{}")
    provider = OCISandboxProvider(driver=driver, image="cap-sandbox-http:latest")
    assert await provider.health() is True
    driver.image_exists = False
    assert await provider.health() is False
    driver.image_exists = True
    driver.healthy = False
    assert await provider.health() is False


async def test_oci_rejects_arbitrary_callables() -> None:
    provider = OCISandboxProvider(driver=_ScriptedDriver(0, b"{}"))

    async def op():
        return {}

    with pytest.raises(SandboxExecutionError, match="does not accept arbitrary callables"):
        await provider.execute(uuid4(), _profile(), op)


# ---------------------------------------------------------------------------
# Kubernetes provider -- failure and cleanup branches
# ---------------------------------------------------------------------------


class _Pod:
    """V1Pod-like object: _list_pods must call .to_dict() on each item."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


class _FakeK8s:
    def __init__(self) -> None:
        self.pods: dict[str, dict] = {}
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.create_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.read_override: dict | None = None

    def create_namespaced_pod(self, namespace, body):
        if self.create_error is not None:
            raise self.create_error
        self.created.append(body)
        name = body["metadata"]["name"]
        self.pods[name] = {
            "metadata": {"name": name, "labels": body["metadata"]["labels"]},
            "status": {"phase": "Running", "pod_ip": "127.0.0.1"},
        }

    def read_namespaced_pod(self, name, namespace):
        if self.read_override is not None:
            return self.read_override
        return self.pods.get(name) or {
            "metadata": {"name": name, "labels": {}},
            "status": {"phase": "Pending", "pod_ip": None},
        }

    def delete_namespaced_pod(self, name, namespace, grace_period_seconds):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)
        self.pods.pop(name, None)

    def list_namespaced_pod(self, namespace, label_selector):
        class _List:
            def __init__(self, items):
                self.items = items

        return _List([_Pod(p) for p in self.pods.values()])


def _k8s_request(url: str = "http://example.invalid/") -> SandboxRequest:
    return SandboxRequest(
        version=PROTOCOL_VERSION,
        operation="http_fetch",
        run_id=str(uuid4()),
        sandbox_execution_id=str(uuid4()),
        url=url,
    )


class _ShimHandler(BaseHTTPRequestHandler):
    """200 on /healthz, configurable status elsewhere."""

    post_status = 500

    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = b"shim exploded"
        self.send_response(self.post_status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def shim_500():
    srv = HTTPServer(("127.0.0.1", 0), _ShimHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


async def test_k8s_pod_create_failure_returns_typed_error_and_cleans_up() -> None:
    fake = _FakeK8s()
    fake.create_error = RuntimeError("API server down")
    provider = KubernetesSandboxProvider()
    provider._client = fake
    response = await provider.execute_request(_profile(), _k8s_request())
    assert response.status == "error"
    assert response.error_type == "RuntimeError"
    assert "API server down" in (response.error or "")
    # cleanup attempted even though the pod never existed (best-effort delete)
    assert provider._active == set()


async def test_k8s_readiness_timeout_raises_with_stall_diagnostics() -> None:
    fake = _FakeK8s()
    fake.read_override = {
        "metadata": {"name": "x", "labels": {}},
        "status": {
            "phase": "Pending",
            "pod_ip": None,
            "container_statuses": [
                {
                    "name": "sandbox",
                    "ready": False,
                    "restart_count": 0,
                    "state": {
                        "waiting": {
                            "reason": "ImagePullBackOff",
                            "message": "back-off pulling image",
                        }
                    },
                }
            ],
        },
    }
    provider = KubernetesSandboxProvider(pod_ready_timeout=0.6)
    provider._client = fake
    with pytest.raises(KubernetesSandboxError, match="not ready"):
        await provider.execute_request(_profile(), _k8s_request())
    # the pod must still be deleted after a readiness failure
    assert fake.deleted, "pod not cleaned up after readiness timeout"


async def test_k8s_pod_without_ip_raises_and_cleans_up(monkeypatch) -> None:
    fake = _FakeK8s()
    provider = KubernetesSandboxProvider()
    provider._client = fake

    async def _ready(name, timeout):
        return {}

    monkeypatch.setattr(provider, "_wait_ready", _ready)
    fake.read_override = {
        "metadata": {"name": "x", "labels": {}},
        "status": {"phase": "Running", "pod_ip": None},
    }
    with pytest.raises(KubernetesSandboxError, match="has no IP"):
        await provider.execute_request(_profile(), _k8s_request())
    assert fake.deleted


async def test_k8s_shim_non_200_is_typed_sandbox_serve_error(shim_500) -> None:
    fake = _FakeK8s()
    provider = KubernetesSandboxProvider(shim_port=shim_500, egress_proxy="")
    provider._client = fake
    response = await provider.execute_request(_profile(), _k8s_request())
    assert response.status == "error"
    assert response.error_type == "SandboxServe"
    assert "500" in (response.error or "")
    assert fake.deleted, "pod must be cleaned up after a shim failure"


async def test_k8s_delete_failure_in_finally_is_best_effort(shim_500) -> None:
    fake = _FakeK8s()
    fake.delete_error = RuntimeError("cannot delete")
    provider = KubernetesSandboxProvider(shim_port=shim_500, egress_proxy="")
    provider._client = fake
    # must NOT raise even though cleanup fails; the result still comes back
    response = await provider.execute_request(_profile(), _k8s_request())
    assert response.status == "error"  # shim 500
    assert provider._active == set()


async def test_k8s_pod_spec_carries_egress_proxy_env() -> None:
    provider = KubernetesSandboxProvider(egress_proxy="http://cap-egress-proxy:8080")
    spec = provider._pod_spec(
        uuid4(),
        _profile(),
        run_id="run-1",
        worker_id="w-1",
        lease_id=None,
        attempt=0,
    )
    env = {entry["name"]: entry["value"] for entry in spec["spec"]["containers"][0]["env"]}
    assert env["HTTPS_PROXY"] == "http://cap-egress-proxy:8080"
    assert env["HTTP_PROXY"] == "http://cap-egress-proxy:8080"
    assert env["NO_PROXY"] == ""

    provider_none = KubernetesSandboxProvider(egress_proxy="")
    spec_none = provider_none._pod_spec(
        uuid4(), _profile(), run_id="r", worker_id="w", lease_id=None, attempt=0
    )
    env_none = {e["name"] for e in spec_none["spec"]["containers"][0]["env"]}
    assert "HTTPS_PROXY" not in env_none


async def test_k8s_execute_rejects_arbitrary_callables() -> None:
    provider = KubernetesSandboxProvider()

    async def op():
        return {}

    with pytest.raises(SandboxExecutionError, match="does not accept arbitrary callables"):
        await provider.execute(uuid4(), _profile(), op)


async def test_k8s_terminate_failure_return_false() -> None:
    fake = _FakeK8s()
    fake.delete_error = RuntimeError("api gone")
    provider = KubernetesSandboxProvider()
    provider._client = fake
    execution_id = uuid4()
    provider._active.add(str(execution_id))
    assert await provider.terminate(execution_id) is False

    fake.delete_error = None
    assert await provider.terminate(execution_id) is True
    assert str(execution_id) not in provider._active


async def test_k8s_health_probe_semantics(monkeypatch) -> None:
    from kubernetes.client import ApiException

    provider = KubernetesSandboxProvider()

    async def _found(name):
        return {"metadata": {"name": name}}

    monkeypatch.setattr(provider, "_get_pod", _found)
    assert await provider.health() is True

    async def _404(name):
        raise ApiException(status=404, reason="Not Found")

    monkeypatch.setattr(provider, "_get_pod", _404)
    assert await provider.health() is True  # 404 proves API + RBAC work

    async def _500(name):
        raise ApiException(status=500, reason="Server Error")

    monkeypatch.setattr(provider, "_get_pod", _500)
    assert await provider.health() is False

    async def _other(name):
        raise RuntimeError("network partition")

    monkeypatch.setattr(provider, "_get_pod", _other)
    assert await provider.health() is False


async def test_k8s_reconcile_orphans_deletes_only_unowned_pods() -> None:
    fake = _FakeK8s()
    owned = str(uuid4())
    orphan = str(uuid4())
    fake.pods = {
        "pod-owned": {
            "metadata": {
                "name": "pod-owned",
                "labels": {LABEL_MANAGED: "true", LABEL_EXECUTION: owned},
            },
            "status": {},
        },
        "pod-orphan": {
            "metadata": {
                "name": "pod-orphan",
                "labels": {LABEL_MANAGED: "true", LABEL_EXECUTION: orphan},
            },
            "status": {},
        },
        "pod-no-identity": {
            "metadata": {"name": "pod-no-identity", "labels": {LABEL_MANAGED: "true"}},
            "status": {},
        },
    }
    provider = KubernetesSandboxProvider()
    provider._client = fake
    deleted = await provider.reconcile_orphans(owned_executions={owned})
    assert deleted == 1
    assert fake.deleted == ["pod-orphan"]


async def test_k8s_reconcile_tolerates_delete_failure() -> None:
    fake = _FakeK8s()
    orphan = str(uuid4())
    fake.pods = {
        "pod-a": {
            "metadata": {
                "name": "pod-a",
                "labels": {LABEL_MANAGED: "true", LABEL_EXECUTION: orphan},
            },
            "status": {},
        },
        "pod-b": {
            "metadata": {
                "name": "pod-b",
                "labels": {LABEL_MANAGED: "true", LABEL_EXECUTION: str(uuid4())},
            },
            "status": {},
        },
    }
    provider = KubernetesSandboxProvider()
    provider._client = fake
    real_delete = fake.delete_namespaced_pod
    state = {"fail_first": True}

    def _flaky_delete(name, namespace, grace_period_seconds):
        if state["fail_first"]:
            state["fail_first"] = False
            raise RuntimeError("transient")
        return real_delete(name, namespace, grace_period_seconds)

    fake.delete_namespaced_pod = _flaky_delete
    deleted = await provider.reconcile_orphans(owned_executions=set())
    assert deleted == 1  # one failed, one succeeded -- still reconciles


async def test_k8s_client_lazy_load_cached(monkeypatch) -> None:
    class _StubClientModule:
        def __init__(self):
            self.api_calls = 0

        def CoreV1Api(self):
            self.api_calls += 1
            return object()

    stub = _StubClientModule()
    monkeypatch.setattr(k8s_mod, "_load_k8s_client", lambda: stub)
    provider = KubernetesSandboxProvider()
    first = provider._k8s()
    second = provider._k8s()
    assert first is second
    assert stub.api_calls == 1


def test_k8s_load_client_falls_back_to_kubeconfig(monkeypatch) -> None:
    from kubernetes import config as k8s_config

    attempts: list[str] = []

    def _no_incluster():
        attempts.append("incluster")
        raise RuntimeError("not in cluster")

    def _kubeconfig():
        attempts.append("kubeconfig")

    monkeypatch.setattr(k8s_config, "load_incluster_config", _no_incluster)
    monkeypatch.setattr(k8s_config, "load_kube_config", _kubeconfig)
    client = k8s_mod._load_k8s_client()
    assert attempts == ["incluster", "kubeconfig"]
    assert client is not None


def test_k8s_load_client_propagates_when_no_config(monkeypatch) -> None:
    from kubernetes import config as k8s_config

    def _no_incluster():
        raise RuntimeError("not in cluster")

    def _no_kubeconfig():
        raise RuntimeError("no kubeconfig")

    monkeypatch.setattr(k8s_config, "load_incluster_config", _no_incluster)
    monkeypatch.setattr(k8s_config, "load_kube_config", _no_kubeconfig)
    with pytest.raises(RuntimeError, match="no kubeconfig"):
        k8s_mod._load_k8s_client()


async def test_k8s_secrets_refused_before_pod_creation() -> None:
    fake = _FakeK8s()
    provider = KubernetesSandboxProvider()
    provider._client = fake
    with pytest.raises(KubernetesSandboxError, match="secret=False"):
        await provider.execute_request(_profile(), _k8s_request(), secrets={"K": "V"})
    assert fake.created == []
