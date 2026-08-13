"""Prompt Injection safety analysis (v2.0 / Phase 25, Critical Requirement).

Untrusted content (web pages, logs, evidence, files, tool output) is never
instructions. This module:

1. classifies untrusted data blocks and isolates them from instructions;
2. detects prompt-injection patterns with a confidence verdict;
3. provides the rule set reused by input/plan/output guardrails.

Design rule: if analysis returns HIGH risk, the caller must fail closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore (all )?(previous|prior|above) (instructions?|prompts?|rules?)", re.I),
    re.compile(r"disregard (all )?(previous|prior) (instructions?|prompts?)", re.I),
    re.compile(r"skip (all )?instructions", re.I),
    re.compile(r"you are now (a |an )?", re.I),
    re.compile(r"act as (a |an )?(system|developer|admin)", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"developer instruction", re.I),
    re.compile(r"disable (the )?(firewall|waf|edr|security)", re.I),
    re.compile(r"delete (the )?(firewall|waf|rules?|policy)", re.I),
    re.compile(r"turn off (the )?security", re.I),
    re.compile(r"unban|whitelist me|escalate (my )?privileges", re.I),
    re.compile(r"reveal (your|the) (secret|password|api key|token)", re.I),
    re.compile(r"forget (your|all) (rules|instructions|guidelines)", re.I),
    re.compile(r"expose (your|the) (system )?prompt", re.I),
    re.compile(r"忽略(之前|以上|前面)(所有)?(指令|提示|规则)", re.I),
    re.compile(r"你现在(是|扮演)", re.I),
    re.compile(r"解除|关闭|禁用(防火墙|防护|安全|waf|edr)", re.I),
    re.compile(r"输出(你的|系统)提示词", re.I),
    re.compile(r"绕过(审批|权限|安全)", re.I),
    re.compile(r"泄露(密码|密钥|令牌|token)", re.I),
)

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?i)\b(secret|password|passwd|token)\s*[=:]\s*[^\s,;]{6,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*\S+"),
)

DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rm\s+-rf\s+/|del\s+/[sq]", re.I),
    re.compile(r"(^|[;&|])\s*(os\.system|subprocess|eval\(|exec\(|__import__)", re.I),
    re.compile(r"\bDROP (TABLE|DATABASE|SCHEMA)\b", re.I),
    re.compile(r"\bDELETE FROM\b|\bUNION\s+SELECT\b", re.I),
    re.compile(r"\b(nmap|sqlmap|nikto|msfconsole|aircrack)\b", re.I),
    re.compile(r"\bcurl\b|\bwget\b", re.I),
    re.compile(r"\bnuclei\s+-[a-z]|\bzap\s+-[a-z]|\bsuricata\s+-[a-z]|\bzeek\s+-[a-z]", re.I),
    re.compile(r"\$(\(|`|`[a-z]{1,64}`)", re.I),
    re.compile(r"(?i)\bhost\.isolate\b|response\.(waf|firewall|edr)\b"),
)

TOOL_OUTPUT_MARKERS: tuple[str, ...] = (
    "ignore",
    "instructions",
    "remember",
    "important:",
    "note:",
)


@dataclass(frozen=True, slots=True)
class InjectionAnalysis:
    """Result of analyzing one untrusted content block."""

    source: str
    risk: str  # HIGH | MEDIUM | LOW
    matched_patterns: tuple[str, ...] = field(default_factory=tuple)
    verdict: str = "allow"

    @property
    def fail_closed(self) -> bool:
        return self.risk == "HIGH"


def analyze_injection(content: str, *, source: str = "untrusted") -> InjectionAnalysis:
    """Scan one untrusted block for prompt-injection indicators."""
    if not content:
        return InjectionAnalysis(source=source, risk="LOW", verdict="allow")
    hits: list[str] = []
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(content)
        if match:
            hits.append(pattern.pattern)
    risk = "LOW"
    if len(hits) >= 2:
        risk = "HIGH"
    elif len(hits) == 1:
        risk = "MEDIUM"
    verdict = "reject" if risk == "HIGH" else "allow"
    return InjectionAnalysis(
        source=source,
        risk=risk,
        matched_patterns=tuple(hits),
        verdict=verdict,
    )


def analyze_secret_exposure(content: str) -> tuple[str, ...]:
    """Return secret patterns found in content (empty when none)."""
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            hits.append(pattern.pattern)
    return tuple(hits)


def analyze_dangerous_commands(content: str) -> tuple[str, ...]:
    """Return dangerous command patterns found in content (empty when none)."""
    hits: list[str] = []
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(content):
            hits.append(pattern.pattern)
    return tuple(hits)


@dataclass(frozen=True, slots=True)
class IsolationResult:
    """Fenced untrusted data plus per-block injection analyses."""

    fenced_text: str
    analyses: tuple[InjectionAnalysis, ...] = ()

    @property
    def fail_closed(self) -> bool:
        return any(analysis.fail_closed for analysis in self.analyses)

    @property
    def risk_level(self) -> str:
        if self.fail_closed:
            return "HIGH"
        if any(analysis.risk == "MEDIUM" for analysis in self.analyses):
            return "MEDIUM"
        return "LOW"


def isolate_untrusted_data(blocks: list[dict[str, Any]]) -> IsolationResult:
    """Wrap every untrusted block in a data fence and analyze it.

    Returns an ``IsolationResult`` whose fenced text is appended to the *user*
    prompt only, never to the system prompt. High-risk blocks set
    ``fail_closed`` so callers must reject the request.
    """
    analyses: list[InjectionAnalysis] = []
    fragments: list[str] = []
    for index, block in enumerate(blocks):
        source = str(block.get("source", f"block-{index}"))
        text = str(block.get("text", ""))
        analysis = analyze_injection(text, source=source)
        analyses.append(analysis)
        fragments.append(
            f"<untrusted-data source={source!r} risk={analysis.risk}>"
            f"{text}</untrusted-data>"
        )
    return IsolationResult(fenced_text="\n".join(fragments), analyses=tuple(analyses))
