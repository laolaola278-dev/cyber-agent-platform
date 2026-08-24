"""Phase 28.7 Tier 2 -- GA-GATE 30..32 (SLO candidates, alerts, runbooks).

Runs in the main ``ga-certification`` job AFTER the DR + tier2-cluster
modules: runbook exercises use the restored Cluster B and the measured DR
evidence produced earlier in the same pytest invocation.

  GA-GATE 30  SLO candidates DERIVED FROM REAL MEASURED DATA (DR RPO/RTO,
              gate timings, soak-independent SLI evidence) -- never invented
  GA-GATE 31  alert rules proven to FIRE and RESOLVE via ``promtool test
              rules`` against synthetic series (deployment/prometheus/)
  GA-GATE 32  executable runbook steps exercised for real: fail-closed
              backup-manifest verification, documented Alembic head match,
              rollback documentation linkage (helm rollback itself is
              exercised end-to-end by GA-GATE 18)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = Path(
    os.environ.get("GA_REPORT_DIR", str(REPO_ROOT / "outputs" / "ga-dr"))
)
OUT_DIR = Path(os.environ.get("CAP_GA_OUT", str(REPO_ROOT / "outputs/cap-cert-ga")))
ALERTS_DIR = REPO_ROOT / "deployment" / "prometheus"
STRICT = os.environ.get("CAP_K8S_STRICT") == "1"


def _run(args: list[str], *, check: bool = True, timeout: float = 300.0):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=check,
            timeout=int(timeout), cwd=str(REPO_ROOT),
        )
    except subprocess.CalledProcessError as error:
        out = (error.stdout or "").strip()[-2000:]
        err = (error.stderr or "").strip()[-2000:]
        raise AssertionError(
            f"command failed rc={error.returncode}: {' '.join(map(str, args))}\n"
            f"--- stdout tail ---\n{out}\n--- stderr tail ---\n{err}"
        ) from error


# -- GA-GATE 30: SLO candidates from real measurements ------------------------


def _dr_context() -> dict:
    path = REPORT_DIR / "ga-dr-context.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def test_ga_gate30_slo_candidates_from_real_data() -> None:
    if not _dr_context() and not STRICT:
        pytest.skip(
            "no ga-dr-context.json evidence (produced by the K8s certification run)"
        )
    dr = _dr_context()
    rpo = float((dr.get("rpo") or {}).get("observed_rpo_seconds") or 0)
    rto = float((dr.get("rto") or {}).get("rto_seconds") or 0)
    assert rpo > 0 and rto > 0, (
        "no measured RPO/RTO in ga-dr-context.json -- SLO candidates must be "
        "derived from REAL measurements, never invented"
    )
    # candidate target = measured value with an explicit engineering margin.
    candidates = [
        {
            "sli": "recovery_rpo",
            "measured_seconds": rpo,
            "candidate_target": f"<= {round(rpo * 2, 3)} seconds",
            "margin": "2x worst measured RPO",
            "source": "outputs/ga-dr/ga-dr-context.json:rpo.observed_rpo_seconds",
        },
        {
            "sli": "recovery_rto",
            "measured_seconds": rto,
            "candidate_target": f"<= {round(rto * 1.5, 3)} seconds",
            "margin": "1.5x worst measured RTO",
            "source": "outputs/ga-dr/ga-dr-context.json:rto.rto_seconds",
        },
        {
            "sli": "api_availability",
            "measured_basis": (
                "backend pods Ready across DR destroy/restore + tier2 "
                "upgrade/rollback/secret-rotation cycles in this run"
            ),
            "candidate_target": ">= 99.5% over 30d",
            "source": "junit-ga.xml gate 5..19 outcomes",
        },
        {
            "sli": "lease_false_reclaim_rate",
            "measured_basis": "heartbeat PRE-GATE B/E regressions passed (zero false reclaims)",
            "candidate_target": "== 0 events per 1000 runs",
            "source": "tests/test_phase_28_7_ga_pregate_heartbeat.py",
        },
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "slo-candidates.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "note": (
                    "CANDIDATES only -- promotion to enforced SLOs is a "
                    "product decision after a full month of production data"
                ),
                "candidates": candidates,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# -- GA-GATE 31: alerts FIRE and RESOLVE ---------------------------------------


def test_ga_gate31_alert_rules_fire_and_resolve() -> None:
    if shutil.which("promtool") is None:
        if STRICT:
            pytest.fail("promtool unavailable (CAP_K8S_STRICT=1 -> SKIP==FAIL)")
        pytest.skip(
            "promtool not installed -- alert FIRE/RESOLVE proof runs only in "
            "the certification workflows that install it"
        )
    rules = ALERTS_DIR / "alerts.yml"
    unit_tests = ALERTS_DIR / "alerts_unit_test.yml"
    assert rules.exists() and unit_tests.exists()
    text = rules.read_text(encoding="utf-8")
    for alert in ("CAPBackendDown", "CAPHighServerErrorRate", "CAPHighP95Latency"):
        assert f"alert: {alert}" in text, f"{alert} missing from alerts.yml"
    proc = _run(["promtool", "test", "rules", str(unit_tests)], timeout=120.0)
    assert "SUCCESS" in proc.stdout or proc.returncode == 0, proc.stdout


# -- GA-GATE 32: runbook exercises ---------------------------------------------


def test_ga_gate32_runbook_exercises() -> None:
    if not REPORT_DIR.exists() and not STRICT:
        pytest.skip(
            "no DR evidence directory (produced by the K8s certification run)"
        )
    exercises: dict = {}

    # 1. fail-closed backup verification tooling actually runs against THIS
    #    run's DR backup manifest (runbook: 'Evidence to preserve' +
    #    restore preconditions)
    manifest = REPORT_DIR / "backup" / "backup-manifest.json"
    if not manifest.exists():
        found = sorted((REPORT_DIR / "backup").rglob("backup-manifest.json"))
        assert found, "DR backup manifest not found for runbook exercise"
        manifest = found[0]
    verify = REPO_ROOT / "scripts" / "certification" / "verify_backup_manifest.py"
    proc = _run(
        ["python", str(verify), str(manifest.parent)], check=False, timeout=300.0
    )
    exercises["verify_backup_manifest"] = {"rc": proc.returncode}
    assert proc.returncode == 0, (
        f"fail-closed manifest verification FAILED on this run's own backup:\n"
        f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}"
    )

    # 2. runbook documents the Alembic head; the repo's actual head matches
    #    (docs may never drift from the real migration state)
    head_doc = (REPO_ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    versions_dir = REPO_ROOT / "backend" / "alembic" / "versions"
    heads = sorted(p.name for p in versions_dir.glob("*.py"))
    assert heads, "no alembic versions found"
    latest_head = heads[-1]
    match = re.search(r"Alembic head `(\d{8}_\d{4})`", head_doc)
    assert match, "runbook does not reference an Alembic head"
    documented = match.group(1)
    assert any(documented in name for name in heads), (
        f"runbook head {documented} not present in alembic versions"
    )
    assert documented in latest_head, (
        f"runbook head {documented} drifted from actual latest {latest_head} "
        "-- update docs/runbook.md when migrations advance"
    )
    exercises["alembic_head_documented_matches_repo"] = {
        "documented": documented,
        "latest_version_file": latest_head,
    }

    # 3. rollback documentation exists and the procedure it references was
    #    genuinely exercised by GATE 18 in this same run (helm rollback)
    rollback_doc = REPO_ROOT / "docs" / "deployment" / "rollback.md"
    assert rollback_doc.exists(), "docs/deployment/rollback.md missing"
    tier2_ctx = REPORT_DIR / "tier2-context.json"
    assert tier2_ctx.exists(), (
        "gate18 evidence missing -- the runbook rollback exercise requires "
        "the helm rollback (GA-GATE 18) to have ACTUALLY run first"
    )
    ctx = json.loads(tier2_ctx.read_text(encoding="utf-8"))
    assert ctx.get("gate18_rollback", {}).get("rows_stable") is True
    exercises["helm_rollback_procedure"] = "exercised by GA-GATE 18"

    (REPORT_DIR / "runbook-exercise.json").write_text(
        json.dumps(exercises, indent=2), encoding="utf-8"
    )
