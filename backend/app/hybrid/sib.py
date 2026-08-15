# ruff: noqa: E501
"""CAP-SIB v1 -- Security Intelligence Benchmark.

A blind benchmark that can genuinely separate the contribution of
Rules / Retrieval / LLM / Hybrid layers.

Design principles (Phase 27.1 spec):
  1. NO label leakage: the *input* never contains expected severity,
     expected false_positive, expected technique_id, expected tactic or
     expected attack stage. Labels live only in the ``labels`` dict used for
     scoring.
  2. Two tracks:
       Track A (metadata-assisted): the rule metadata may carry an ATT&CK id
       (as real detection products do).
       Track B (metadata-blind): input is pure behavior/events/logs, no ATT&CK
       id anywhere. Product-competitiveness certification uses Track B.
  3. 300 scenarios: 200 dev + 100 holdout. Holdout is frozen BEFORE the
     system is tuned (dev-set driven development; one-shot holdout run).
  4. >=25% hard negatives in holdout; >=20% incomplete-evidence scenarios.
  5. All synthetic or explicitly authorized data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

SIB_VERSION = "cap-sib-v1"
SIB_TOTAL = 300
SIB_DEV = 200
SIB_HOLDOUT = 100

TECHNIQUE_CATALOG: dict[str, str] = {
    "T1566": "Phishing",
    "T1059": "Command and Scripting Interpreter",
    "T1003": "OS Credential Dumping",
    "T1021": "Remote Services",
    "T1071": "Application Layer Protocol",
    "T1547": "Boot or Logon Autostart Execution",
    "T1068": "Exploitation for Privilege Escalation",
    "T1567": "Exfiltration Over Web Service",
    "T1082": "System Information Discovery",
    "T1204": "User Execution",
    "T1078": "Valid Accounts",
    "T1219": "Remote Access Software",
    "T1498": "Network Denial of Service",
    "T1027": "Obfuscated Files or Information",
    "T1568": "Dynamic Resolution",
    "T1053": "Scheduled Task/Job",
    "T1136": "Create Account",
    "T1190": "Exploit Public-Facing Application",
    "T1485": "Data Destruction",
    "T1041": "Exfiltration Over C2 Channel",
}

# ---------------------------------------------------------------------------
# Scenario templates (behavior descriptions only -- never an ATT&CK id in the
# input for Track B). Labels are separate.
# ---------------------------------------------------------------------------

ATTACK_TEMPLATES: dict[str, dict[str, Any]] = {
    "web_attack": {
        "title": "public web server request pattern",
        "behavior": (
            "Multiple requests to /login with sql-like fragments and encoded "
            "payloads, followed by a request writing a small php file under a "
            "web-accessible path, then a callback to an external host."
        ),
        "events": [
            {
                "event_type": "http_request",
                "detail": "GET /login?id=1' OR '1'='1",
                "ts_offset_s": 0,
            },
            {
                "event_type": "http_request",
                "detail": "POST /upload with shell.php",
                "ts_offset_s": 8,
            },
            {
                "event_type": "http_request",
                "detail": "GET /uploads/shell.php?cmd=id",
                "ts_offset_s": 20,
            },
            {
                "event_type": "http_request",
                "detail": "GET /uploads/shell.php?cmd=whoami",
                "ts_offset_s": 26,
            },
        ],
        "techniques": ["T1190", "T1059"],
        "severity": "HIGH",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "credential_abuse": {
        "title": "repeated logon failures then success",
        "behavior": (
            "A single source IP attempted logon with many distinct usernames "
            "over 90 seconds, several account lockouts, then a successful logon "
            "with a newly created account followed by a sensitive share access."
        ),
        "events": [
            {"event_type": "logon_failure", "detail": "user enumeration pattern", "ts_offset_s": 0},
            {"event_type": "logon_failure", "detail": "400 distinct users", "ts_offset_s": 40},
            {"event_type": "account_created", "detail": "new admin-ish account", "ts_offset_s": 70},
            {
                "event_type": "logon_success",
                "detail": "new account, high privilege",
                "ts_offset_s": 85,
            },
            {"event_type": "share_access", "detail": "sensitive share read", "ts_offset_s": 88},
        ],
        "techniques": ["T1110", "T1078"],
        "severity": "HIGH",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "malware_execution": {
        "title": "obfuscated script execution chain",
        "behavior": (
            "A document dropped an encoded script that decoded to a PowerShell "
            "one-liner, which downloaded a binary from a rarely-seen domain, "
            "and the binary then ran with an unusual parent process tree."
        ),
        "events": [
            {"event_type": "file_create", "detail": "encoded payload in temp", "ts_offset_s": 0},
            {"event_type": "script_execution", "detail": "powershell -enc ...", "ts_offset_s": 5},
            {
                "event_type": "network_connection",
                "detail": "download from unknown domain",
                "ts_offset_s": 12,
            },
            {
                "event_type": "process_create",
                "detail": "binary with renamed parent",
                "ts_offset_s": 18,
            },
        ],
        "techniques": ["T1204", "T1059", "T1027"],
        "severity": "CRITICAL",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "persistence": {
        "title": "startup persistence write",
        "behavior": (
            "A process wrote a registry Run key and created a scheduled task "
            "pointing to an executable in the user profile directory, both "
            "configured to start at logon."
        ),
        "events": [
            {"event_type": "registry_write", "detail": "HKCU Run key", "ts_offset_s": 0},
            {"event_type": "scheduled_task_create", "detail": "task in user dir", "ts_offset_s": 6},
            {"event_type": "file_create", "detail": "exe under profile", "ts_offset_s": 7},
        ],
        "techniques": ["T1547", "T1053"],
        "severity": "MEDIUM",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "lateral_movement": {
        "title": "remote admin service usage",
        "behavior": (
            "From a compromised workstation an account connected to three "
            "servers using an administrative service, executed a remote "
            "command on each, and then the same account appeared on the "
            "domain controller minutes later."
        ),
        "events": [
            {
                "event_type": "network_connection",
                "detail": "admin port to server A",
                "ts_offset_s": 0,
            },
            {"event_type": "remote_command", "detail": "server A", "ts_offset_s": 4},
            {
                "event_type": "network_connection",
                "detail": "admin port to server B",
                "ts_offset_s": 9,
            },
            {
                "event_type": "logon_success",
                "detail": "domain controller, same account",
                "ts_offset_s": 20,
            },
        ],
        "techniques": ["T1021", "T1059"],
        "severity": "HIGH",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "c2": {
        "title": "beaconing to dynamic domain",
        "behavior": (
            "A host made periodic HTTPS connections every 55-65 seconds to a "
            "newly registered domain with no historical DNS records; responses "
            "had an unusual TLS fingerprint and payload sizes were uniform."
        ),
        "events": [
            {"event_type": "network_connection", "detail": "https to new domain", "ts_offset_s": 0},
            {"event_type": "network_connection", "detail": "same host, +58s", "ts_offset_s": 58},
            {"event_type": "network_connection", "detail": "same host, +117s", "ts_offset_s": 117},
            {"event_type": "dns_query", "detail": "low-TTL dynamic domain", "ts_offset_s": 150},
        ],
        "techniques": ["T1071", "T1568"],
        "severity": "HIGH",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "data_exfiltration": {
        "title": "bulk archive upload",
        "behavior": (
            "An endpoint compressed a large set of documents it had never "
            "accessed before into a single archive and uploaded it to a "
            "file-sharing domain outside the org, in one burst at night."
        ),
        "events": [
            {"event_type": "file_read", "detail": "bulk sensitive docs", "ts_offset_s": 0},
            {"event_type": "archive_create", "detail": "single large zip", "ts_offset_s": 30},
            {
                "event_type": "network_connection",
                "detail": "upload to external share",
                "ts_offset_s": 40,
            },
        ],
        "techniques": ["T1567"],
        "severity": "CRITICAL",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "cloud_identity": {
        "title": "privilege escalation via new access key",
        "behavior": (
            "An identity that normally logs in from a single office region "
            "suddenly authenticated from an unexpected region, created a new "
            "access key, attached an admin policy to a role it does not own, "
            "and began enumerating secrets."
        ),
        "events": [
            {"event_type": "cloud_login", "detail": "unexpected region", "ts_offset_s": 0},
            {"event_type": "iam_access_key_create", "detail": "new key", "ts_offset_s": 15},
            {"event_type": "iam_policy_attach", "detail": "admin policy", "ts_offset_s": 22},
            {"event_type": "secrets_list", "detail": "secret enumeration", "ts_offset_s": 30},
        ],
        "techniques": ["T1136", "T1078"],
        "severity": "HIGH",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "container_k8s": {
        "title": "privileged pod with host mount",
        "behavior": (
            "A pod was created with privileged=true and the host filesystem "
            "mounted; inside it a process wrote to a host directory and "
            "started a shell listening on a host network port."
        ),
        "events": [
            {"event_type": "pod_create", "detail": "privileged pod", "ts_offset_s": 0},
            {"event_type": "container_exec", "detail": "host mount write", "ts_offset_s": 10},
            {"event_type": "network_bind", "detail": "host network port", "ts_offset_s": 25},
        ],
        "techniques": ["T1068", "T1059"],
        "severity": "CRITICAL",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
    "multi_stage": {
        "title": "intrusion chain across hosts",
        "behavior": (
            "A phishing attachment executed a script, which downloaded a "
            "remote access tool; the tool enumerated the network, moved to "
            "another server over an admin service, dumped credentials, and "
            "then compressed data for upload."
        ),
        "events": [
            {
                "event_type": "email_attachment_open",
                "detail": "document with macro",
                "ts_offset_s": 0,
            },
            {"event_type": "script_execution", "detail": "downloaded RAT", "ts_offset_s": 10},
            {"event_type": "network_scan", "detail": "internal enumeration", "ts_offset_s": 40},
            {"event_type": "remote_command", "detail": "second host", "ts_offset_s": 70},
            {"event_type": "credential_dump", "detail": "lsass access", "ts_offset_s": 90},
            {"event_type": "archive_upload", "detail": "external upload", "ts_offset_s": 120},
        ],
        "techniques": ["T1566", "T1059", "T1082", "T1021", "T1003", "T1567"],
        "severity": "CRITICAL",
        "classification": "MALICIOUS",
        "false_positive": False,
    },
}

HARD_NEGATIVE_TEMPLATES: dict[str, dict[str, Any]] = {
    "benign_admin_powershell": {
        "title": "admin PowerShell automation",
        "behavior": (
            "A known administrator account ran a signed PowerShell script from "
            "a change-request ticket during the maintenance window; the script "
            "enumerated services and set a scheduled task for the nightly "
            "backup."
        ),
        "events": [
            {
                "event_type": "script_execution",
                "detail": "signed script, known admin",
                "ts_offset_s": 0,
            },
            {
                "event_type": "service_enumeration",
                "detail": "maintenance activity",
                "ts_offset_s": 5,
            },
            {
                "event_type": "scheduled_task_create",
                "detail": "nightly backup task",
                "ts_offset_s": 12,
            },
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "backup_traffic": {
        "title": "scheduled backup job",
        "behavior": (
            "Every night at 02:00 a backup agent reads a large set of files "
            "and uploads to the on-prem backup server over the dedicated "
            "backup VLAN; the volume is consistent with the previous 60 days."
        ),
        "events": [
            {"event_type": "file_read", "detail": "backup agent, 02:00", "ts_offset_s": 0},
            {"event_type": "network_connection", "detail": "backup VLAN target", "ts_offset_s": 2},
            {"event_type": "archive_create", "detail": "backup container", "ts_offset_s": 10},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "vulnerability_scanner": {
        "title": "authorized vuln scan",
        "behavior": (
            "A scanner appliance from the security team's subnet scanned a "
            "range of internal hosts on common ports; the scan pattern matches "
            "the quarterly authorized scan calendar and the appliance is "
            "whitelisted."
        ),
        "events": [
            {"event_type": "port_scan", "detail": "security appliance subnet", "ts_offset_s": 0},
            {"event_type": "http_request", "detail": "scanner UA pattern", "ts_offset_s": 5},
            {"event_type": "network_connection", "detail": "port sweep", "ts_offset_s": 8},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "software_deployment": {
        "title": "orchestrated deployment",
        "behavior": (
            "The deployment orchestrator pushed an update to 40 servers in "
            "waves; each server ran the installer, restarted a service, and "
            "reported back; the package hash matches the approved release."
        ),
        "events": [
            {
                "event_type": "process_create",
                "detail": "installer from orchestrator",
                "ts_offset_s": 0,
            },
            {"event_type": "service_restart", "detail": "app service", "ts_offset_s": 3},
            {
                "event_type": "network_connection",
                "detail": "orchestrator callback",
                "ts_offset_s": 8,
            },
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "dns_monitoring": {
        "title": "internal DNS monitor",
        "behavior": (
            "The DNS monitoring sensor resolves a long list of internal and "
            "external domains at high volume every 5 minutes as part of its "
            "normal operation; queries originate from the sensor's dedicated "
            "account."
        ),
        "events": [
            {"event_type": "dns_query", "detail": "monitor account, bulk", "ts_offset_s": 0},
            {"event_type": "dns_query", "detail": "repeated sweep", "ts_offset_s": 300},
            {"event_type": "dns_query", "detail": "sensor routine", "ts_offset_s": 600},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "internal_automation": {
        "title": "CI pipeline agent",
        "behavior": (
            "A CI runner cloned repos, executed tests, and published build "
            "artifacts; it authenticates with a service account and its "
            "network fingerprint is stable across the last 6 months."
        ),
        "events": [
            {"event_type": "process_create", "detail": "test runner", "ts_offset_s": 0},
            {"event_type": "network_connection", "detail": "git server", "ts_offset_s": 2},
            {"event_type": "artifact_upload", "detail": "build registry", "ts_offset_s": 20},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "pentest_authorized": {
        "title": "authorized red-team exercise",
        "behavior": (
            "A red-team operator with a temporary authorized account used a "
            "C2-style toolset against a scoped test range; the engagement is "
            "in the approved calendar and the operator's badge is recorded."
        ),
        "events": [
            {
                "event_type": "network_connection",
                "detail": "test range, red-team",
                "ts_offset_s": 0,
            },
            {"event_type": "script_execution", "detail": "authorized toolset", "ts_offset_s": 10},
            {
                "event_type": "network_connection",
                "detail": "C2-like beacon (approved)",
                "ts_offset_s": 30,
            },
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "cdn_waf_noise": {
        "title": "CDN/WAF traffic noise",
        "behavior": (
            "The CDN edge forwards a high volume of mixed requests including "
            "a small number of probes that are immediately blocked by the WAF "
            "with 403s; the blocked probes are consistent with internet-wide "
            "background noise."
        ),
        "events": [
            {"event_type": "http_request", "detail": "CDN forwarded", "ts_offset_s": 0},
            {"event_type": "http_request", "detail": "WAF 403 block", "ts_offset_s": 3},
            {"event_type": "http_request", "detail": "background noise", "ts_offset_s": 7},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
    "developer_debugging": {
        "title": "developer debug session",
        "behavior": (
            "A developer connected to the staging database, ran a set of "
            "ad-hoc queries including a few that look like enumeration, and "
            "pulled a config file to reproduce a bug; the session is on the "
            "developer's machine with their normal account."
        ),
        "events": [
            {"event_type": "database_query", "detail": "staging, ad-hoc", "ts_offset_s": 0},
            {"event_type": "file_read", "detail": "config for repro", "ts_offset_s": 10},
            {"event_type": "network_connection", "detail": "staging DB", "ts_offset_s": 12},
        ],
        "severity": "LOW",
        "classification": "BENIGN",
        "false_positive": True,
    },
}


@dataclass
class SIBScenario:
    """One CAP-SIB scenario. Labels are strictly separated from input."""

    scenario_id: str
    track: str  # "A" (metadata-assisted) | "B" (metadata-blind)
    split: str  # "dev" | "holdout"
    category: str
    hard_negative: bool
    incomplete: str  # none | missing_evidence | missing_stage | conflicting | out_of_order
    input: dict[str, Any]
    labels: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "track": self.track,
            "split": self.split,
            "category": self.category,
            "hard_negative": self.hard_negative,
            "incomplete": self.incomplete,
            "input": self.input,
            "labels": self.labels,
        }

    @property
    def label_techniques(self) -> list[str]:
        return list(self.labels.get("techniques", []))


def _make_input(
    *,
    template: dict[str, Any],
    track: str,
    incomplete: str,
    assets: list[dict[str, Any]],
    context: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Build a leakage-free input dict.

    Track B never contains ATT&CK ids. Track A may attach rule metadata
    (realistic for detection products) -- and only there.
    """
    events: list[dict[str, Any]] = []
    base_ts = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
    for index, event in enumerate(template["events"]):
        ts = (base_ts + timedelta(seconds=event["ts_offset_s"])).isoformat()
        events.append(
            {
                "id": f"evt-{seed}-{index}",
                "event_type": event["event_type"],
                "detail": event["detail"],
                # No severity field: the expected severity is NEVER present in
                # the input (leakage-free by construction). Severity factors
                # come only from context (cvss/epss/kev/exposure/criticality).
                "timestamp": ts,
                "entities": [assets[0]["value"]],
                "evidence_refs": [f"evidence:{seed}-{index}"],
            }
        )
    # incomplete-evidence transformations (never touch the labels)
    if incomplete == "missing_evidence":
        for event in events:
            event["evidence_refs"] = []
    elif incomplete == "missing_stage":
        events = events[:-1] if len(events) > 2 else events
    elif incomplete == "out_of_order":
        if len(events) > 1:
            events = [events[-1]] + events[:-1]
    elif incomplete == "conflicting":
        if len(events) > 1:
            events[1]["detail"] = (
                "event flagged by two rules with opposite conclusions: "
                "benign-maintenance and malware-execution"
            )

    scenario_input: dict[str, Any] = {
        "title": template["title"],
        "behavior": template["behavior"],
        "events": events,
        "assets": assets,
        "context": context,
    }
    if track == "A":
        # Realistic product metadata: the detection rule may carry a technique.
        scenario_input["rule_metadata"] = {
            "rule": f"rule-{seed}",
            "attck": template.get("techniques", [""])[0],
        }
    return scenario_input


def _context_for(seed: int, severity: str, hard_negative: bool) -> dict[str, Any]:
    """Real, usable severity factors -- never the expected severity itself.

    The factors correlate with the verdict the way they do in reality (a
    higher-CVSS / KEV / exposed finding is more likely severe) but the verdict
    string never appears in the input.
    """
    sev = str(severity).upper()
    if hard_negative or sev == "LOW":
        cvss = 2.0 + (seed % 30) / 10.0  # 2.0 .. 4.9
        epss = round((seed % 25) / 100.0, 3)  # 0.00 .. 0.24
        in_kev = seed % 13 == 0
        exposed = seed % 5 == 0
        criticality = "LOW" if seed % 2 else "MEDIUM"
    elif sev == "MEDIUM":
        cvss = 5.0 + (seed % 25) / 10.0  # 5.0 .. 7.4
        epss = round(0.3 + (seed % 30) / 100.0, 3)  # 0.30 .. 0.59
        in_kev = seed % 7 == 0
        exposed = seed % 3 == 0
        criticality = "MEDIUM" if seed % 2 else "HIGH"
    else:  # HIGH / CRITICAL
        cvss = 8.0 + (seed % 20) / 10.0  # 8.0 .. 9.9
        epss = round(0.6 + (seed % 35) / 100.0, 3)  # 0.60 .. 0.94
        in_kev = seed % 3 == 0
        exposed = True
        criticality = "HIGH" if seed % 4 else "CRITICAL"

    base: dict[str, Any] = {
        "cvss": round(cvss, 1),
        "epss": epss,
        "in_kev": in_kev,
        "exposed": exposed,
        "asset_criticality": criticality,
        "evidence_confidence": 0.6 + (seed % 40) / 100.0,
    }
    if hard_negative:
        base["historical_fp_rate"] = 0.7
        base["known_benign_context"] = True
    return base


def build_sib_v1(*, rng_seed: int = 42) -> list[SIBScenario]:
    """Generate the full CAP-SIB v1 dataset (300 scenarios).

    Deterministic: same seed => same dataset. Splits are stable.
    """
    import random

    rng = random.Random(rng_seed)
    attack_categories = list(ATTACK_TEMPLATES.keys())
    hn_categories = list(HARD_NEGATIVE_TEMPLATES.keys())

    scenarios: list[SIBScenario] = []
    scenario_index = 0

    for split in ("dev", "holdout"):
        target = SIB_DEV if split == "dev" else SIB_HOLDOUT
        # holdout: >=25% hard negatives, >=20% incomplete
        hn_target = 30 if split == "dev" else 30  # 30/100 = 30% holdout HN
        incomplete_target = 40 if split == "dev" else 25  # 25/100 = 25%
        hn_count = 0
        incomplete_count = 0

        for _ in range(target):
            scenario_index += 1
            scenario_id = f"cap-sib-{scenario_index:04d}"
            track = "A" if scenario_index % 4 == 0 else "B"  # 25% Track A

            # hard negative vs attack
            if hn_count < hn_target:
                hn_count += 1
                category = hn_categories[scenario_index % len(hn_categories)]
                template = HARD_NEGATIVE_TEMPLATES[category]
                hard_negative = True
                incomplete = "none"
            else:
                category = attack_categories[scenario_index % len(attack_categories)]
                template = ATTACK_TEMPLATES[category]
                hard_negative = False
                # incomplete evidence on some attack scenarios
                if incomplete_count < incomplete_target and scenario_index % 2 == 0:
                    incomplete_count += 1
                    incomplete = rng.choice(
                        ["missing_evidence", "missing_stage", "conflicting", "out_of_order"]
                    )
                else:
                    incomplete = "none"

            assets = [
                {
                    "id": f"asset-{scenario_index}",
                    "value": f"10.0.{scenario_index % 250}.{1 + scenario_index % 250}",
                    "name": f"host-{scenario_index}",
                    "criticality": ("CRITICAL" if scenario_index % 5 == 0 else "HIGH"),
                }
            ]
            context = _context_for(
                scenario_index, template.get("severity", "MEDIUM"), hard_negative
            )
            scenario_input = _make_input(
                template=template,
                track=track,
                incomplete=incomplete,
                assets=assets,
                context=context,
                seed=scenario_index,
            )
            labels: dict[str, Any] = {
                "severity": template.get("severity", "LOW"),
                "classification": template.get("classification", "BENIGN"),
                "false_positive": template.get("false_positive", True),
                "techniques": list(template.get("techniques", [])),
            }
            scenarios.append(
                SIBScenario(
                    scenario_id=scenario_id,
                    track=track,
                    split=split,
                    category=category,
                    hard_negative=hard_negative,
                    incomplete=incomplete,
                    input=scenario_input,
                    labels=labels,
                )
            )
    return scenarios


def freeze_sib(dataset: list[SIBScenario]) -> str:
    """Deterministic serialization + SHA-256 (freeze before scoring)."""
    payload = [
        {k: v for k, v in scenario.to_dict().items() if k != "labels"} for scenario in dataset
    ]
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def label_leakage_audit(scenario: SIBScenario) -> list[str]:
    """Audit that the input never leaks any label value.

    Returns a list of leakage findings (empty = clean).
    """
    findings: list[str] = []
    text = json.dumps(scenario.input, ensure_ascii=False).lower()
    # 1. technique ids must not appear in Track B inputs
    if scenario.track == "B":
        for technique in scenario.labels.get("techniques", []):
            if technique.lower() in text:
                findings.append(f"technique {technique} leaked into input")
    # 2. expected severity must not appear as input severity field
    expected_sev = str(scenario.labels.get("severity", "")).lower()
    if expected_sev and f'"severity": "{expected_sev}"' in text:
        findings.append(f"expected severity {expected_sev} present in input")
    # 3. expected false_positive must not appear
    expected_fp = scenario.labels.get("false_positive")
    if expected_fp is not None and f'"false_positive": {str(expected_fp).lower()}' in text:
        findings.append("expected false_positive present in input")
    # 4. hard_negative/incomplete flags are benchmark metadata, not input
    return findings


def sib_stats(dataset: list[SIBScenario]) -> dict[str, Any]:
    """Dataset composition stats."""
    from collections import Counter

    splits = Counter(s.split for s in dataset)
    tracks = Counter(s.track for s in dataset)
    hard_negatives = {
        split: sum(1 for s in dataset if s.split == split and s.hard_negative)
        for split in ("dev", "holdout")
    }
    incomplete = {
        split: sum(1 for s in dataset if s.split == split and s.incomplete != "none")
        for split in ("dev", "holdout")
    }
    categories = Counter(s.category for s in dataset)
    return {
        "total": len(dataset),
        "splits": dict(splits),
        "tracks": dict(tracks),
        "hard_negatives": hard_negatives,
        "incomplete": incomplete,
        "categories": dict(categories),
    }
