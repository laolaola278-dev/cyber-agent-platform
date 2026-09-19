"""Turn a certification run's JUnit + coverage XML into the two tables the
release audit has to publish: which tests did not run, and which modules the
coverage figure is hiding.

Both exist because a single headline number was repeatedly read as a verdict:

  * "1304 passed, 44 skipped" says nothing until the skips are named and grouped
    -- 44 skips of "needs a real OCI runtime" on a laptop is expected, one skip
    of a security test in a certification job is not. The Linux certification has
    its own strict-skip policy (CAP_CERTIFICATION_STRICT); this tool is
    descriptive, never a gate, so it can be run on any job's evidence.
  * the project coverage percentage is a line-weighted average, so a handful of
    modules in the 40s can hide behind a 91% figure. The list is what an operator
    can act on.

Usage:
    python scripts/quality/audit_junit.py outputs/cert-x/junit-backend.xml
        [--coverage outputs/cert-x/coverage.xml] [--out outputs/cert-x]
        [--threshold 80]

Writes skip-audit.json and (with --coverage) coverage-low-modules.json next to
the input unless --out says otherwise, and prints both summaries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

# Markers that explain a skip are written by the suite itself (pytest markers and
# skipif reasons); these families are how the release report groups them.
FAMILY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("oci_runtime", re.compile(r"oci|docker|container runtime|sandbox image", re.IGNORECASE)),
    ("browser", re.compile(r"playwright|chromium|browser", re.IGNORECASE)),
    ("postgres", re.compile(r"postgres|\bpg\b|CAP_PG_TEST", re.IGNORECASE)),
    ("object_store", re.compile(r"s3|minio|object store|CAP283_S3", re.IGNORECASE)),
    ("redis", re.compile(r"redis", re.IGNORECASE)),
    ("network_egress", re.compile(r"egress|outbound|internet|no egress|zap", re.IGNORECASE)),
    ("kubernetes", re.compile(r"kubectl|kind|kube|cluster", re.IGNORECASE)),
    ("linux_only", re.compile(r"linux|cgroup|namespace|posix", re.IGNORECASE)),
    ("benchmark", re.compile(r"benchmark|slow|--deselect|capacity", re.IGNORECASE)),
    ("windows_unsupported", re.compile(r"windows|not on windows|only on", re.IGNORECASE)),
]

#: Reason families whose presence in ANY job's evidence is worth a name. Platform
#: families (postgres, linux_only, oci_runtime) legitimately skip on a developer
#: machine, so they are reported per family instead of being called critical.
SECURITY_HINT = re.compile(r"security|adversarial|injection|ssrf|secret|isolation", re.IGNORECASE)


def _reason_of(case: ET.Element) -> tuple[str, str]:
    """Return (kind, reason) for a testcase element: skipped or failure text."""
    for child in case:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag in ("skipped", "failure", "error"):
            message = (child.get("message") or "").strip()
            text = (child.text or "").strip()
            return tag, (message or text).splitlines()[0] if (message or text) else ""
    return "", ""


def _family(reason: str) -> str:
    """Why the run says it did not execute -- from the reason text alone."""
    for name, pattern in FAMILY_PATTERNS:
        if pattern.search(reason):
            return name
    return "other"


def audit_skips(junit_path: Path) -> dict[str, Any]:
    """Group every non-passing outcome in a JUnit report by reason family."""
    # input is this project's own CI evidence, never third-party XML
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag.endswith("testsuite") else root.iter("testsuite")
    outcomes: Counter[str] = Counter()
    entries: list[dict[str, str]] = []
    for suite in suites:
        for case in suite.iter("testcase"):
            kind, reason = _reason_of(case)
            if not kind:
                outcomes["passed"] += 1
                continue
            outcomes[kind] += 1
            classname = case.get("classname") or ""
            entries.append(
                {
                    "test": f"{classname}::{case.get('name', '')}",
                    "outcome": kind,
                    "family": _family(reason),
                    # whether the TEST is security-relevant is a property of its
                    # identity, not of the excuse in its skip message: a security
                    # test that skipped because a container runtime is missing is
                    # still a security test that did not run.
                    "security_relevant": bool(SECURITY_HINT.search(classname)),
                    "reason": reason[:300],
                }
            )
    skipped = [entry for entry in entries if entry["outcome"] == "skipped"]
    by_family = Counter(entry["family"] for entry in skipped)
    by_file: Counter[str] = Counter(entry["test"].split("::")[0] for entry in skipped)
    security_skips = sorted(
        entry["test"] for entry in skipped if entry["security_relevant"]
    )
    return {
        "source": str(junit_path),
        "outcomes": dict(outcomes),
        "skipped_total": len(skipped),
        "skipped_by_family": dict(sorted(by_family.items(), key=lambda kv: (-kv[1], kv[0]))),
        "skipped_by_file": dict(sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0]))),
        "security_skips": security_skips,
        "non_passing": [entry for entry in entries if entry["outcome"] != "skipped"],
        "note": (
            "descriptive only: a run's own strictness policy decides whether a skip "
            "is fatal; this tool never fails a build"
        ),
    }


def low_coverage_modules(coverage_path: Path, threshold: float) -> dict[str, Any]:
    """Per-module and per-file line coverage, lowest first, from coverage.py XML.

    Both levels, because a directory average hides the one file inside it at 0%
    that only a DR restore gate executes -- and that file is the actionable item.
    """
    # input is this project's own CI evidence, never third-party XML
    root = ET.parse(coverage_path).getroot()
    totals: dict[str, list[int]] = {}
    files: dict[str, list[int]] = {}
    for klass in root.iter("class"):
        filename = klass.get("filename") or ""
        module = filename.rsplit("/", 1)[0] or filename
        lines = klass.find("lines")
        if lines is None:
            continue
        hit = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        total = sum(1 for _ in lines)
        for table, key in ((totals, module), (files, filename)):
            bucket = table.setdefault(key, [0, 0])
            bucket[0] += hit
            bucket[1] += total

    def _rows(table: dict[str, list[int]], key: str) -> list[dict[str, Any]]:
        out = []
        for name, (hit, total) in table.items():
            if not total:
                continue
            out.append(
                {
                    key: name,
                    "covered": hit,
                    "statements": total,
                    "percent": round(100.0 * hit / total, 1),
                }
            )
        # lowest first; on a tie the bigger file is the more actionable one
        out.sort(key=lambda row: (row["percent"], -row["statements"], row[key]))
        return out

    rows = _rows(totals, "module")
    file_rows = _rows(files, "file")
    overall_hit = sum(row["covered"] for row in rows)
    overall_total = sum(row["statements"] for row in rows)
    return {
        "source": str(coverage_path),
        "threshold_percent": threshold,
        "overall_percent": round(100.0 * overall_hit / overall_total, 1) if overall_total else None,
        "modules_below_threshold": [row for row in rows if row["percent"] < threshold],
        "module_count": len(rows),
        "worst_files_below_threshold": [
            row for row in file_rows if row["percent"] < threshold
        ][:20],
        "file_count": len(file_rows),
        "note": (
            "line-weighted module coverage; the headline project percentage is "
            "weighted by statement count, so these rows are the actionable part"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("junit", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--threshold", type=float, default=80.0)
    args = parser.parse_args(argv)

    out_dir = args.out or args.junit.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    skip_report = audit_skips(args.junit)
    skip_path = out_dir / "skip-audit.json"
    skip_path.write_text(json.dumps(skip_report, indent=2), encoding="utf-8")
    print(f"skips: {skip_report['skipped_total']} total -> {skip_path}")
    for family, count in skip_report["skipped_by_family"].items():
        print(f"  {family}: {count}")
    if skip_report["security_skips"]:
        print(f"  SECURITY-relevant tests that did not run: {skip_report['security_skips']}")
    for entry in skip_report["non_passing"]:
        print(f"  {entry['outcome']}: {entry['test']} :: {entry['reason'][:120]}")

    if args.coverage is not None:
        coverage_report = low_coverage_modules(args.coverage, args.threshold)
        coverage_path = out_dir / "coverage-low-modules.json"
        coverage_path.write_text(json.dumps(coverage_report, indent=2), encoding="utf-8")
        print(f"coverage: overall {coverage_report['overall_percent']}% -> {coverage_path}")
        for row in coverage_report["modules_below_threshold"]:
            print(f"  {row['percent']:>5}%  {row['module']} ({row['covered']}/{row['statements']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
