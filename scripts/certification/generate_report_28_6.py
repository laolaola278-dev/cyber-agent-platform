"""Phase 28.6 -- generate the machine-readable K8s certification artifact.

Gates are derived from the JUnit XML emitted by the strict certification test
run (tests/test_phase_28_6_k8s_certification.py): a test that passed maps to
its gate(s) PASS; failed maps to FAIL; skipped is a FAILURE when
CAP_K8S_STRICT=1 (SKIP == FAIL), otherwise reported as-is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(os.environ.get("CAP_CERT_OUT", str(REPO_ROOT / "outputs/cap-cert")))

# test function name -> the gates it certifies
TEST_GATES: dict[str, list[str]] = {
    "test_gate2_required_pods_healthy": ["K8S-GATE 2"],
    "test_gate3_worker_has_no_runtime_socket": ["K8S-GATE 3"],
    "test_gate4_worker_role_is_namespaced_least_privilege": ["K8S-GATE 4"],
    "test_gate5_worker_sa_adversarial_attempts_denied": ["K8S-GATE 5"],
    "test_gate6_sandbox_has_no_serviceaccount_token": ["K8S-GATE 6"],
    "test_gate7_sandbox_networkpolicy_enforced": ["K8S-GATE 7", "K8S-GATE 8", "K8S-GATE 9"],
    "test_gate10_controlled_egress_via_proxy_works": ["K8S-GATE 10"],
    "test_gate11_api_multi_replica_idempotency": ["K8S-GATE 11"],
    "test_gate12_worker_multi_replica_ownership": ["K8S-GATE 12"],
}

# gates certified by evidence OUTSIDE this run (historical / separately
# certified): K8S-GATE 1 is the Phase 28.5-RC2 baseline (14/14 gates, commit
# 705bdd2, v1.0.0-rc3), which this phase builds on and must not regress.
BASELINE_GATES = {
    "K8S-GATE 1": "PASS",  # 28.5-RC2 certified baseline (705bdd2, 14/14)
}

ALL_GATES = [f"K8S-GATE {i}" for i in range(1, 33)]


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return proc.stdout.strip() or "unknown"


def _cluster_info() -> dict[str, str]:
    def run(args: list[str]) -> str:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False
        )
        return proc.stdout.strip()

    kind = run(["kind", "version"])
    k8s = run(["kubectl", "version", "-o", "json"]).split("gitVersion")[1][:12] if "gitVersion" in run(["kubectl", "version", "-o", "json"]) else ""
    return {
        "kind": kind,
        "kubernetes": k8s,
        "nodes": run(["kubectl", "get", "nodes", "--no-headers", "-o", "custom-columns=:.metadata.name"]).replace("\n", ","),
    }


def _parse_junit() -> dict[str, str]:
    junit = OUT_DIR / "junit-k8s.xml"
    results: dict[str, str] = {}
    if not junit.exists():
        return results
    root = ET.parse(junit).getroot()
    for case in root.iter("testcase"):
        name = case.get("name", "")
        if case.find("failure") is not None or case.find("error") is not None:
            results[name] = "failed"
        elif case.find("skipped") is not None:
            results[name] = "skipped"
        else:
            results[name] = "passed"
    return results


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = _parse_junit()
    strict = os.environ.get("CAP_K8S_STRICT") == "1"

    gates: dict[str, str] = {}
    for gate in ALL_GATES:
        gates[gate] = "NOT_RUN"
    for gate, status in BASELINE_GATES.items():
        gates[gate] = status
    for test_name, test_gates in TEST_GATES.items():
        outcome = results.get(test_name, "NOT_RUN")
        for gate in test_gates:
            if outcome == "passed":
                gates[gate] = "PASS"
            elif outcome == "failed":
                gates[gate] = "FAIL"
            elif outcome == "skipped" and strict:
                gates[gate] = "FAIL"  # SKIP == FAIL
            elif outcome == "skipped":
                gates[gate] = "SKIPPED"
            else:
                gates[gate] = "NOT_RUN"

    passed = sum(1 for v in gates.values() if v == "PASS")
    payload = {
        "phase": "28.6",
        "commit": _commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kubernetes": _cluster_info(),
        "images": {
            "backend": "cap-backend:ci",
            "frontend": "cap-frontend:ci",
            "sandbox_http": "cap-sandbox-http:latest",
            "sandbox_browser": "cap-sandbox-browser:latest",
            "egress_proxy": "cap-egress-proxy:latest",
        },
        "gates": gates,
        "gate_summary": {
            "total": len(ALL_GATES),
            "passed": passed,
            "failed": sum(1 for v in gates.values() if v == "FAIL"),
            "not_run": sum(1 for v in gates.values() if v == "NOT_RUN"),
        },
        "test_results": results,
    }
    out_json = OUT_DIR / "cap-28.6-k8s-certification.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    human = OUT_DIR / "CAP Phase 28.6 - Kubernetes Certification Gates.md"
    lines = [
        "# CAP Phase 28.6 -- Kubernetes Certification Gates",
        "",
        f"- phase: 28.6 | commit: `{payload['commit']}`",
        f"- kind: {payload['kubernetes'].get('kind')} | k8s: {payload['kubernetes'].get('kubernetes')}",
        f"- gates: {payload['gate_summary']['passed']}/{payload['gate_summary']['total']} PASS",
        "",
        "| Gate | Status |",
        "|---|---|",
    ]
    for gate in ALL_GATES:
        lines.append(f"| {gate} | {gates[gate]} |")
    human.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload, indent=2))
    # exit non-zero when any gate FAILed or the run was incomplete under strict
    bad = [g for g, v in gates.items() if v in ("FAIL", "NOT_RUN")]
    if strict and bad:
        print(f"K8s certification FAILED gates: {bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
