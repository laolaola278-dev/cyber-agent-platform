"""Scan the tracked tree for credentials that must never be committed.

Part of the release certification contract (§20 of the readiness checklist): the
source delivered with a release has to be free of live secrets, and the scan has
to be *executed*, not asserted. It is stdlib-only and exits non-zero on a finding
so it can gate CI the same way ``assert_coverage_matrix.py`` does -- the CI jobs
that install third-party tools have been the fragile ones on this line (a retired
vendor download turned into an HTML error page installed as an executable), so
the gate that matters most is the one with no dependencies.

What it looks for:

  * provider-shaped tokens (AWS access key ids, GitHub tokens, Slack, Stripe,
    Google API keys, private keys, JWTs, Ansible/Vault password assignments);
  * high-entropy opaque strings *assigned* to a key whose name says secret, token,
    password, api-key or credential -- the shape a real credential takes;
  * an environment file that git is tracking (``.env`` with real values in it is
    the single most common way this repo could leak a production credential).

Deliberate non-findings are listed in ``ALLOW`` with a reason, because a scanner
nobody can silence gets deleted rather than fixed.

Usage:
    python scripts/quality/scan_secrets.py [--json OUT] [PATH ...]

With no PATHs it scans the files git tracks in the current repository.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# -- rules --------------------------------------------------------------------

#: name -> compiled pattern; every match is a finding unless ALLOW excuses it.
PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b|\bgithub_pat_[0-9A-Za-z_]{40,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "stripe_key": re.compile(r"\b[spr]k_[0-9A-Za-z]{24,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(r"\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\b"),
    "openai_key": re.compile(r"\bsk-[0-9A-Za-z]{32,}\b"),
    "huggingface_token": re.compile(r"\bhf_[0-9A-Za-z]{34}\b"),
    # The password group, not the whole URL: `postgresql+asyncpg://cap:ci-postgres`
    # -password@postgres:5432/cap` is a CI fixture and only the middle is a secret.
    "basic_auth_url": re.compile(
        r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:(?P<password>[^/@\s]{6,})@"
    ),
    "password_assignment": re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|credentials?)\b\s*[:=]\s*[\"']?(?![\"']?\s*$)([^\s\"';,)]{9,})"
    ),
}

#: Which capture group holds the credential, for the rules that capture more than
#: one thing. Group 0 means "the whole match is the token".
_VALUE_GROUP: dict[str, int] = {"password_assignment": 2, "basic_auth_url": 1}

#: Rules that need more than one line. A PEM key is a header line followed by
#: base64 body lines, so a per-line scan of the header alone would flag every
#: redaction test that quotes `-----BEGIN RSA PRIVATE KEY-----` and still miss
#: nothing -- requiring the body keeps it strong *and* quiet.
MULTILINE_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key_block": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{40,}"
    ),
}

#: An assignment to a secret-shaped name. Requiring the `=`/`:` keeps a prose line
#: or a filename that merely contains "secrets" out of the entropy hunt: the first
#: version matched `--ignore=backend/tests/...linux_secrets.py` and a table of
#: column names, which is how a scanner loses its audience.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Za-z0-9_]*(?:secret|token|passwd|password|api[_-]?key|access[_-]?key|"
    r"credential|private[_-]?key)[A-Za-z0-9_]*\b\s*[=:]\s*['\"]?(?P<value>[A-Za-z0-9+/_=-]{24,96})"
)

#: Placeholder values that are the application's own documented defaults, or
#: fixtures the test suite asserts on. Committing these is intentional; a scanner
#: that reports them will be switched off instead of corrected.
PLACEHOLDER_VALUES = {
    "change-me",
    "change-me-too",
    "change-me-proxy-secret",
    "capadmin123",
    "capminio-secret",
    "ci-postgres-password",
    "ci-secret-key-at-least-32-characters",
    "ci-jwt-secret-at-least-32-characters",
    "ci-proxy-secret-at-least-32-characters",
    "ci-grafana-password",
    "ci-pgadmin-password",
    "test-zap-api-key",
    "capminio",
    "minioadmin",
    "capadmin",
    "hunter2",
    "sk-abcdef1234567890",
    "hunter2secret",
    "probe-zap-credential-value",
    "0f1e2d3c4b5a6e7f8a9b0c1d2e3f4a5b",
    "changeme",
    "password",
    "postgres",
    "secret",
    "replace-me",
}

#: A bare identifier, attribute or call: `api_key=zap_api_key`,
#: `apikey=self._api_key`, `api_key = load_real_credentials()`. Naming where a
#: credential comes from is the opposite of embedding one -- a pydantic field
#: annotated `secret: SecretManifestSpec = Field(...)` is a type. Identifier shape
#: (valid Python, no digits mixed into high entropy) is not what a leaked key looks
#: like, and the provider-shaped rules plus the entropy rule stay armed for those.
#: Deliberately digit-free: `ghp_AbCd123...` and a random 34-character key are
#: identifier-shaped too, and an exception that matches those is not an
#: exception, it is an off switch. Real references never carry digits.
_CODE_REFERENCE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z_]*\.)*[A-Za-z_][A-Za-z_]*(?:\.[A-Za-z_][A-Za-z_]*)*(?:\([^)]*)?$"
)

#: Fixture-shaped values: the certification clusters and CI jobs name their
#: throwaway credentials after the environment they belong to, which is exactly
#: what a *real* leaked credential does not look like. Anchored, and combined with
#: the secret-shaped word requirement, so it cannot cover a genuine token.
_FIXTURE_VALUE = re.compile(
#: the secret-shaped word requirement, so it cannot cover a genuine token.
    r"^(?:cap|ci|test|probe|dev|local|cert|kind|example|fake|e2e|smoke)[a-z0-9]*"
    r"[-_].*(?:secret|key|password|token|credential|pass|pwd)",
    re.IGNORECASE,
)

#: (path fragment, rule) pairs excused with the reason recorded in the report, so
#: the exception is visible rather than a silent skip.
#: Weak rules only: provider-shaped tokens (a GitHub token, a PEM block, an AWS
#: key id) are findings *wherever* they appear, including tests, so this cannot be
#: used to hide a real leak behind a filename.
ALLOW: list[tuple[str, str, str]] = [
    ("scripts/quality/scan_secrets.py", "*", "the scanner contains the patterns it hunts"),
    (
        "docs/quality/",
        "password_assignment",
        "audit reports quote the finding they closed",
    ),
    (
        "docs/quality/",
        "high_entropy_secretish",
        "audit reports quote the finding they closed",
    ),
    ("_test", "password_assignment", "unit test asserting redaction of a fake value"),
    ("tests/", "password_assignment", "suite fixtures use documented default credentials"),
    ("tests/", "high_entropy_secretish", "suite fixtures use documented default credentials"),
    ("_test", "high_entropy_secretish", "unit test asserting redaction of a fake value"),
    ("docs/", "high_entropy_secretish", "documentation prose, not a credential assignment"),
    ("backend/docs/", "high_entropy_secretish", "certification manuals quote the cert-cluster fixtures"),
]

#: Files whose *content* is a hash by design (digest pinning, lockfiles).
DIGEST_CONTEXT = re.compile(
    r"(?i)(sha256|sha512|digest|integrity|checksum|content_hash|revision|commit)"
)

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".gz", ".tgz", ".xz", ".so", ".dll", ".exe", ".pyd", ".pyc",
    ".whl", ".jar", ".class", ".o", ".a", ".bin", ".mp4", ".webp",
}


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    value: str
    excerpt: str


def entropy(token: str) -> float:
    counts: dict[str, int] = {}
    for char in token:
        counts[char] = counts.get(char, 0) + 1
    total = len(token)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _allow_reason(path: str, rule: str) -> str | None:
    for fragment, allowed_rule, reason in ALLOW:
        if fragment in path.replace("\\", "/") and allowed_rule in (rule, "*"):
            return reason
    return None


def _tracked_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, check=True
    )
    return sorted(name for name in proc.stdout.decode("utf-8").split("\0") if name)


def _read(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return handle.read()
    except (UnicodeDecodeError, OSError):
        return None


def _placeholder(value: str) -> bool:
    cleaned = value.strip("\"'`,;")
    lowered = cleaned.lower()
    if lowered in PLACEHOLDER_VALUES:
        return True
    # Templated, interpolated or structured values cannot be live secrets: a
    # plugin manifest declares `secret: {provider: memory, references: []}`, which
    # is a reference to a credential, not one.
    if cleaned[:1] in {"{", "[", "("}:
        return True
    if any(marker in cleaned for marker in ("${", "{{", "os.environ", "getenv", "<", "REPLACE")):
        return True
    if _FIXTURE_VALUE.match(cleaned) or _CODE_REFERENCE.match(cleaned):
        return True
    # The documented "give me a real one" style placeholders.
    return bool(re.search(r"(?i)example|placeholder|dummy|fake|sample|xxx|yyy|zzz", cleaned))


def scan_text(rel_path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                # Which group holds the credential is declared per rule (see
                # _VALUE_GROUP): `lastindex` reports the prefix alternative for
                # `(AKIA|ASIA)[0-9A-Z]{16}`, and judging a key by its four leading
                # characters would let the placeholder list swallow real ones.
                value = match.group(_VALUE_GROUP.get(rule, 0))
                if _allow(rel_path, rule):
                    continue
                if _placeholder(value):
                    continue
                if rule == "password_assignment" and DIGEST_CONTEXT.search(line):
                    continue
                findings.append(
                    Finding(rel_path, lineno, rule, value[:80], line.strip()[:160])
                )
        if _allow(rel_path, "high_entropy_secretish"):
            continue
        for match in _SECRET_ASSIGNMENT.finditer(line):
            token = match.group("value")
            if _placeholder(token) or DIGEST_CONTEXT.search(line):
                continue
            if entropy(token) >= 4.0 and len(token) >= 24:
                findings.append(
                    Finding(
                        rel_path, lineno, "high_entropy_secretish", token[:80], line.strip()[:160]
                    )
                )
    for rule, pattern in MULTILINE_PATTERNS.items():
        if _allow(rel_path, rule):
            continue
        for match in pattern.finditer(text):
            header_line = text.count("\n", 0, match.start())
            body = text.splitlines()[header_line : header_line + 1]
            findings.append(
                Finding(
                    rel_path,
                    header_line + 1,
                    rule,
                    match.group(0)[:60].replace("\n", "\\n"),
                    (body[0] if body else "")[:160],
                )
            )
    return findings


_ALLOWED_CACHE: dict[tuple[str, str], bool] = {}


def _allow(path: str, rule: str) -> bool:
    key = (path, rule)
    if key not in _ALLOWED_CACHE:
        _ALLOWED_CACHE[key] = _allow_reason(path, rule) is not None
    return _ALLOWED_CACHE[key]


def check_tracked_env_file(root: Path) -> list[Finding]:
    """A tracked .env is a finding even if it currently holds no real secret.

    The file is where operators put credentials; if git tracks it, the next
    `git add` ships them.
    """
    tracked = [name for name in _tracked_files(root) if Path(name).name in {".env", ".env.local"}]
    return [Finding(name, 0, "tracked_env_file", "", "environment file is under version control")
            for name in tracked]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="*", help="files to scan (default: git-tracked files)")
    parser.add_argument("--json", dest="json_out", type=Path, help="write machine-readable results")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv[1:])

    root = args.root.resolve()
    findings: list[Finding] = []
    scanned = 0
    targets = args.paths or _tracked_files(root)
    for rel in targets:
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:  # a path outside the repo: refuse rather than read it
            findings.append(Finding(rel, 0, "path_outside_repository", "", "--"))
            continue
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        text = _read(path)
        if text is None:
            continue
        scanned += 1
        findings.extend(scan_text(rel.replace("\\", "/"), text))
    if not args.paths:
        findings.extend(check_tracked_env_file(root))

    payload = {
        "schema": "cap.secret-scan/v1",
        "root": str(root),
        "files_scanned": scanned,
        "findings": [finding.__dict__ for finding in findings],
        "verdict": "FAIL" if findings else "PASS",
        "allow_list": [{"fragment": f, "rule": r, "reason": why} for f, r, why in ALLOW],
        "placeholders_allowed": sorted(PLACEHOLDER_VALUES),
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for finding in findings:
        print(f"FINDING {finding.path}:{finding.line} [{finding.rule}] {finding.excerpt}")
    print(
        f"secret scan: {payload['verdict']} ({scanned} files scanned, {len(findings)} findings)"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
