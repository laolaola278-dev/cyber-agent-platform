"""Phase 28.7 -- generate the machine-readable GA certification artifact.

Gates are derived from the JUnit XML of the GA certification test run plus
the measured DR evidence (outputs/ga-dr/ga-dr-context.json). A gate backed
by a test that is not passing is reported FAIL/SKIPPED/NOT_RUN -- never
silently PASS (SKIP == FAIL, NOT_RUN == FAIL).

TWO DECISION MODES (restored STRICT GA semantics):

* development (default): gates without ANY implementing test are PLANNED --
  visible but excluded from the pass/fail decision. Used while gates
  17..40 are landing. Exit 0 means IMPLEMENTED-SCOPE PASS.
* final strict (CAP_GA_STRICT=1): the FULL GA meta-gate. PLANNED ==
  failure, exactly like SKIP/NOT_RUN/FAIL. Exit 0 requires GA-GATE 1..40
  ALL PASS -- only then is ``full_ga_certified`` true and the report
  titled "FULL GA CERTIFIED". The forbidden state "24 planned + workflow
  green = GA certified" is structurally impossible here: this mode makes
  that state exit non-zero.
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


def _implemented_gates() -> set[str]:
    """Gates with a real certifying test (or baseline) behind them."""
    implemented: set[str] = set(BASELINE_GATES)
    for gate_list in TEST_GATES.values():
        implemented.update(gate_list)
    return implemented


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
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


def _evidence(name: str) -> dict:
    """Optional Tier-2 evidence file (outputs/cap-cert-ga/<name>.json).

    Missing evidence is reported honestly as NOT_EXECUTED -- it never
    fabricates a PASS.
    """
    path = OUT_DIR / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _section(evidence: dict, key: str) -> dict:
    """Evidence section, or an honest NOT_EXECUTED placeholder."""
    value = evidence.get(key)
    if isinstance(value, dict) and value:
        return value
    return {"executed": False, "status": "NOT_EXECUTED"}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = _parse_junit()
    # STRICT GA semantics: CAP_GA_STRICT=1 switches the meta-gate to FINAL
    # mode (PLANNED == failure, 40/40 required). Default stays development
    # mode while gates land; both modes are reported in the artifact.
    final_strict = os.environ.get("CAP_GA_STRICT") == "1"

    gates = {gate: "NOT_RUN" for gate in ALL_GATES}
    implemented = _implemented_gates()
    # Gates without any implementing test are PLANNED, not FAIL: development
    # mode exists to catch a test that silently skips, not to permanently
    # red-flag gates that have never been implemented. In FINAL STRICT mode
    # PLANNED fails the decision below -- that is the whole point of the
    # FULL GA meta-gate.
    for gate in ALL_GATES:
        if gate not in implemented:
            gates[gate] = "PLANNED"
    for gate, status in BASELINE_GATES.items():
        gates[gate] = status
    for test_name, test_gates in TEST_GATES.items():
        outcome = results.get(test_name, "NOT_RUN")
        for gate in test_gates:
            if outcome == "passed":
                gates[gate] = "PASS"
            elif outcome == "failed":
                gates[gate] = "FAIL"
            elif outcome == "skipped":
                # SKIP == FAIL in BOTH modes: a certification test must
                # never be allowed to skip silently.
                gates[gate] = "FAIL"

    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    dr = _dr_evidence()
    tier2 = {
        "soak": _section(_evidence("soak"), "soak"),
        "capacity": _section(_evidence("capacity"), "capacity"),
        "sli": _section(_evidence("sli"), "sli"),
        "slo": _section(_evidence("slo"), "slo"),
        "security": _section(_evidence("security"), "security"),
        "images": _section(_evidence("images"), "images"),
    }
    passed = sum(1 for v in gates.values() if v == "PASS")
    failed = sum(1 for v in gates.values() if v == "FAIL")
    not_run = sum(1 for v in gates.values() if v == "NOT_RUN")
    planned = sum(1 for v in gates.values() if v == "PLANNED")
    full_ga_certified = final_strict and passed == len(ALL_GATES) and failed == 0
    payload = {
        "phase": "28.7",
        "mode": "final-strict" if final_strict else "development",
        "commit": _commit(),
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline": {
            "phase": "28.6",
            "run": os.environ.get("PHASE_28_6_RUN", "32565459369"),
            "gates": "32/32 PASS",
            "commit": "b905393b818c226017f52fb17d732f3e14627d30",
        },
        "heartbeat_invariant": {
            # commit 2ba8bec promoted to architecture invariant: dedicated
            # heartbeat AsyncSession at every app construction site
            "static_scan": results.get(
                "test_arch_every_app_worker_runtime_site_uses_dedicated_"
                "heartbeat_session",
                "NOT_RUN",
            ),
            "behavioral_sqlite": results.get(
                "test_heartbeat_renewal_is_isolated_from_open_main_transaction",
                "NOT_RUN",
            ),
            "behavioral_postgres_authoritative": (
                "PASS"
                if results.get(
                    "test_heartbeat_renewal_isolation_postgres_authoritative"
                )
                == "passed"
                else "NOT_RUN"
            ),
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
        **tier2,
        "full_ga_certified": full_ga_certified,
        "release_status": (
            "FULL GA CERTIFIED -- awaiting explicit release authorization"
            if full_ga_certified
            else "IMPLEMENTED-SCOPE PASS / FULL GA NOT CERTIFIED"
        ),
        "gates": gates,
        "gate_summary": {
            "total": len(ALL_GATES),
            "implemented": len(implemented),
            "passed": passed,
            "failed": failed,
            "not_run": not_run,
            "skipped": 0,
            "planned": planned,
        },
        "test_results": results,
    }
    out_json = OUT_DIR / "cap-28.7-ga-certification.json"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    title = (
        "FULL GA CERTIFIED"
        if full_ga_certified
        else "IMPLEMENTED-SCOPE PASS / FULL GA NOT CERTIFIED"
    )
    lines = [
        "# CAP Phase 28.7 -- GA Reliability Certification Gates",
        "",
        f"- phase: 28.7 | version: `{version}` | commit: `{payload['commit'][:12]}`",
        f"- mode: `{payload['mode']}` | decision: **{title}**",
        f"- gates: {passed}/{len(ALL_GATES)} PASS"
        f" (implemented {len(implemented)}, planned {planned})",
        "",
        "| Gate | Status |",
        "|---|---|",
    ]
    lines += [f"| {gate} | {gates[gate]} |" for gate in ALL_GATES]
    (OUT_DIR / "CAP Phase 28.7 - GA Certification Gates.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    print(json.dumps(payload["gate_summary"], indent=2))
    # Development decision: covers IMPLEMENTED gates only -- FAIL /
    # SKIP-as-FAIL / a mapped test that never ran (NOT_RUN). PLANNED gates
    # are reported but cannot fail the run until an implementation exists.
    bad_dev = [
        g for g, v in gates.items() if v in ("FAIL", "NOT_RUN") and g in implemented
    ]
    if bad_dev:
        print(f"GA certification FAILED gates: {bad_dev}", file=sys.stderr)
        return 1
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
    if final_strict:
        # FINAL STRICT meta-gate: ALL 40 gates must PASS. PLANNED ==
        # failure here -- "24 planned + workflow green" can NEVER equal
        # GA certified.
        bad_final = [g for g, v in gates.items() if v not in ("PASS",)]
        if bad_final:
            print(
                f"FULL GA (strict) NOT CERTIFIED -- non-PASS gates: " f"{bad_final}",
                file=sys.stderr,
            )
            return 1
        print("CAP v1.0.0 GA READY -- awaiting explicit release authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
