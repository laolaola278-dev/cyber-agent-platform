"""Phase 28.7 Tier 2 -- GA-GATE 17..19 (migration/upgrade, rollback, secrets).

Runs in the main ``ga-certification`` job AFTER the whole-cluster DR module
(pytest executes files in the listed order), i.e. against the RESTORED
Cluster B with the recovered dataset -- which is exactly what an upgrade
path must protect.

  GA-GATE 17  rolling upgrade: a new helm revision + pod recycling keeps the
              recovered dataset intact and the control plane ready
  GA-GATE 18  helm rollback to the previous revision: service healthy, data
              intact in BOTH directions (rollback AND roll-forward)
  GA-GATE 19  secret rotation (JWT_SECRET/SECRET_KEY): control plane
              restarts, recovers, serves health; dataset intact; original
              secret restored afterwards

These gates deliberately run against real deployments via kubectl/helm --
no mocks. STRICT semantics apply (CAP_K8S_STRICT=1 -> no silent skips).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "cap"
INFRA_NS = "cap-infra"
RELEASE = "cap"
STRICT = os.environ.get("CAP_K8S_STRICT") == "1"
REPORT_DIR = Path(
    os.environ.get("GA_REPORT_DIR", str(REPO_ROOT / "outputs" / "ga-dr"))
)


def _cluster_ready() -> bool:
    try:
        proc = subprocess.run(
            ["kubectl", "cluster-info"], capture_output=True, timeout=30
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _require_cluster() -> None:
    if not _cluster_ready():
        if STRICT:
            pytest.fail("kind cluster unavailable (CAP_K8S_STRICT=1 -> SKIP==FAIL)")
        pytest.skip("kind cluster unavailable")


def _run(args: list[str], *, check: bool = True, timeout: float = 600.0):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=check,
            timeout=int(timeout), cwd=str(REPO_ROOT),
        )
    except subprocess.CalledProcessError as error:
        out = (error.stdout or "").strip()[-1500:]
        err = (error.stderr or "").strip()[-1500:]
        raise AssertionError(
            f"command failed rc={error.returncode}: {' '.join(map(str, args))}\n"
            f"--- stdout tail ---\n{out}\n--- stderr tail ---\n{err}"
        ) from error


def _kubectl(args, *, check: bool = True, timeout: float = 300.0):
    return _run(["kubectl", *args], check=check, timeout=timeout)


def _helm(args, *, timeout: float = 900.0):
    return _run(["helm", *args], timeout=timeout)


def _json_k(args):
    return json.loads(_kubectl([*args, "-o", "json"]).stdout)


def _release_revision() -> int:
    out = _run(["helm", "ls", "-n", NAMESPACE, "-o", "json"]).stdout
    for rel in json.loads(out):
        if rel["name"] == RELEASE:
            return int(rel["revision"])
    raise AssertionError(f"helm release {RELEASE!r} not found in ns {NAMESPACE}")


def _rollout_status(deployment: str) -> None:
    _kubectl(
        [
            "-n", NAMESPACE, "rollout", "status",
            f"deployment/{deployment}", "--timeout=600s",
        ],
        timeout=660.0,
    )


def _restart_and_wait(deployment: str) -> None:
    _kubectl(["-n", NAMESPACE, "rollout", "restart", f"deployment/{deployment}"])
    _rollout_status(deployment)


def _pg_count(table: str) -> int:
    proc = _kubectl(
        [
            "-n", INFRA_NS, "exec", "deploy/postgres", "--",
            "psql", "-U", "cap", "-d", "cap", "-tAc",
            f"SELECT count(*) FROM {table}",
        ],
    )
    return int(proc.stdout.strip())


def _dataset_baseline() -> int:
    count = _pg_count("sandbox_executions")
    assert count > 0, (
        "recovered dataset is empty -- tier2 gates must run on the restored "
        "Cluster B (order the pytest invocation after the DR module)"
    )
    return count


def _record(evidence: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "tier2-context.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    existing.update(evidence)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# -- GA-GATE 17: rolling upgrade protects recovered data ----------------------


def test_ga_gate17_helm_upgrade_migration_survives() -> None:
    _require_cluster()
    revision_before = _release_revision()
    baseline = _dataset_baseline()

    # REAL upgrade: change a value that changes live state (backend replicas
    # 3 -> 2), producing genuine pod churn under helm revision management.
    _helm(
        [
            "upgrade", RELEASE, "deployment/helm/cap", "-n", NAMESPACE,
            "--reuse-values",
            "--set", "backend.replicaCount=2",
            "--timeout", "600s",
        ]
    )
    assert _release_revision() == revision_before + 1, (
        "helm upgrade must produce exactly one new release revision"
    )
    _restart_and_wait("cap-cap-worker")  # worker fleet recycles too
    after = _pg_count("sandbox_executions")
    assert after >= baseline, (
        f"upgrade lost data: {baseline} rows before, {after} after"
    )
    _record(
        {
            "gate17_upgrade": {
                "revision_before": revision_before,
                "revision_after": revision_before + 1,
                "rows_before": baseline,
                "rows_after": after,
            }
        }
    )


# -- GA-GATE 18: rollback restores service and data ---------------------------


def test_ga_gate18_helm_rollback_service_healthy() -> None:
    _require_cluster()
    current = _release_revision()
    baseline = _pg_count("sandbox_executions")
    assert current >= 2, "need >=2 revisions to exercise rollback"

    # ROLLBACK to the pre-upgrade revision (backend replicas back to 3)
    _helm(["rollback", RELEASE, str(current - 1), "-n", NAMESPACE,
           "--timeout", "600s"])
    assert _release_revision() == current + 1
    _rollout_status("cap-cap-backend")
    mid = _pg_count("sandbox_executions")
    assert mid >= baseline

    # ROLL-FORWARD again so later gates see the upgraded spec
    _helm(["upgrade", RELEASE, "deployment/helm/cap", "-n", NAMESPACE,
           "--reuse-values", "--timeout", "600s"])
    _rollout_status("cap-cap-backend")
    after = _pg_count("sandbox_executions")
    assert after >= baseline, (
        f"rollback cycle lost data: baseline={baseline}, final={after}"
    )
    _record(
        {
            "gate18_rollback": {
                "rolled_back_from": current,
                "revision_after_rollback": current + 1,
                "rows_stable": after >= baseline,
            }
        }
    )


# -- GA-GATE 19: secret rotation recovers the control plane --------------------


def test_ga_gate19_secret_rotation_control_plane_recovers() -> None:
    _require_cluster()
    baseline = _pg_count("sandbox_executions")

    secret = _json_k(["get", "secret", "cap-runtime", "-n", NAMESPACE])
    original = {
        key: base64.b64decode(value).decode()
        for key, value in secret["data"].items()
    }
    rotated = dict(original)
    rotated["JWT_SECRET"] = "rotated-" + uuid.uuid4().hex
    rotated["SECRET_KEY"] = "rotated-" + uuid.uuid4().hex

    args: list[str] = [
        "create", "secret", "generic", "cap-runtime", "-n", NAMESPACE,
        "--dry-run=client", "-o", "yaml",
    ]
    for key, value in rotated.items():
        args += ["--from-literal", f"{key}={value}"]
    manifest = _run(args).stdout
    _run(["kubectl", "apply", "-f", "-"], input=manifest)
    try:
        _restart_and_wait("cap-cap-backend")
        pods = _json_k(
            ["get", "pods", "-n", NAMESPACE,
             "-l", "app.kubernetes.io/component=backend"]
        )
        ready = [
            item
            for item in pods.get("items", [])
            if all(
                c["status"].get("ready")
                for c in item.get("status", {}).get("containerStatuses", [{}])
            )
        ]
        assert ready, "no ready backend pods after secret rotation"
        after = _pg_count("sandbox_executions")
        assert after >= baseline, "secret rotation disturbed the dataset"
    finally:
        # leave the cluster with the ORIGINAL secret for any later gate
        restore_args: list[str] = [
            "create", "secret", "generic", "cap-runtime", "-n", NAMESPACE,
            "--dry-run=client", "-o", "yaml",
        ]
        for key, value in original.items():
            restore_args += ["--from-literal", f"{key}={value}"]
        restore_manifest = _run(restore_args).stdout
        _run(["kubectl", "apply", "-f", "-"], input=restore_manifest)
        _restart_and_wait("cap-cap-backend")

    _record({"gate19_secret_rotation": {"rotated": ["JWT_SECRET", "SECRET_KEY"],
                                        "recovered": True}})
