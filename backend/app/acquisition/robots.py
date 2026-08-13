"""Phase 28 -- Robots policy (spec 18).

Public acquisition respects robots.txt by default. A robots.txt fetch is
itself an acquisition (goes through the HTTP adapter + URL validator); if it
cannot be fetched or has no matching rule, the default is ALLOWED but the
source URL is recorded so governance can review.

This is NOT a bypass mechanism: DISALLOWED always stops the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.acquisition.models import RobotsPolicyResult

_USER_AGENT_TOKENS = ("*", "cap-adaptiveacquisition", "cap", "adaptiveacquisition")

_DIRECTIVE_RE = re.compile(r"^\s*([A-Za-z-]+)\s*:\s*(.*?)\s*$")


@dataclass
class _RuleGroup:
    agents: list[str]
    disallows: list[str]
    allows: list[str]
    crawl_delay: float | None = None


def _parse_robots(text: str) -> list[_RuleGroup]:
    groups: list[_RuleGroup] = []
    current: _RuleGroup | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DIRECTIVE_RE.match(line)
        if not match:
            continue
        directive, value = match.group(1).lower(), match.group(2)
        if directive == "user-agent":
            current = _RuleGroup(agents=[], disallows=[], allows=[])
            current.agents.append(value.lower())
            groups.append(current)
        elif directive == "allow":
            if current is not None:
                current.allows.append(value)
        elif directive == "disallow":
            if current is not None:
                current.disallows.append(value)
        elif directive == "crawl-delay":
            if current is not None:
                try:
                    current.crawl_delay = float(value)
                except ValueError:
                    pass
    return groups


def _path_allowed(path: str, group: _RuleGroup) -> bool | None:
    """None = no rule matched; True/False otherwise (longest match wins)."""
    best_len = -1
    best_allow: bool | None = None
    for pattern in group.disallows:
        if pattern and path.startswith(pattern):
            if len(pattern) > best_len:
                best_len = len(pattern)
                best_allow = False
    for pattern in group.allows:
        if pattern and path.startswith(pattern):
            if len(pattern) > best_len:
                best_len = len(pattern)
                best_allow = True
    return best_allow


class RobotsPolicy:
    """Evaluate robots.txt rules for a URL (public-only compliance)."""

    def __init__(self, user_agent_tokens: tuple[str, ...] = _USER_AGENT_TOKENS) -> None:
        self._tokens = user_agent_tokens

    def evaluate(self, url: str, robots_text: str | None) -> RobotsPolicyResult:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if robots_text is None:
            return RobotsPolicyResult(
                allowed=True,
                reason="no robots.txt (governance: unreachable source)",
                source_url=url,
            )
        groups = _parse_robots(robots_text)
        matched_group: _RuleGroup | None = None
        for group in groups:
            if any(agent in self._tokens for agent in group.agents):
                matched_group = group
                break
        if matched_group is None:
            return RobotsPolicyResult(
                allowed=True,
                reason="no matching user-agent group (wildcard absent)",
                source_url=url,
            )
        verdict = _path_allowed(path, matched_group)
        if verdict is False:
            return RobotsPolicyResult(
                allowed=False,
                reason=f"robots.txt DISALLOW rule matched path {path!r}",
                source_url=url,
                rule="disallow",
            )
        return RobotsPolicyResult(
            allowed=True,
            reason=f"robots.txt allow (path {path!r})",
            source_url=url,
            rule="allow",
        )


def robots_url_for(url: str) -> str:
    """robots.txt lives at the origin root of the URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"
