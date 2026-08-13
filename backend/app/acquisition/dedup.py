"""Phase 28 -- Deduplication (spec 21).

URL canonicalization + content SHA-256 + record keys. Duplicates are NEVER
deleted -- they are marked ``duplicate_of`` the first occurrence. Near-
duplicate detection is an interface reservation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_SKIP_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "spm",
}


def canonicalize_url(url: str) -> str:
    """Lowercase host, strip default ports/fragments/noise params, sort params."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if parsed.port:
        default_port = (scheme == "https" and 443) or (scheme == "http" and 80)
        if parsed.port == default_port:
            host = host  # port removed implicitly by hostname
    path = re.sub(r"/+", "/", parsed.path or "/")
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SKIP_QUERY_PARAMS
    ]
    kept.sort(key=lambda kv: kv[0].lower())
    query = urlencode(kept, doseq=True)
    return urlunparse((scheme, host, path, "", query, ""))


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass
class DuplicateRegistry:
    """Remembers seen URLs + content hashes; marks (never deletes) duplicates.

    Semantics: a duplicate is (a) the same canonical URL returning the same
    content, or (b) the same content seen at a different URL. A URL whose
    content CHANGED (e.g. HTTP shell -> browser-rendered page during a
    replan) is allowed and updates the recorded hash.
    """

    seen_urls: dict[str, str] = field(default_factory=dict)  # canonical -> content hash
    seen_hashes: dict[str, str] = field(default_factory=dict)  # sha256 -> first url
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (url, duplicate_of)

    def check(self, url: str, content_hash: str) -> str | None:
        """Return duplicate_of url if already seen, else record and return None."""
        canonical = canonicalize_url(url)
        if canonical in self.seen_urls:
            if self.seen_urls[canonical] == content_hash:
                first = self.seen_hashes.get(content_hash, url)
                self.duplicates.append((url, first))
                return first
            # content changed at the same URL: allow, update hash below
        elif content_hash in self.seen_hashes:
            first = self.seen_hashes[content_hash]
            self.duplicates.append((url, first))
            return first
        self.seen_urls[canonical] = content_hash
        self.seen_hashes.setdefault(content_hash, url)
        return None

    def reset(self) -> None:
        self.seen_urls.clear()
        self.seen_hashes.clear()
        self.duplicates.clear()
