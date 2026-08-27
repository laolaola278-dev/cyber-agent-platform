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
    # Tier 2 (cluster): upgrade / rollback / secret rotation on Cluster B
    "test_ga_gate17_helm_upgrade_migration_survives": ["GA-GATE 17"],
    "test_ga_gate18_helm_rollback_service_healthy": ["GA-GATE 18"],
    "test_ga_gate19_secret_rotation_control_plane_recovers": ["GA-GATE 19"],
    # Tier 2 (supply-chain job): images / SBOM / trivy / provenance / SLI
    "test_ga_gate20_release_images_pinned_sha256": ["GA-GATE 20"],
    "test_ga_gate21_sbom_spdx_and_cyclonedx": ["GA-GATE 21"],
    "test_ga_gate22_trivy_blocking_policy": ["GA-GATE 22"],
    "test_ga_gate23_provenance_attestation": ["GA-GATE 23"],
    "test_ga_gate29_machine_readable_sli_definitions": ["GA-GATE 29"],
    # Tier 2 (resilience): real dependency fault injection on Cluster B
    "test_ga_gate36_pg_connection_exhaustion_controlled": ["GA-GATE 36"],
    "test_ga_gate37_object_store_outage_fails_closed_and_recovers": [
        "GA-GATE 37"
    ],
    "test_ga_gate38_dns_outage_fails_closed_and_recovers": ["GA-GATE 38"],
    "test_ga_gate39_egress_proxy_outage_no_direct_bypass": ["GA-GATE 39"],
    # Tier 2 (capacity): documented matrix + overload backpressure
    "test_ga_gate27_capacity_envelope_matrix": ["GA-GATE 27"],
    "test_ga_gate28_overload_backpressure_clean": ["GA-GATE 28"],
    # Tier 2 (ops): SLO candidates / alerts / runbooks
    "test_ga_gate30_slo_candidates_from_real_data": ["GA-GATE 30"],
    "test_ga_gate31_alert_rules_fire_and_resolve": ["GA-GATE 31"],
    "test_ga_gate32_runbook_exercises": ["GA-GATE 32"],
    # Tier 2 (reliability workflow -- nightly/dispatch, own cluster):
    # soak / leak / orphan / under-load upgrade+rollback
    "test_ga_gate24_two_hour_soak_full_workload": ["GA-GATE 24"],
    "test_ga_gate25_worker_memory_no_leak": ["GA-GATE 25"],
    "test_ga_gate26_orphan_accumulation_zero": ["GA-GATE 26"],
    "test_ga_gate34_upgrade_under_load_bounded": ["GA-GATE 34"],
    "test_ga_gate35_rollback_under_load_bounded": ["GA-GATE 35"],
}

# Gates certified ONLY by the reliability workflow. When its JUnit is not
# part of THIS run they fall back to PLANNED (development mode keeps exit 0;
# final-strict mode fails on PLANNED -- exactly the FULL GA semantics).
RELIABILITY_GATES = {
    "GA-GATE 24",
    "GA-GATE 25",
    "GA-GATE 26",
    "GA-GATE 34",
    "GA-GATE 35",
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
    """Merge EVERY junit-*.xml in the output dir.

    Gates come from multiple CI jobs: junit-ga.xml (cluster job: DR +
    tier2-cluster) AND junit-supply-chain.xml (supply-chain job: images/
    SBOM/trivy/provenance/SLI). The supply-chain job's evidence reaches
    this runner via a download-artifact step.
    """
    candidates = sorted(OUT_DIR.glob("junit-*.xml"))
    if not candidates:
        legacy = REPO_ROOT / "outputs" / "cap-cert-ga" / "junit-ga.xml"
        if legacy.exists():
            candidates = [legacy]
    results: dict[str, str] = {}
    for candidate in candidates:
        root = ET.parse(candidate).getroot()
        for case in root.iter("testcase"):
            name = case.get("name", "")
            if case.find("failure") is not None or case.find("error") is not None:
                # never downgrade a failure from an earlier file
                results[name] = "failed"
            elif case.find("skipped") is not None:
                results.setdefault(name, "skipped")
            else:
                results.setdefault(name, "passed")
    return results


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


# Measured Tier-2 evidence is produced by the reliability (soak) and GA
# (capacity / backpressure) jobs into outputs/ga-dr/ as soak-context.json /
# capacity.json / backpressure.json -- NOT as outputs/cap-cert-ga/<section>.json.
# These helpers *shape* that evidence on read so the descriptive blocks carry the
# REAL measured numbers, while the `executed` flag is driven by the matching
# JUnit gate outcome (PASS == executed). Nothing is ever hardcoded.
#
# section key -> (certifying gate, candidate raw evidence paths in priority order)
TIER2_EVIDENCE: dict[str, tuple[str, list[Path]]] = {
    "soak": ("GA-GATE 24", [DR_DIR / "soak-context.json", OUT_DIR / "soak.json"]),
    "capacity": ("GA-GATE 27", [DR_DIR / "capacity.json", OUT_DIR / "capacity.json"]),
    "backpressure": ("GA-GATE 28", [DR_DIR / "backpressure.json", OUT_DIR / "backpressure.json"]),
}


def _load_tier2(section: str) -> tuple[dict | None, Path | None]:
    """Return (parsed_evidence, source_path) for a Tier-2 section, or (None, None)."""
    _expected_gate, candidates = TIER2_EVIDENCE[section]
    for path in candidates:
        if not path.exists():
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed, path
    return None, None


def _tier2_evidence(gates: dict[str, str], section: str) -> dict:
    """Load a measured Tier-2 evidence block and gate-link its state.

    executed is TRUE only when the expected gate PASSED in JUnit AND the raw
    evidence file is present with a matching `gate` header. Real measured
    values are passed through verbatim -- nothing is fabricated.
    """
    expected_gate, _candidates = TIER2_EVIDENCE[section]
    raw, source = _load_tier2(section)
    outcome = gates.get(expected_gate, "NOT_RUN")
    if raw is None:
        return {"executed": False, "status": "NOT_EXECUTED"}
    if str(raw.get("gate")) != expected_gate:
        return {
            "executed": False,
            "status": "MISMATCH",
            "expected_gate": expected_gate,
            "found_gate": raw.get("gate"),
        }
    base = {k: v for k, v in raw.items()}
    base["evidence_source"] = str(source.relative_to(REPO_ROOT))
    if outcome == "PASS":
        base["executed"] = True
        base["status"] = "PASS"
    elif outcome in ("FAIL", "skipped"):
        base["executed"] = False
        base["status"] = outcome
    else:
        base["executed"] = False
        base["status"] = "NOT_RUN"
    return base


def _tier2_evidence_present(section: str) -> bool:
    """True iff the measured Tier-2 evidence file exists with a matching gate header."""
    expected_gate, _candidates = TIER2_EVIDENCE[section]
    raw, _source = _load_tier2(section)
    return bool(raw) and str(raw.get("gate")) == expected_gate


def _suite_status(filename: str, gate: str) -> str:
    """Suite-level evidence gate.

    GATE 33: security-recert.json -- written by the GA workflow after
    confirming the CAP Linux Certification run (the Phase 28.5 adversarial
    security suite) COMPLETED SUCCESSFULLY for exactly this commit SHA.
    Missing / non-success / SHA mismatch => FAIL-level statuses below.

    GATE 40: skip-report.json -- post-run scan of the merged JUnit results.
    {"skipped": n} with n == 0 passes; anything else fails (critical
    skip == 0).
    """
    if gate == "GA-GATE 33":
        recert = _evidence("security-recert")
        conclusion = recert.get("conclusion")
        if not recert:
            return "NOT_RUN"
        if recert.get("head_sha") != _commit():
            # stale re-certification from another commit is NOT evidence
            return "FAIL"
        return "PASS" if conclusion == "success" else "FAIL"
    # GA-GATE 40
    report = _evidence(filename.removesuffix(".json"))
    skipped = report.get("skipped")
    if skipped is None:
        return "NOT_RUN"
    return "PASS" if int(skipped) == 0 else "FAIL"


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

    # Suite-level evidence gates (no single testcase maps 1:1):
    gates["GA-GATE 33"] = _suite_status("junit-security.xml", "GA-GATE 33")
    gates["GA-GATE 40"] = _suite_status("skip-report", "GA-GATE 40")

    # Reliability-workflow gates: PLANNED unless this run carries the
    # reliability JUnit (nightly/dispatch runs and full-GA proofs do).
    if not (OUT_DIR / "junit-reliability.xml").exists():
        for gate in RELIABILITY_GATES:
            gates[gate] = "PLANNED"

    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    dr = _dr_evidence()
    slo_candidates = _evidence("slo-candidates")
    tier2 = {
        "soak": _tier2_evidence(gates, "soak"),
        "capacity": _tier2_evidence(gates, "capacity"),
        "backpressure": _tier2_evidence(gates, "backpressure"),
        "sli": _section(_evidence("sli"), "sli"),
        "slo": (
            {**slo_candidates, "executed": True}
            if slo_candidates
            else {"executed": False, "status": "NOT_EXECUTED"}
        ),
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
    # Artifact-consistency gate (Tier-2 measured evidence): a PASS on GA-GATE
    # 24/27/28 MUST carry its evidence file (soak / capacity / backpressure).
    # Missing evidence alongside a PASSing gate means the runner's outputs/ga-dr
    # staging drifted -- the human report would show numbers the JSON cannot
    # back up (same invariant as the GA-GATE 15/16 RPO/RTO check above).
    for sec in ("soak", "capacity", "backpressure"):
        gate = TIER2_EVIDENCE[sec][0]
        if gates.get(gate) == "PASS" and not _tier2_evidence_present(sec):
            print(
                f"GA-{gate} PASS but Tier-2 {sec} evidence file missing -- "
                "machine artifact inconsistent with measured evidence",
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
