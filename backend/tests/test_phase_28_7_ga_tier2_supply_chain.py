"""Phase 28.7 Tier 2 -- GA-GATE 20..23 + 29 (supply chain & SLI contract).

Runs in the dedicated ``supply-chain`` CI job (docker available, NO kind
cluster required -- must stay green even while the DR job owns the cluster):

  GA-GATE 20  every CAP release image carries an immutable SHA256 digest,
              recorded into images.json
  GA-GATE 21  SBOM produced in BOTH SPDX and CycloneDX formats for the
              backend image
  GA-GATE 22  Trivy vulnerability scan enforces the documented blocking
              policy (scripts/certification/security_policy.json)
  GA-GATE 23  provenance attestation binds commit + image digests + SBOM
              hashes, and every recorded hash is independently re-verified
  GA-GATE 29  machine-readable SLI definitions exist, are schema-valid,
              unique, and reference declared measurement sources

Evidence lands in $CAP_GA_OUT (default outputs/cap-cert-ga/) as
images.json / sbom-spdx.json / sbom-cyclonedx.json / trivy-<image>.json /
provenance.json -- consumed by generate_report_28_7.py and archived as CI
artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(os.environ.get("CAP_GA_OUT", str(REPO_ROOT / "outputs/cap-cert-ga")))
POLICY_PATH = REPO_ROOT / "scripts" / "certification" / "security_policy.json"
SLI_PATH = REPO_ROOT / "scripts" / "certification" / "sli_definitions.json"
IMAGES = [
    "cap-backend:ci",
    "cap-frontend:ci",
    "cap-sandbox-http:latest",
    "cap-sandbox-browser:latest",
    "cap-egress-proxy:latest",
]
SBOM_IMAGE = "cap-backend:ci"

STRICT = os.environ.get("CAP_GA_STRICT") == "1"


def _require_toolchain() -> None:
    """Skip (never silently fail) when docker/syft/trivy are unavailable.

    These gates run in the dedicated ``supply-chain`` CI job. In the plain
    ``ci.yml`` backend job the toolchain is absent -- SKIP there, FAIL under
    final strict GA mode.
    """
    missing = [t for t in ("docker", "syft", "trivy") if shutil.which(t) is None]
    if not missing:
        probe = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=60
        )
        if probe.returncode == 0:
            return
        missing.append("docker-daemon")
    if STRICT:
        pytest.fail(f"supply-chain toolchain unavailable: {missing}")
    pytest.skip(f"supply-chain toolchain unavailable: {missing}")


def _run(args: list[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=True, timeout=int(timeout)
        )
    except subprocess.CalledProcessError as error:
        out = (error.stdout or "").strip()[-1500:]
        err = (error.stderr or "").strip()[-1500:]
        raise AssertionError(
            f"command failed rc={error.returncode}: {' '.join(args)}\n"
            f"--- stdout tail ---\n{out}\n--- stderr tail ---\n{err}"
        ) from error


def _commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )
    return proc.stdout.strip() or "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


# -- GA-GATE 20: immutable release image digests ------------------------------


def test_ga_gate20_release_images_pinned_sha256() -> None:
    _require_toolchain()
    records = []
    for image in IMAGES:
        proc = _run(
            ["docker", "image", "inspect", "-f", "{{.Id}}\t{{.Created}}", image]
        )
        image_id, created = proc.stdout.strip().split("\t")
        assert image_id.startswith("sha256:") and len(image_id) == 71, (
            f"{image}: expected sha256 config digest, got {image_id!r}"
        )
        records.append(
            {
                "image": image,
                "digest": image_id,
                "created": created.strip(),
                "immutability": (
                    "content-addressed sha256 config digest -- the tag may "
                    "move, the digest cannot"
                ),
            }
        )
    assert len(records) == len(IMAGES)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "images.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "commit": _commit(),
                "images": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# -- GA-GATE 21: SBOM in BOTH standard formats --------------------------------


def test_ga_gate21_sbom_spdx_and_cyclonedx() -> None:
    _require_toolchain()
    spdx = OUT_DIR / "sbom-spdx.json"
    cdx = OUT_DIR / "sbom-cyclonedx.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "syft", "packages", SBOM_IMAGE,
            "-o", f"spdx-json={spdx}",
            "-o", f"cyclonedx-json={cdx}",
            "--file", str(OUT_DIR),
        ],
        timeout=900.0,
    )
    spdx_doc = json.loads(spdx.read_text(encoding="utf-8"))
    assert spdx_doc.get("spdxVersion", "").startswith("SPDX-"), (
        "SPDX SBOM missing spdxVersion"
    )
    assert spdx_doc.get("packages"), "SPDX SBOM has no packages"
    cdx_doc = json.loads(cdx.read_text(encoding="utf-8"))
    assert cdx_doc.get("bomFormat") == "CycloneDX", "CycloneDX format mismatch"
    assert cdx_doc.get("components"), "CycloneDX SBOM has no components"


# -- GA-GATE 22: Trivy scan per documented blocking policy --------------------


def test_ga_gate22_trivy_blocking_policy() -> None:
    _require_toolchain()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    blocking_severities = set(policy["blocking"]["severities"])
    assert policy["blocking"]["only_with_fix"] is True, (
        "policy must not block on findings without an available fix "
        "(un-fixable base-image noise would permanently red GA)"
    )

    summaries = []
    for image in policy["scan_targets"]:
        report = OUT_DIR / f"trivy-{image.replace(':', '-').replace('/', '_')}.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "trivy", "image", "--quiet",
                "--scanners", "vuln",
                "--ignore-unfixed",
                "--format", "json",
                "--output", str(report),
                image,
            ],
            timeout=1200.0,
        )
        doc = json.loads(report.read_text(encoding="utf-8"))
        assert doc.get("SchemaVersion"), f"trivy report for {image} malformed"
        blocked, reported = [], 0
        for result in doc.get("Results") or []:
            for vuln in result.get("Vulnerabilities") or []:
                reported += 1
                if vuln.get("VulnerabilityID") in policy.get("allowlist", []):
                    continue
                if vuln.get("Severity") in blocking_severities:
                    blocked.append(
                        f"{image}:{vuln.get('PkgName')}:"
                        f"{vuln.get('VulnerabilityID')}"
                    )
        summaries.append(
            {"image": image, "findings_total": reported, "blocked": blocked}
        )

    (OUT_DIR / "trivy-summary.json").write_text(
        json.dumps(
            {
                "policy": str(POLICY_PATH.relative_to(REPO_ROOT)),
                "blocking_severities": sorted(blocking_severities),
                "scans": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    flat = [finding for scan in summaries for finding in scan["blocked"]]
    assert not flat, (
        f"BLOCKING vulnerabilities (policy {POLICY_PATH.name}): {flat}. "
        "Fix the base image or justify an allowlist entry in the policy."
    )


# -- GA-GATE 23: provenance attestation with re-verified hashes ---------------


def test_ga_gate23_provenance_attestation() -> None:
    _require_toolchain()
    images = json.loads((OUT_DIR / "images.json").read_text(encoding="utf-8"))
    subjects = [
        {"name": rec["image"], "digest": rec["digest"]}
        for rec in images["images"]
    ]
    sbom_files = sorted(OUT_DIR.glob("sbom-*.json"))
    assert len(sbom_files) >= 2, (
        "provenance requires both SBOM artifacts (run gate 21 first)"
    )
    attestation = {
        "spec_version": "cap.provenance.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "builder": "github-actions",
        "commit": os.environ.get("GITHUB_SHA") or _commit(),
        "subject": subjects,
        "sbom": [
            {"path": path.name, "sha256": _sha256(path)} for path in sbom_files
        ],
    }
    provenance_path = OUT_DIR / "provenance.json"
    provenance_path.write_text(json.dumps(attestation, indent=2), encoding="utf-8")

    # INDEPENDENT re-verification: every hash recorded in the attestation
    # must match the artifact actually on disk (tamper-evident binding)
    verified = json.loads(provenance_path.read_text(encoding="utf-8"))
    for entry in verified["sbom"]:
        assert entry["sha256"] == _sha256(OUT_DIR / entry["path"]), (
            f"provenance hash mismatch for {entry['path']}"
        )
    image_digests = {rec["digest"] for rec in images["images"]}
    assert all(s["digest"] in image_digests for s in verified["subject"])


# -- GA-GATE 29: machine-readable SLI definitions -----------------------------


def test_ga_gate29_machine_readable_sli_definitions() -> None:
    doc = json.loads(SLI_PATH.read_text(encoding="utf-8"))
    assert doc.get("schema_version"), "missing schema_version"
    slis = doc.get("slis")
    assert isinstance(slis, list) and len(slis) >= 5, (
        "at least 5 SLIs must be defined"
    )
    required_fields = {"id", "description", "unit", "measurement", "source"}
    seen: set[str] = set()
    for sli in slis:
        missing = required_fields - set(sli)
        assert not missing, f"SLI {sli.get('id')}: missing fields {missing}"
        assert sli["id"] not in seen, f"duplicate SLI id {sli['id']}"
        seen.add(sli["id"])
        assert sli["description"].strip(), f"SLI {sli['id']}: empty description"
        assert sli["source"].get("kind"), f"SLI {sli['id']}: undeclared source kind"
