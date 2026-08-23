"""Phase 28.7 -- generate the machine-readable GA certification artifact.

Gates are derived from the JUnit XML of the GA certification test run plus
the measured DR evidence (outputs/ga-dr/ga-dr-context.json). A gate that is
not yet backed by a passing test is reported NOT_RUN -- never silently PASS
(strict: SKIP == FAIL, NOT_RUN == FAIL for the GA decision).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(os.environ.get("CAP_GA_OUT", str(REPO_ROOT / "outputs/cap-cert-ga")))
DR_DIR = Path(os.environ.get("GA_REPORT_DIR", str(REPO_ROOT / "outputs/ga-dr")))

# test function name -> the GA gates it certifies
TEST_GATES: dict[str, list[str]] = {
    "test_ga_gate1_baseline_recorded": ["GA-GATE 1"],
    "test_ga_gate2_pg_backup_independently_stored": ["GA-GATE 2"],
    "test_ga_gate3_object_backup_independently_stored": ["GA-GATE 3"],
    "test_ga_gate4_backup_manifest_integrity": ["GA-GATE 4"],
    "test_ga_gate5_cluster_a_destroyed": ["GA-GATE 5"],
    "test_ga_gate6_fresh_cluster_b": ["GA-GATE 6"],
    "test_ga_gate7_pg_restored": ["GA-GATE 7"],
    "test_ga_gate8_object_store_restored": ["GA-GATE 8"],
    "test_ga_gate9_evidence_integrity_after_restore": ["GA-GATE 9"],
    "test_ga_gate10_idempotency_survives_restore": ["GA-GATE 10"],
    "test_ga_gate11_running_run_auto_recovers": ["GA-GATE 11"],
    "test_ga_gate12_missing_object_detected": ["GA-GATE 12"],
    "test_ga_gate13_orphan_detected_and_gc_safe": ["GA-GATE 13"],
    "test_ga_gate14_digest_corruption_detected": ["GA-GATE 14"],
    "test_ga_gate15_measured_rpo": ["GA-GATE 15"],
    "test_ga_gate16_measured_rto": ["GA-GATE 16"],
    # GA-GATE 17..40 land with their implementations in subsequent
    # certification iterations; until then they are honestly NOT_RUN.
}

BASELINE_GATES: dict[str, str] = {}  # no gate passes by assertion-free default

ALL_GATES = [f"GA-GATE {i}" for i in range(1, 41)]


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=REPO_ROOT, check=False,
    )
    return proc.stdout.strip() or "unknown"


def _parse_junit() -> dict[str, str]:
    for candidate in (
        OUT_DIR / "junit-ga.xml",
        REPO_ROOT / "backend" / "outputs" / "cap-cert-ga" / "junit-ga.xml",
        REPO_ROOT / "outputs" / "cap-cert-ga" / "junit-ga.xml",
    ):
        if candidate.exists():
            root = ET.parse(candidate).getroot()
            results: dict[str, str] = {}
            for case in root.iter("testcase"):
                name = case.get("name", "")
                if case.find("failure") is not None or case.find("error") is not None:
                    results[name] = "failed"
                elif case.find("skipped") is not None:
                    results[name] = "skipped"
                else:
                    results[name] = "passed"
            return results
    return {}


def _dr_evidence() -> dict:
    path = DR_DIR / "ga-dr-context.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _rpo_measured(dr: dict) -> bool:
    rpo = dr.get("rpo") or {}
    try:
        return float(rpo.get("observed_rpo_seconds", 0)) > 0
    except (TypeError, ValueError):
        return False


def _rto_measured(dr: dict) -> bool:
    rto = dr.get("rto") or {}
    try:
        return float(rto.get("rto_seconds", 0)) > 0
    except (TypeError, ValueError):
        return False


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = _parse_junit()
    strict = os.environ.get("CAP_K8S_STRICT") == "1"

    gates = {gate: "NOT_RUN" for gate in ALL_GATES}
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

    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    dr = _dr_evidence()
    payload = {
        "phase": "28.7",
        "commit": _commit(),
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline": {
            "phase": "28.6",
            "run": os.environ.get("PHASE_28_6_RUN", "32565459369"),
            "gates": "32/32 PASS",
            "commit": "b905393b818c226017f52fb17d732f3e14627d30",
        },
        "dr": {
            "cluster_a_destroyed": dr.get("cluster_a", {}).get("destroyed"),
            "cluster_b_fresh": bool(dr.get("cluster_b")),
            "manifest": dr.get("manifest"),
            "running_recovery": dr.get("running_recovery"),
            "reconciliation_initial": dr.get("reconciliation_initial"),
        },
        "rpo": dr.get("rpo"),
        "rto": dr.get("rto"),
        "soak": {"executed": "pending (later certification iteration)"},
        "gates": gates,
        "gate_summary": {
            "total": len(ALL_GATES),
            "passed": sum(1 for v in gates.values() if v == "PASS"),
            "failed": sum(1 for v in gates.values() if v == "FAIL"),
            "not_run": sum(1 for v in gates.values() if v == "NOT_RUN"),
            "skipped": sum(1 for v in gates.values() if v == "SKIPPED"),
        },
        "test_results": results,
    }
    out_json = OUT_DIR / "cap-28.7-ga-certification.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# CAP Phase 28.7 -- GA Reliability Certification Gates",
        "",
        f"- phase: 28.7 | version: `{version}` | commit: `{payload['commit'][:12]}`",
        f"- gates: {payload['gate_summary']['passed']}/{payload['gate_summary']['total']} PASS",
        "",
        "| Gate | Status |",
        "|---|---|",
    ]
    lines += [f"| {gate} | {gates[gate]} |" for gate in ALL_GATES]
    (OUT_DIR / "CAP Phase 28.7 - GA Certification Gates.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    print(json.dumps(payload["gate_summary"], indent=2))
    bad = [g for g, v in gates.items() if v in ("FAIL", "NOT_RUN", "SKIPPED")]
    # Artifact-consistency gate: a PASS on the measured RPO/RTO gates MUST
    # carry the measured values into the machine-readable artifact. A null
    # here means the DR evidence file was not found (path/env drift) -- the
    # human report would show measured values the JSON cannot back up.
    if gates.get("GA-GATE 15") == "PASS" and not _rpo_measured(dr):
        print(
            "GA-GATE 15 PASS but rpo.observed_rpo_seconds missing in JSON "
            "-- machine artifact inconsistent with measured evidence",
            file=sys.stderr,
        )
        return 1
    if gates.get("GA-GATE 16") == "PASS" and not _rto_measured(dr):
        print(
            "GA-GATE 16 PASS but rto.rto_seconds missing in JSON "
            "-- machine artifact inconsistent with measured evidence",
            file=sys.stderr,
        )
        return 1
    if strict and bad:
        print(f"GA certification FAILED gates: {bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
