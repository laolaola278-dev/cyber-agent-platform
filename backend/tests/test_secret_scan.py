"""Tests for ``scripts/quality/scan_secrets.py`` -- the credential gate.

A scanner has two failure modes, and a gate that only demonstrates one of them is
theatre: it can cry wolf (and then get switched off, which is exactly what
happened to earlier attempts at this check on other lines of work), or it can
sleep through a real key. Both directions are asserted here, and the interesting
case is the last one: the path allow-list that lets test fixtures quote a fake
password must NOT be able to hide a provider-shaped token in the same file.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "scan_secrets", _REPO_ROOT / "scripts" / "quality" / "scan_secrets.py"
)
scanner = importlib.util.module_from_spec(_SPEC)
sys.modules["scan_secrets"] = scanner
_SPEC.loader.exec_module(scanner)


# -- the tree we ship is clean ------------------------------------------------


def test_tracked_tree_has_no_credentials() -> None:
    """The real gate, executed as part of the suite rather than as a promise."""
    files = scanner._tracked_files(_REPO_ROOT)
    assert len(files) > 500, f"only {len(files)} tracked files -- wrong root?"
    findings: list[scanner.Finding] = []
    for rel in files:
        path = _REPO_ROOT / rel
        if path.suffix.lower() in scanner.BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scanner.scan_text(rel.replace("\\", "/"), text))
    reported = [f"{f.path}:{f.line} [{f.rule}] {f.excerpt}" for f in findings]
    assert not reported, "tracked files carry credentials:\n  " + "\n  ".join(reported)


def test_environment_file_is_ignored_not_tracked() -> None:
    """`.env` is where operators put real secrets; git must never carry it."""
    tracked = scanner._tracked_files(_REPO_ROOT)
    assert ".env" not in tracked, "a committed .env is a leak by construction"
    proc = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=str(_REPO_ROOT), capture_output=True
    )
    assert proc.returncode == 0, ".env is not git-ignored: any `git add .` ships it"


# -- it must find what it promises -------------------------------------------

#: Assembled from fragments on purpose: this file is the gate's own fixture, and a
#: scanner that had to allow-list it could not scan the test suite -- which is
#: exactly the hole an audit looks for.
GITHUB_TOKEN = "ghp_" + "AbCdEf1234567890" * 2 + "AbCd"
AWS_KEY_ID = "AKIA" + "IOSQODHSM7TGBXYP"
PEM_BODY = "MIIEowIBAAKCAQEAwq5in9HlTpTgc0vTghncZbLbnjxJCPvTFWZq" + "xkGmnbLMWqjR"
HIGH_ENTROPY_SECRET = "k3v9X2mZq8TlpR7yBs4Nc6Hd1" + "Gt0Fj5awu"
URL_PASSWORD = "Xk3vT9mZqLpR8yBs" + "7Nc4Hd"

PLANTED = [
    ("github_token", 'GITHUB_TOKEN = "' + GITHUB_TOKEN + '"\n'),
    ("aws_access_key_id", "aws_key_id: " + AWS_KEY_ID + "\n"),
    (
        "private_key_block",
        "-----BEGIN RSA PRIVATE KEY-----\n" + PEM_BODY + "\n"
        "-----END RSA PRIVATE KEY-----\n",
    ),
    (
        "high_entropy_secretish",
        'OBJECT_STORE_SECRET_KEY = "' + HIGH_ENTROPY_SECRET + '"\n',
    ),
    (
        "basic_auth_url",
        "url: postgresql+asyncpg://cap:" + URL_PASSWORD + "@db.internal:5432/cap\n",
    ),
]


@pytest.mark.parametrize("rule, payload", PLANTED, ids=lambda v: v if isinstance(v, str) else "")
def test_scanner_detects_a_planted_credential(rule: str, payload: str) -> None:
    findings = scanner.scan_text("deploy/planted.yaml", payload)
    matched = [f for f in findings if f.rule == rule]
    assert matched, f"{rule} did not fire; findings were {[(f.rule, f.value) for f in findings]}"


def test_scanner_reports_the_line_where_a_key_sits() -> None:
    payload = "header\n" + PLANTED[2][1]
    findings = [
        f for f in scanner.scan_text("deploy/x.pem", payload) if f.rule == "private_key_block"
    ]
    assert findings and findings[0].line == 2, [(f.line, f.value) for f in findings]


# -- and must not cry wolf ----------------------------------------------------

BENIGN = [
    "api_key = zap_api_key\n",
    "self._client = ZAPv2(apikey=self._api_key, proxies=proxies)\n",
    "model_id, api_key = load_real_credentials()\n",
    "secret: SecretManifestSpec = Field(default_factory=SecretManifestSpec)\n",
    'SECRET_KEY: "${SECRET_KEY:?required}"\n',
    "CAP_ZAP_API_KEY: ${CAP_ZAP_API_KEY:-}\n",
    "secret: {provider: memory, references: [zap-api-key]}\n",
    "RBAC_TRUSTED_PROXY_SECRET: change-me-proxy-secret\n",
    'password = "REPLACE_ME_IN_YOUR_DEPLOYMENT"\n',
    "image: quay.io/minio/minio@sha256:" + "a" * 64 + "\n",
    "sha256: 01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891\n",
]


@pytest.mark.parametrize("payload", BENIGN)
def test_scanner_ignores_references_placeholders_and_digests(payload: str) -> None:
    findings = scanner.scan_text("backend/app/example.py", payload)
    assert not findings, f"false positive on {payload!r}: {[(f.rule, f.value) for f in findings]}"


# -- the allow-list cannot be used to bury a real leak ------------------------


def test_path_allow_list_cannot_hide_a_provider_shaped_token() -> None:
    """The exceptions are scoped to the weak rules, and the reason is recorded.

    `tests/` and `docs/quality/` may quote a fake password (they test redaction
    and they report findings that were closed). Neither may hold a GitHub token:
    that shape is a finding wherever the file lives.
    """
    token = 'GITHUB_TOKEN = "' + GITHUB_TOKEN + '"\n'
    for path in ("backend/tests/test_x.py", "docs/quality/report.md", "scripts/x.sh"):
        rules = {f.rule for f in scanner.scan_text(path, token)}
        assert "github_token" in rules, f"the allow-list swallowed a token in {path}"


def test_every_allow_list_entry_states_why() -> None:
    for fragment, rule, reason in scanner.ALLOW:
        assert fragment and rule, (fragment, rule)
        assert len(reason) > 20, f"allow-list entry {fragment!r} has no real justification"
