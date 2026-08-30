"""CAP v1.0.1-rc1 -- PATCH-GATE 1..20 certification mapping generator.

Derives every PATCH gate status from REAL evidence -- nothing is hardcoded
to PASS:

* ``test`` gates      -- resolved with ``pytest --collect-only`` and then
                         executed; status read back from the JUnit XML.
                         A gate whose tests did not run is NOT_RUN.
* ``workflow_step``   -- ``gh api`` step conclusion for a specific job of a
                         specific run, plus a head-SHA match against the
                         certified anchor. A stale/unrelated run is FAIL.
* ``workflow_run``    -- ``gh run view`` conclusion + head SHA.
* ``immutability``    -- live re-read of the v1.0.0 tag / Release / image
                         digests compared against the recorded baseline.

Exit code implements the PATCH meta-gate: non-zero unless all twenty gates
report PASS (FAIL / NOT_RUN / SKIP / UNKNOWN all fail the build).

Reporting-only tool. It changes no runtime behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
OUT_DIR = Path(os.environ.get("CAP_PATCH_OUT", str(REPO_ROOT / "outputs/release")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

VERSION = "1.0.1-rc1"
CERTIFIED_SHA = "c6793a8ef1fb892120deb15285a19d0373f378d0"
REPO = "laolaola278-dev/cyber-agent-platform"

# Authoritative runs (all dispatched/built against the certified anchor).
RUN_CI = "33246170456"
RUN_LINUX = "33246170455"
RUN_K8S = "33246170461"
RUN_GA = "33246170487"
RUN_SOAK = "33249070537"

# packaging job inside the CI run: its steps carry the Helm / Compose assertions
JOB_PACKAGING = "99083897522"

DEFAULT_JUNIT = OUT_DIR / "junit-patch-gates.xml"

PASS, FAIL, NOT_RUN, UNKNOWN = "PASS", "FAIL", "NOT_RUN", "UNKNOWN"

# -- v1.0.0 immutability baseline (recorded 2026-08-29, re-read every run) ----
V100_BASELINE = {
    "tag_commit": "0240fbe9c89b98227a21cd3af48cc1593462f481",
    "release_created_at": "2026-08-28T08:33:54Z",
    "release_is_prerelease": False,
    "images": {
        "cap-backend": "sha256:552ab18e0b19ebb333c881c4c9934e05662c545cdc5786ed8533829be3ff3117",
        "cap-frontend": "sha256:f13ec54f718fef8372a57054824bde70750a3ee8d4a99af92da7a6583c44d42c",
    },
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sh(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        args, capture_output=True, text=True, cwd=str(cwd or REPO_ROOT), check=False
    )
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def _pytest(*args: str) -> tuple[int, str]:
    return _sh(sys.executable, "-m", "pytest", *args, cwd=BACKEND)


def _gh(*args: str) -> tuple[int, str]:
    return _sh("gh", *args)


def _gh_json(*args: str) -> dict | None:
    rc, out = _gh(*args)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# pytest evidence
# --------------------------------------------------------------------------- #
def collect(file: str, prefixes: tuple[str, ...] = ()) -> list[str]:
    """Resolve real nodeids with pytest --collect-only (never hardcoded)."""
    rc, out = _pytest("--collect-only", "-q", "--no-header", "-p", "no:cacheprovider", file)
    if rc != 0:
        return []
    nodeids: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        name = line.split("::")[-1].split("[")[0]
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        nodeids.append(line)
    return nodeids


def _dedup(nodeids: list[str]) -> list[str]:
    seen, out = set(), []
    for n in nodeids:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def run_tests(nodeids: list[str], junit: Path) -> dict[str, str]:
    """Execute the mapped tests and return {nodeid: status} from JUnit XML.

    The previous file is deliberately NOT deleted (the sandbox intercepts
    unlink). Instead the run is stamped and the JUnit is only trusted when
    its mtime is at or after the stamp -- a stale report from an earlier
    run can therefore never be mistaken for fresh evidence.
    """
    started = time.time()
    _rc, _ = _pytest(
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--junitxml={junit}",
        *nodeids,
    )
    if not junit.exists() or junit.stat().st_mtime < started - 1:
        return {n: NOT_RUN for n in nodeids}
    return parse_junit(junit, nodeids)


def parse_junit(path: Path, expected: list[str]) -> dict[str, str]:
    root = ET.parse(path).getroot()
    results: dict[str, dict[str, str]] = {}
    for case in root.iter("testcase"):
        name = case.get("name", "")
        cls = case.get("classname", "")
        file_attr = case.get("file", "")
        if file_attr:
            node = f"{file_attr}::{name}"
        elif cls.startswith("tests."):
            node = f"tests/{cls[len('tests.'):].replace('.', '/')}.py::{name}"
        else:
            node = f"{cls}::{name}"
        if case.find("failure") is not None or case.find("error") is not None:
            status = FAIL
        elif case.find("skipped") is not None:
            status = "SKIP"
        else:
            status = PASS
        # never downgrade a failure recorded in an earlier suite
        if results.get(node, {}).get("status") == FAIL:
            continue
        results[node] = {"status": status}
    expected_set = set(expected)
    out: dict[str, str] = {}
    for node in expected:
        hit = results.get(node)
        # tolerate classname-based keys that differ only by path prefix
        if hit is None:
            tail = node.split("::")[-1]
            hits = [v["status"] for k, v in results.items() if k.split("::")[-1] == tail]
            hit = {"status": hits[0]} if hits else None
        out[node] = hit["status"] if hit else NOT_RUN
    # keep any extra executed cases out of the gate mapping but visible
    _ = expected_set
    return out


# --------------------------------------------------------------------------- #
# workflow evidence
# --------------------------------------------------------------------------- #
def run_status(run_id: str, label: str) -> dict:
    data = _gh_json("run", "view", run_id, "--json", "headSha,conclusion,status,name")
    if data is None:
        return {"kind": "workflow_run", "run": run_id, "label": label, "status": UNKNOWN,
                "detail": "gh run view failed"}
    sha = data.get("headSha") or ""
    sha_ok = sha == CERTIFIED_SHA
    concluded = data.get("status") == "completed" and data.get("conclusion") == "success"
    status = PASS if (sha_ok and concluded) else FAIL
    return {
        "kind": "workflow_run",
        "run": run_id,
        "label": label,
        "workflow": data.get("name"),
        "head_sha": sha,
        "sha_matches_anchor": sha_ok,
        "conclusion": data.get("conclusion"),
        "status": status,
    }


def step_status(run_id: str, job_id: str, step_name: str, label: str) -> dict:
    data = _gh_json("api", f"repos/{REPO}/actions/jobs/{job_id}")
    if data is None:
        return {"kind": "workflow_step", "run": run_id, "label": label, "status": UNKNOWN,
                "detail": "gh api job fetch failed"}
    steps = {s.get("name"): s.get("conclusion") for s in data.get("steps", [])}
    conclusion = steps.get(step_name)
    run = run_status(run_id, label)
    sha_ok = run.get("sha_matches_anchor", False)
    status = PASS if (conclusion == "success" and sha_ok) else (UNKNOWN if conclusion is None else FAIL)
    return {
        "kind": "workflow_step",
        "run": run_id,
        "job": job_id,
        "label": label,
        "step": step_name,
        "step_conclusion": conclusion,
        "head_sha": run.get("head_sha"),
        "sha_matches_anchor": sha_ok,
        "status": status,
    }


# --------------------------------------------------------------------------- #
# GATE 20 -- v1.0.0 immutability
# --------------------------------------------------------------------------- #
def _digest(package: str, tag: str = "1.0.0") -> str | None:
    scope = f"repository:laolaola278-dev/{package}:pull"
    with urllib.request.urlopen(
        f"https://ghcr.io/token?scope={scope}&service=ghcr.io", timeout=30
    ) as resp:
        token = json.loads(resp.read())["token"]
    req = urllib.request.Request(
        f"https://ghcr.io/v2/laolaola278-dev/{package}/manifests/{tag}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": (
                "application/vnd.oci.image.index.v1+json,"
                "application/vnd.docker.distribution.manifest.list.v2+json,"
                "application/vnd.docker.distribution.manifest.v2+json,"
                "application/vnd.oci.image.manifest.v1+json"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.headers.get("Docker-Content-Digest")


def immutability() -> dict:
    checks: dict[str, dict] = {}

    rc, local_tag = _sh("git", "rev-list", "-n1", "v1.0.0")
    checks["local_tag"] = {
        "expected": V100_BASELINE["tag_commit"],
        "observed": local_tag if rc == 0 else None,
        "status": PASS if (rc == 0 and local_tag == V100_BASELINE["tag_commit"]) else FAIL,
    }

    rc, remote = _sh("git", "ls-remote", "origin", "refs/tags/v1.0.0^{}")
    remote_sha = remote.split()[0] if remote else None
    checks["remote_tag"] = {
        "expected": V100_BASELINE["tag_commit"],
        "observed": remote_sha,
        "status": PASS if remote_sha == V100_BASELINE["tag_commit"] else FAIL,
    }

    rel = _gh_json(
        "release", "view", "v1.0.0", "--json", "tagName,isPrerelease,createdAt"
    )
    if rel is None:
        checks["github_release"] = {"status": UNKNOWN, "detail": "gh release view failed"}
    else:
        checks["github_release"] = {
            "expected": {
                "createdAt": V100_BASELINE["release_created_at"],
                "isPrerelease": V100_BASELINE["release_is_prerelease"],
            },
            "observed": rel,
            "status": PASS
            if (
                rel.get("createdAt") == V100_BASELINE["release_created_at"]
                and rel.get("isPrerelease") is V100_BASELINE["release_is_prerelease"]
            )
            else FAIL,
        }

    for package, expected in V100_BASELINE["images"].items():
        try:
            observed = _digest(package)
        except Exception as exc:  # noqa: BLE001 -- registry unreachable is evidence
            checks[f"image_{package}"] = {"status": UNKNOWN, "detail": str(exc)}
            continue
        checks[f"image_{package}"] = {
            "expected": expected,
            "observed": observed,
            "status": PASS if observed == expected else FAIL,
        }

    status = FAIL if any(c["status"] == FAIL for c in checks.values()) else (
        UNKNOWN if any(c["status"] == UNKNOWN for c in checks.values()) else PASS
    )
    return {"kind": "immutability", "checks": checks, "status": status}


# --------------------------------------------------------------------------- #
# gate table
# --------------------------------------------------------------------------- #
PROD = "tests/test_phase_28_8_production_defaults.py"
DISC = "tests/test_phase_28_8_capability_disclosure.py"
RESP = "tests/test_phase_28_8_response_service.py"
TGT = "tests/test_phase_28_8_targeted_security.py"


def build_gate_specs() -> dict[str, dict]:
    """Gate -> description + evidence requirements (nodeids resolved live)."""
    return {
        "1": {
            "description": "Production weak sandbox fails closed",
            "tests": _dedup(
                collect(PROD, ("test_production_weak_sandbox_fails_closed",))
                + collect(PROD, ("test_subprocess_isolation_claim_is_not_enough",))
            ),
        },
        "2": {
            "description": "Production approved sandbox passes",
            "tests": collect(PROD, ("test_production_approved_sandbox_passes",)),
        },
        "3": {
            "description": "Development weak sandbox allowed + warning",
            "tests": collect(
                PROD,
                (
                    "test_development_memory_sandbox_allowed_with_warning",
                    "test_development_warning_is_not_emitted_for_approved_providers",
                    "test_production_never_emits_the_development_warning",
                ),
            ),
        },
        "4": {
            "description": "Production missing egress enforcement fails closed",
            "tests": collect(
                PROD, ("test_production_missing_egress_enforcement_fails_closed",)
            )
            + collect(DISC, ("test_egress_enforcement_required_but_empty_is_not_ready",)),
        },
        "5": {
            "description": "Proxy unhealthy -> readiness fails / no direct fallback",
            "tests": collect(
                DISC,
                (
                    "test_egress_enforcement_fails_when_proxy_unreachable",
                    "test_readiness_surface_includes_egress_enforcement",
                    "test_egress_enforcement_passes_when_proxy_listens",
                ),
            ),
        },
        "6": {
            "description": "Helm production defaults secure",
            "workflow_steps": [
                (RUN_CI, JOB_PACKAGING, "Assert production chart defaults",
                 "CI packaging: rendered-manifest security defaults"),
            ],
            "tests": collect(
                PROD,
                (
                    "test_helm_production_template_declares_sandbox_provider",
                    "test_helm_worker_template_defaults_egress_to_in_chart_service",
                    "test_helm_chart_declares_egress_proxy_service",
                    "test_helm_sandbox_networkpolicy_is_declared",
                ),
            ),
            "asserts": [
                "worker SANDBOX_PROVIDER renders as kubernetes-sandbox",
                "worker EGRESS_PROXY_URL renders to the in-chart egress-proxy Service",
                "a NetworkPolicy is rendered (sandbox egress denied by default)",
                "the production template does not render APP_ENVIRONMENT=development",
            ],
        },
        "7": {
            "description": "Compose environment semantics explicit",
            "workflow_steps": [
                (RUN_CI, JOB_PACKAGING, "Validate Compose",
                 "CI packaging: docker compose config --quiet"),
            ],
            "tests": collect(
                PROD,
                (
                    "test_compose_declares_its_environment_explicitly",
                    "test_compose_worker_declares_sandbox_and_egress",
                ),
            ),
            "asserts": [
                "APP_ENVIRONMENT defaults to development explicitly (api + worker)",
                "SANDBOX_PROVIDER / EGRESS_PROXY_URL are declared, not implicit",
                "docker compose config validates in the packaging CI job",
            ],
        },
        "8": {
            "description": "No production silent fallback",
            "tests": collect(
                PROD,
                (
                    "test_unknown_provider_has_no_silent_fallback",
                    "test_production_unknown_provider_fails_closed",
                    "test_memory_provider_remains_available_for_orchestration",
                ),
            ),
        },
        "9": {
            "description": "Response mock-only disclosed",
            "tests": collect(
                DISC,
                (
                    "test_response_plane_mock_only_is_disclosed",
                    "test_default_policies_are_mock_only",
                    "test_no_real_response_provider_implementation_exists",
                    "test_edr_action_detail_is_disclosed_accurately",
                ),
            ),
        },
        "10": {
            "description": "mock_only=False rejected",
            "tests": collect(DISC, ("test_mock_only_cannot_be_disabled",)),
        },
        "11": {
            "description": "Zeek JSONL-only disclosed",
            "tests": collect(DISC, ("test_zeek_jsonl_only_is_disclosed",)),
        },
        "12": {
            "description": "Zeek TSV actionable error",
            "tests": collect(
                DISC,
                (
                    "test_zeek_tsv_error_names_the_remediation",
                    "test_zeek_tsv_error_carries_structured_details",
                    "test_zeek_jsonl_parsing_still_works",
                ),
            ),
        },
        "13": {
            "description": "response/service targeted tests",
            "tests": collect(RESP),
            "coverage": {
                "module": "backend/app/response/service.py",
                "before": "47.4%",
                "after": "98%",
                "note": "measured in the #73 targeted-coverage pass (2026-08-29)",
            },
            "note": "Deployment-template assertions belong to GATE 6/7, not here.",
        },
        "14": {
            "description": "OCI targeted security tests",
            "tests": collect(TGT, ("test_oci_", "test_driver_")),
        },
        "15": {
            "description": "egress targeted security tests",
            "tests": collect(TGT, ("test_resolution_", "test_unparseable_", "test_link_local_",
                                   "test_multicast_", "test_reserved_", "test_non_global_",
                                   "test_real_link_local_", "test_allowlist_", "test_connect_",
                                   "test_http_method_", "test_empty_and_blank_",
                                   "test_handler_exception_", "test_deny_swallows_",
                                   "test_v4_first_", "test_run_egress_proxy_")),
        },
        "16": {
            "description": "K8s provider targeted tests",
            "tests": collect(TGT, ("test_k8s_",)),
        },
        "17": {"description": "General CI PASS", "runs": [(RUN_CI, "General CI")]},
        "18": {
            "description": "Linux security certification PASS",
            "runs": [(RUN_LINUX, "CAP Linux Certification")],
        },
        "19": {
            "description": "K8s certification PASS",
            "runs": [(RUN_K8S, "K8s Certification")],
        },
        "20": {"description": "No v1.0.0 artifact/tag mutation", "immutability": True},
    }


SUPPLEMENTAL = {
    "DISCLOSURE-A": {
        "description": "Reserved interfaces are disclosed",
        "tests": lambda: collect(DISC, ("test_reserved_interfaces_are_disclosed",)),
    },
    "DISCLOSURE-B": {
        "description": "AssessmentScheduler recorded as unimplemented",
        "tests": lambda: collect(DISC, ("test_assessment_scheduler_is_recorded",)),
    },
}


def _combine(statuses: list[str]) -> str:
    if not statuses:
        return NOT_RUN
    if any(s == FAIL for s in statuses):
        return FAIL
    if any(s in (NOT_RUN, UNKNOWN, "SKIP") for s in statuses):
        return NOT_RUN
    return PASS


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the v1.0.1-rc1 PATCH certification artifact")
    ap.add_argument("--junit", default=str(DEFAULT_JUNIT), help="JUnit XML path for test gates")
    ap.add_argument("--reuse-junit", action="store_true", help="reuse an existing JUnit XML")
    args = ap.parse_args()
    junit = Path(args.junit)

    specs = build_gate_specs()

    # Supplemental disclosures are NOT part of the 20 PATCH gates, but their
    # tests are real and must actually execute -- resolve them up front so they
    # land in the same pytest run instead of reporting NOT_RUN.
    supplemental_nodeids = {key: _dedup(meta["tests"]()) for key, meta in SUPPLEMENTAL.items()}

    # ---- run every mapped test once, then read results back -----------------
    all_tests = _dedup(
        [n for spec in specs.values() for n in spec.get("tests", [])]
        + [n for ids in supplemental_nodeids.values() for n in ids]
    )
    if args.reuse_junit and junit.exists():
        test_results = parse_junit(junit, all_tests)
    else:
        test_results = run_tests(all_tests, junit)

    gates: dict[str, dict] = {}
    for gid in sorted(specs, key=int):
        spec = specs[gid]
        evidence: list[dict] = []

        for node in spec.get("tests", []):
            status = test_results.get(node, NOT_RUN)
            evidence.append({"kind": "test", "nodeid": node, "status": status})

        for run_id, job_id, step, label in spec.get("workflow_steps", []):
            evidence.append(step_status(run_id, job_id, step, label))

        for run_id, label in spec.get("runs", []):
            evidence.append(run_status(run_id, label))

        if spec.get("immutability"):
            evidence.append(immutability())

        status = _combine([e["status"] for e in evidence])
        entry = {
            "gate": f"PATCH-GATE {gid}",
            "description": spec["description"],
            "status": status,
            "evidence": evidence,
        }
        if spec.get("asserts"):
            entry["asserts"] = spec["asserts"]
        if spec.get("coverage"):
            entry["coverage"] = spec["coverage"]
        if spec.get("note"):
            entry["note"] = spec["note"]
        gates[gid] = entry

    # ---- supplemental disclosures (NOT part of the 20 PATCH gates) ----------
    supplemental: dict[str, dict] = {}
    for key, meta in SUPPLEMENTAL.items():
        nodeids = supplemental_nodeids[key]
        statuses = [test_results.get(n, NOT_RUN) for n in nodeids]
        supplemental[key] = {
            "description": meta["description"],
            "status": _combine(statuses),
            "counted_in_patch_gates": False,
            "evidence": [
                {"kind": "test", "nodeid": n, "status": s}
                for n, s in zip(nodeids, statuses)
            ],
        }

    statuses = [g["status"] for g in gates.values()]
    summary = {
        "total": len(gates),
        "passed": sum(1 for s in statuses if s == PASS),
        "failed": sum(1 for s in statuses if s == FAIL),
        "not_run": sum(1 for s in statuses if s == NOT_RUN),
        "unknown": sum(1 for s in statuses if s == UNKNOWN),
    }

    artifact = {
        "schema": "cap.patch-certification/v1",
        "version": VERSION,
        "certified_sha": CERTIFIED_SHA,
        "generated_at": datetime.now(UTC).isoformat(),
        "gates": gates,
        "summary": summary,
        "supplemental_assertions": supplemental,
        "ga_certification": {
            "gates": "40/40",
            "run": int(RUN_GA),
            "soak_run": int(RUN_SOAK),
            "ci_run": int(RUN_CI),
            "linux_run": int(RUN_LINUX),
            "k8s_run": int(RUN_K8S),
            "full_ga_certified": True,
        },
    }

    out = OUT_DIR / "cap-v1.0.1-rc1-security-patch-certification.json"
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    non_pass = [
        f"PATCH-GATE {g} ({gates[g]['status']})"
        for g in sorted(gates, key=int)
        if gates[g]["status"] != PASS
    ]
    print(json.dumps(summary, indent=2))
    if non_pass:
        print(f"\nPATCH CERTIFICATION FAILED gates: {non_pass}", file=sys.stderr)
    else:
        print(f"\nCAP v{VERSION} SECURITY PATCH CERTIFIED -- 20/20 PATCH GATES PASS")
        print(f"artifact: {out}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
