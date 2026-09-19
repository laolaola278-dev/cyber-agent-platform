#!/usr/bin/env python3
"""Phase 28.5-CI -- machine-readable certification result + human report.

Consumes:
  - pytest JUnit XMLs (junit-security/ha/benchmark/regression.xml)
  - sandbox-images.json
  - environment artifacts (uname/os-release/resources/cgroup/iptables...)

Emits:
  - outputs/cap-28.5-linux-certification.json   (machine-readable gate result)
  - outputs/CAP Phase 28.5-L Linux Runtime Certification Report.md

Meta-gate completeness: if any REQUIRED gate is missing / NOT_RUN / SKIPPED,
exit non-zero so the release certification job FAILS (no silent green).
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path(os.environ.get("CAP_CERT_OUT", "outputs/cap-cert"))
ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"

# every gate that MUST appear in the result with a real outcome
REQUIRED_GATES = [
    "container_isolation",
    "filesystem",
    "memory",
    "cpu",
    "pids",
    "network_enforcement",
    "ssrf_defense_in_depth",
    "hard_cancellation",
    "reaper",
    "browser",
    "secrets",
    "real_integration",
]

# map gate -> (test file, test id substring) that proves it
GATE_TESTS = {
    "container_isolation": ("test_phase_28_5_container_integration", "test_containerized_fetch"),
    "filesystem": ("test_phase_28_5_linux_resources", "test_filesystem_isolation_real"),
    "memory": ("test_phase_28_5_linux_resources", "test_memory_limit_real_and_oom"),
    "cpu": ("test_phase_28_5_linux_resources", "test_cpu_quota_real"),
    "pids": ("test_phase_28_5_linux_resources", "test_pids_limit_real"),
    "network_enforcement": ("test_phase_28_5_linux_network", "test_sandbox_direct_public_egress_is_blocked"),
    "ssrf_defense_in_depth": ("test_phase_28_5_linux_network", "test_sandbox_direct_private_and_metadata_blocked"),
    "hard_cancellation": ("test_phase_28_5_linux_reaper", "test_cancellation_ordering_timestamps"),
    "reaper": ("test_phase_28_5_linux_reaper", "test_reaper_fencing_on_real_containers"),
    "browser": (
        "test_phase_28_5_container_integration",
        "test_browser_renders_page_in_isolated_container",
    ),
    "secrets": ("test_phase_28_5_linux_secrets", "test_secret_never_appears_in_control_plane_artifacts"),
    "real_integration": ("test_phase_28_4_multi_worker_ha", "test_two_workers"),
}


def parse_junit(junit: Path) -> dict[str, str]:
    """test_id -> outcome (passed/failed/skipped)."""
    result: dict[str, str] = {}
    if not junit.exists():
        return result
    tree = ET.parse(junit)
    for case in tree.iter("testcase"):
        name = case.get("name", "")
        cls = case.get("classname", "")
        # keep the full class path (module[.Class]) so class-based tests
        # (e.g. TestMultiWorkerHA.test_two_workers...) still match the module
        # substring used by resolve_gate.
        test_id = f"{cls}.{name}" if cls else name
        if case.find("failure") is not None or case.find("error") is not None:
            result[test_id] = "failed"
        elif case.find("skipped") is not None:
            result[test_id] = "skipped"
        else:
            result[test_id] = "passed"
    return result


def resolve_gate(gate: str, results: dict[str, str]) -> str:
    file, sub = GATE_TESTS[gate]
    # any test id matching the file + substring decides the gate
    matched = [v for k, v in results.items() if file in k and sub in k]
    if not matched:
        return "NOT_RUN"
    if any(v == "failed" for v in matched):
        return "FAIL"
    if any(v == "skipped" for v in matched):
        return "SKIPPED"
    if all(v == "passed" for v in matched):
        return "PASS"
    return "NOT_RUN"


def secret_canary_scan() -> list[str]:
    """Scan generated logs/artifacts for the sentinel value."""
    sentinel = os.environ.get("CAP_CERT_SECRET_SENTINEL", "")
    if not sentinel:
        return []
    leaks: list[str] = []
    pattern = re.compile(re.escape(sentinel))
    for path in OUT_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in (".xml", ".json", ".txt", ".log", ".md"):
            try:
                if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                    leaks.append(str(path))
            except OSError:
                continue
    return leaks


#: A container-runtime control socket in a worker's mounts means the worker can
#: create, inspect and delete containers on the host -- host root, in practice.
RUNTIME_SOCKETS = ("docker.sock", "podman.sock", "containerd.sock", "crio.sock")

#: The worker service name in docker-compose.yml and the chart template that must
#: not mount one.
COMPOSE_WORKER_SERVICE = "acquisition-worker"
CHART_WORKER_TEMPLATE = Path("deployment/helm/cap/templates/worker.yaml")


def _mounts_socket(mounts: object) -> bool:
    """True if any entry in a volume/volumeMount list names a runtime socket."""
    if not mounts:
        return False
    for entry in mounts if isinstance(mounts, list) else [mounts]:
        rendered = entry if isinstance(entry, str) else repr(entry)
        if any(socket in rendered for socket in RUNTIME_SOCKETS):
            return True
    return False


def compose_worker_volumes(text: str, service: str = COMPOSE_WORKER_SERVICE) -> list[str]:
    """The `volumes:` entries of one compose service, parsed with the stdlib only.

    A YAML parser was tried first and rejected: PyYAML is in the backend venv, but
    the certification job calls this script with a bare interpreter, so the gate
    would have depended on which python happened to run it (that is how the
    full-certification layer failed with ModuleNotFoundError: No module named
    'yaml'). Compose files of this shape are regular enough that indent-scanning
    answers the one question asked, and it keeps the gate dependency-free like
    scripts/quality/scan_secrets.py.
    """
    lines = text.splitlines()
    try:
        start = next(
            i
            for i, line in enumerate(lines)
            if line.rstrip() == f"  {service}:"
        )
    except StopIteration:
        return []
    volumes: list[str] = []
    in_volumes = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= 2:  # next service, or the end of this one
            break
        if stripped.startswith("volumes:"):
            in_volumes = True
            continue
        if in_volumes:
            if stripped.startswith("- "):
                volumes.append(stripped[2:])
            elif stripped.endswith(":"):
                in_volumes = False  # a sibling key ended the volumes list
    return volumes


def compose_worker_mounts_control_socket() -> bool:
    """True when the compose worker mounts a container-runtime socket.

    The oci-sandbox provider starts sandbox containers through the docker CLI, so
    the compose worker legitimately holds ``/var/run/docker.sock``. That is a
    property of the evaluation path, not something to hide in a report: it makes
    that worker host-root-equivalent. Comments are not mounts -- only the service's
    own volume entries are read.
    """
    path = ROOT / "docker-compose.yml"
    if not path.exists():
        return False
    return _mounts_socket(compose_worker_volumes(path.read_text(encoding="utf-8", errors="replace")))


def chart_worker_mounts_control_socket() -> bool:
    """The production chart's worker template must mount no runtime socket."""
    path = ROOT / CHART_WORKER_TEMPLATE
    if not path.exists():
        # Absent template: say so rather than claim isolation.
        return True
    # Comment lines are prose about the mount, not mounts -- the previous detector
    # matched them, which is how a docstring could change a security verdict.
    mount_lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
        and any(token in line for token in ("mountPath", "hostPath", "persistentVolumeClaim", "volumeMounts"))
    ]
    return _mounts_socket(mount_lines)


def docker_socket_control_plane() -> dict[str, object]:
    """Per-path control-socket facts, so the artifact cannot be read as a blanket claim."""
    compose = compose_worker_mounts_control_socket()
    chart = chart_worker_mounts_control_socket()
    # Never a blanket PASS while any shipped deployment path holds the socket:
    # PARTIAL says "the production chart is isolated, the compose path is not",
    # and the legacy boolean stays true if *either* path mounts one, so an older
    # consumer cannot read this as full isolation.
    mounted = chart or compose
    verdict = "PASS" if not mounted else ("NOT_CERTIFIED" if chart else "PARTIAL")
    return {
        "worker_control_plane_isolation": verdict,
        "unrestricted_docker_socket_mounted": mounted,
        "production_chart_worker_mounts_runtime_socket": chart,
        "compose_worker_mounts_runtime_socket": compose,
        "compose_control_socket_scope": (
            "docker-compose runs SANDBOX_PROVIDER=oci-sandbox, which drives the docker CLI, so "
            "the worker mounts /var/run/docker.sock and is host-root-equivalent; sandbox "
            "containers themselves do not see it. Compose is the evaluation/single-node path -- "
            "documented in docs/known-issues.md and in docker-compose.yml at the mount."
            if compose
            else "the compose worker no longer mounts a runtime socket; update the docs and this "
                 "message, because they currently state that it does"
        ),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # pytest runs with working-directory: backend, so its --junitxml paths
    # land under backend/outputs/cap-cert/; the preflight/collect scripts run
    # from the repo root, so their environment/topology artifacts land under
    # ./outputs/cap-cert/. Search both for junit files so the gates resolve.
    backend_out = BACKEND / OUT_DIR if not OUT_DIR.is_absolute() else OUT_DIR
    junits = sorted(set(OUT_DIR.glob("junit-*.xml")) | set(backend_out.glob("junit-*.xml")))
    results: dict[str, str] = {}
    for junit in junits:
        results.update(parse_junit(junit))

    gates: dict[str, str] = {}
    for gate in REQUIRED_GATES:
        gates[gate] = resolve_gate(gate, results)

    env: dict[str, object] = {}
    for key in ("uname.txt", "os-release.txt", "docker-version.txt", "resources.txt",
                "cgroup.txt", "ip-route.txt", "iptables-save.txt", "nft-ruleset.txt"):
        p = OUT_DIR / key
        if p.exists():
            env[key] = p.read_text(encoding="utf-8", errors="replace")[:2000]

    images: dict[str, object] = {}
    img_file = OUT_DIR / "sandbox-images.json"
    if img_file.exists():
        images = json.loads(img_file.read_text(encoding="utf-8")).get("images", {})

    canary_leaks = secret_canary_scan()
    if canary_leaks:
        gates["secrets"] = "FAIL"  # canary found in artifacts

    socket_ctx = docker_socket_control_plane()
    # Sandbox WORKLOAD isolation is the conjunction of the 12 certified gates.
    sandbox_workload = "PASS" if all(v == "PASS" for v in gates.values()) else "NOT_CERTIFIED"

    payload = {
        "phase": "28.5-L",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": env,
        "images": images,
        "gates": gates,
        "sandbox_workload_isolation": sandbox_workload,
        "worker_control_plane_isolation": socket_ctx["worker_control_plane_isolation"],
        "unrestricted_docker_socket_mounted": socket_ctx["unrestricted_docker_socket_mounted"],
        "production_chart_worker_mounts_runtime_socket": socket_ctx[
            "production_chart_worker_mounts_runtime_socket"
        ],
        "compose_worker_mounts_runtime_socket": socket_ctx["compose_worker_mounts_runtime_socket"],
        "compose_control_socket_scope": socket_ctx["compose_control_socket_scope"],
        "tests": {"total": len(results), "outcomes": {
            "passed": sum(1 for v in results.values() if v == "passed"),
            "failed": sum(1 for v in results.values() if v == "failed"),
            "skipped": sum(1 for v in results.values() if v == "skipped"),
        }},
        "secret_canary_leaks": canary_leaks,
    }
    (OUT_DIR / "cap-28.5-linux-certification.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    # human report
    lines = [
        "# CAP Phase 28.5-L — Linux Runtime Certification Report",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "## Environment",
        "```",
        env.get("uname.txt", "NOT EXECUTED"),
        env.get("os-release.txt", ""),
        "```",
        "",
        "## Gates",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for gate, value in gates.items():
        lines.append(f"| {gate} | {value} |")
    lines += [
        "",
        "## Tests",
        f"- total: {payload['tests']['total']}",
        f"- passed: {payload['tests']['outcomes']['passed']}",
        f"- failed: {payload['tests']['outcomes']['failed']}",
        f"- skipped: {payload['tests']['outcomes']['skipped']}",
        "",
        "## Control plane",
        f"- sandbox_workload_isolation: {payload['sandbox_workload_isolation']}",
        f"- worker_control_plane_isolation: {payload['worker_control_plane_isolation']}",
        "-   production chart worker mounts a runtime control socket: "
        f"{payload['production_chart_worker_mounts_runtime_socket']}"
        " | compose worker (evaluation path): "
        f"{payload['compose_worker_mounts_runtime_socket']}",
        f"- unrestricted_docker_socket_mounted: {payload['unrestricted_docker_socket_mounted']}",
        "",
    ]
    if canary_leaks:
        lines.append("## SECRET CANARY LEAK (FAIL)")
        for leak in canary_leaks:
            lines.append(f"- {leak}")
    (OUT_DIR / "CAP Phase 28.5-L Linux Runtime Certification Report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    # meta-gate completeness: release-blocking check
    problems = [g for g, v in gates.items() if v in ("NOT_RUN", "SKIPPED")]
    if canary_leaks:
        problems.append("secret_canary")
    if problems:
        print(f"META-CERTIFICATION FAIL: incomplete gates: {problems}")
        return 1
    failed = [g for g, v in gates.items() if v == "FAIL"]
    if failed:
        print(f"CERTIFICATION FAIL: gates failed: {failed}")
        return 1
    print(f"CERTIFICATION PASS: all {len(REQUIRED_GATES)} gates certified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
