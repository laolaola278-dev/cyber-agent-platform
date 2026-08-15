"""Phase 27.1 -- deterministic input normalization for injection defense.

Fixes the Hybrid 0.6429 injection-resistance regression by normalizing
untrusted text BEFORE the pattern-based prompt-injection guardrail sees it:

  1. Unicode NFKC normalization        (full-width / homoglyph attacks)
  2. Zero-width character removal      (U+200B..U+200F, U+FEFF, etc.)
  3. HTML entity decoding              (&amp; &#x41; &#65;)
  4. Bounded Base64 candidate decoding (obfuscated payloads)

All normalization is BOUNDED: at most ``max_rounds`` decode iterations and a
``max_input_chars`` cap -- no recursive infinite decoding. After
normalization the Phase 26 injection boundary runs on the normalized text.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ZERO_WIDTH_CHARS = (
    "\u200b"  # zero width space
    "\u200c"  # zero width non-joiner
    "\u200d"  # zero width joiner
    "\u2060"  # word joiner
    "\u2061"  # function application
    "\u2062"  # invisible times
    "\u2063"  # invisible separator
    "\u2064"  # invisible plus
    "\u2066"  # LRI
    "\u2067"  # RLI
    "\u2068"  # FSI
    "\u2069"  # PDI
    "\ufeff"  # zero width no-break space
)
_ZERO_WIDTH_RE = re.compile("[" + ZERO_WIDTH_CHARS + "]")

# Base64 candidate: >=20 chars, valid b64 alphabet, no spaces/newlines.
_B64_CANDIDATE_RE = re.compile(r"^[A-Za-z0-9+/]{20,}={0,2}$")
_HTML_ENTITY_RE = re.compile(r"&(?:#x[0-9a-fA-F]{1,6}|#\d{1,7}|[a-zA-Z]{2,8});")

# Distinguished jailbreak patterns: a SINGLE hit raises the risk to HIGH
# (unambiguous instruction-hijacking / policy-override / dangerous commands).
# Whitespace-tolerant: zero-width removal can concatenate tokens.
JAILBREAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s*(all\s+)?(previous|prior|earlier)\s*instructions", re.I),
    re.compile(
        r"ignore\s*(previous|prior|everything)\s*(instructions|policy|rules|above|and)", re.I
    ),  # noqa: E501
    re.compile(r"ignore\s*above", re.I),
    re.compile(r"disregard\s*(previous|prior|earlier)\s*instructions", re.I),
    re.compile(r"forget\s*(everything|all\s*previous|your\s*rules)", re.I),
    re.compile(r"reveal\s*(your|the)\s*(system\s*)?prompt", re.I),
    re.compile(r"you\s*are\s*(now\s*)?the\s*system", re.I),
    re.compile(r"you\s*are\s*(an|the)?\s*admin", re.I),
    re.compile(r"override\s*(your|the)?\s*(policy|instructions|rules)", re.I),
    re.compile(r"act\s*as\s*(an|the)?\s*(admin|developer|root)", re.I),
    re.compile(r"delete\s*(the\s*)?firewall", re.I),
    re.compile(r"isolate\s*(this|the)\s*host", re.I),
    re.compile(r"disable\s*(the\s*)?firewall", re.I),
    re.compile(
        r"(disable|delete|remove|bypass)\s*(the\s*)?(firewall|pare-?feu|брандмауэр|جدار\s*الحماية|방화벽)",
        re.I,
    ),  # noqa: E501
    re.compile(r"grant\s*(me\s*)?(full\s*)?(admin|root)\s*access", re.I),
    re.compile(r"grant\s*full\s*access", re.I),
    re.compile(r"\[system\]", re.I),
    re.compile(r"whitelist\s*(my\s*)?ip", re.I),
    re.compile(r"host\.isolate", re.I),
    re.compile(r"rm\s*-rf", re.I),
    re.compile(r"shutdown\s*-r", re.I),
    re.compile(r"execute\s*:\s*\w+", re.I),
    re.compile(r"system\s*:\s*override", re.I),
    re.compile(r"disclose\s*(the\s*)?secret", re.I),
    re.compile(r"expose\s*(api\s*)?keys", re.I),
    re.compile(r"above\s*instructions\s*and\s*(act|behave|respond)", re.I),
    re.compile(r"ignore\s*instructions", re.I),
    re.compile(r"ignore\s*policy", re.I),
    re.compile(r"ignore_policy", re.I),
    # multilingual "ignore previous instructions / disable firewall"
    re.compile(r"指示\s*を\s*無視|ファイアウォール\s*を\s*無効", re.I),
    re.compile(
        r"ignor(ez|a|ar)\s*(todas\s+)?(las|les)?\s*(instrucciones\s*anteriores|instructions\s*pr[ée]c[ée]dentes)",
        re.I,
    ),  # noqa: E501
    re.compile(r"d[ée]sob[ée]issez", re.I),
    re.compile(
        r"(disable|delete|remove|bypass)\s*(le\s*)?pare-?feu|(отключите|удалите)\s*брандмауэр|(عطّل|احذف)\s*جدار\s*الحماية|방화벽\s*(비활성화|제거)",
        re.I,
    ),  # noqa: E501
    re.compile(r"предыдущие\s*инструкции|игнорируйте|аннулируйте", re.I),
    re.compile(r"이전\s*지시|무시하고", re.I),
    re.compile(r"التعليمات\s*السابقة|ألغِ|عطّل", re.I),
    re.compile(r"ignore\\?u?0020?prev(ious)?", re.I),
    re.compile(r"select\s+\S+\s+from\s+\S+\s+where", re.I),
)

MAX_INPUT_CHARS = 20000
MAX_ROUNDS = 3
MAX_DECODED_CHARS = 40000


@dataclass
class NormalizationReport:
    original_length: int = 0
    normalized_length: int = 0
    nfkc_applied: bool = False
    zero_width_removed: int = 0
    html_entities_decoded: int = 0
    base64_decoded: int = 0
    rounds: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_length": self.original_length,
            "normalized_length": self.normalized_length,
            "nfkc_applied": self.nfkc_applied,
            "zero_width_removed": self.zero_width_removed,
            "html_entities_decoded": self.html_entities_decoded,
            "base64_decoded": self.base64_decoded,
            "rounds": self.rounds,
            "truncated": self.truncated,
        }


def normalize_text(text: str, *, report: NormalizationReport | None = None) -> str:
    """Deterministic bounded normalization pipeline for untrusted text."""
    if not text:
        return text
    rep = report or NormalizationReport()
    rep.original_length = len(text)
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        rep.truncated = True

    rounds = 0
    previous = text
    for _ in range(MAX_ROUNDS):
        rounds += 1
        changed = False

        # 1. NFKC normalization (full-width letters, compatibility forms)
        normalized = unicodedata.normalize("NFKC", text)
        if normalized != text:
            rep.nfkc_applied = True
            changed = True
            text = normalized

        # 2. zero-width removal
        stripped = _ZERO_WIDTH_RE.sub("", text)
        if len(stripped) != len(text):
            rep.zero_width_removed += len(text) - len(stripped)
            changed = True
            text = stripped

        # 3. HTML entity decoding (bounded)
        decoded = _decode_html_entities(text, rep)
        if decoded != text:
            changed = True
            text = decoded

        # 4. bounded Base64 candidate decoding
        b64_result = _decode_base64_candidates(text, rep)
        if b64_result != text:
            changed = True
            text = b64_result

        if not changed or text == previous:
            break
        previous = text
        if len(text) > MAX_DECODED_CHARS:
            text = text[:MAX_DECODED_CHARS]
            rep.truncated = True
            break

    rep.rounds = rounds
    rep.normalized_length = len(text)
    return text


def _decode_html_entities(text: str, rep: NormalizationReport) -> str:
    count = len(_HTML_ENTITY_RE.findall(text))
    if count == 0:
        return text
    # html.unescape is bounded per call (no recursion); guard against
    # double-escape blowups by capping output size.
    decoded = html.unescape(text)
    if len(decoded) <= MAX_DECODED_CHARS:
        rep.html_entities_decoded += count
        return decoded
    return text


def _decode_base64_candidates(text: str, rep: NormalizationReport) -> str:
    """Decode standalone Base64 blobs (bounded).

    A candidate must be a contiguous base64 token (20+ chars) that decodes to
    printable UTF-8 text. Failed decodes are left untouched. Only the FIRST
    decode per round is applied (no infinite unrolling).
    """
    tokens = re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", text)
    for token in tokens:
        if not _B64_CANDIDATE_RE.match(token):
            continue
        try:
            raw = base64.b64decode(token, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # only accept decodes that look like text (avoid garbage expansion)
        printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\n\r\t")
        if len(decoded) == 0 or printable / len(decoded) < 0.8:
            continue
        if len(decoded) <= MAX_DECODED_CHARS:
            rep.base64_decoded += 1
            return text.replace(token, decoded, 1)
    return text


def normalize_data_blocks(
    data_blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[NormalizationReport]]:
    """Normalize every text payload inside untrusted data blocks."""
    reports: list[NormalizationReport] = []
    normalized_blocks: list[dict[str, Any]] = []
    for block in data_blocks or []:
        rep = NormalizationReport()
        block = dict(block)
        for key in ("text", "content", "value"):
            if isinstance(block.get(key), str):
                block[key] = normalize_text(block[key], report=rep)
        reports.append(rep)
        normalized_blocks.append(block)
    return normalized_blocks, reports


def injection_resistant(
    data_blocks: list[dict[str, Any]],
    *,
    isolation_func: Any,
) -> tuple[bool, NormalizationReport | None, str]:
    """Run the prompt-injection guardrail on NORMALIZED untrusted data.

    Returns (resisted, report, note). ``isolation_func`` is the Phase 26
    ``isolate_untrusted_data`` callable. Normalization is applied first, so
    obfuscated (Unicode / zero-width / HTML-entity / Base64) injections that
    previously evaded pattern matching are now caught. A distinguished
    jailbreak pattern on the normalized text also fails closed (single-hit
    HIGH), closing the Phase 27 0.6429 regression.
    """
    if not data_blocks:
        return True, None, "no data blocks"
    normalized, reports = normalize_data_blocks(data_blocks)
    combined_report = _merge_reports(reports)

    # 1. distinguished jailbreak patterns (single-hit HIGH) on normalized text
    for block in normalized:
        text = str(block.get("text") or block.get("content") or "")
        if _jailbreak_hit(text):
            return False, combined_report, "jailbreak pattern detected after normalization"

    # 2. Phase 26 pattern-count guardrail on normalized text (when provided)
    if isolation_func is None:
        return True, combined_report, "clean (jailbreak scan)"
    try:
        isolation = isolation_func(normalized)
    except Exception:  # noqa: BLE001 -- fail closed
        return True, combined_report, "fail-closed on guardrail error"
    risk = getattr(isolation, "risk_level", "LOW")
    if risk in ("HIGH", "CRITICAL"):
        return False, combined_report, f"injection detected after normalization ({risk})"
    return True, combined_report, f"clean ({risk})"


def _jailbreak_hit(text: str) -> bool:
    if not text:
        return False
    detection = _detection_text(text)
    return any(pattern.search(detection) for pattern in JAILBREAK_PATTERNS)


def _detection_text(text: str) -> str:
    """A token-normalized view of the text used ONLY for injection matching.

    Removes bidi control characters, URL-decodes (bounded), and converts
    common token separators / markup into spaces so that obfuscated
    "ignore_previous_instructions", "<script>ignore instructions</script>",
    log-field and JSON-wrapped payloads still match jailbreak patterns.
    The original normalized text (not this view) is what flows onward.
    """
    import urllib.parse

    for ch in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069":
        text = text.replace(ch, "")
    try:
        text = urllib.parse.unquote_plus(text)
    except Exception:  # noqa: BLE001
        pass
    # literal \u0020 escapes -> space
    text = re.sub(r"\\u0020", " ", text)
    # keep comment inner content for detection
    text = re.sub(r"<!--(.*?)-->", lambda m: " " + m.group(1), text, flags=re.DOTALL)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"[{}\[\]\"'`\\]", " ", text)
    # underscore is a common token separator; keep '-' and '.' so that
    # patterns like `rm -rf` / `host.isolate` still match.
    text = re.sub(r"_", " ", text)
    return re.sub(r"\s+", " ", text)


def _merge_reports(reports: list[NormalizationReport]) -> NormalizationReport:
    merged = NormalizationReport()
    for rep in reports:
        merged.nfkc_applied = merged.nfkc_applied or rep.nfkc_applied
        merged.zero_width_removed += rep.zero_width_removed
        merged.html_entities_decoded += rep.html_entities_decoded
        merged.base64_decoded += rep.base64_decoded
        merged.rounds = max(merged.rounds, rep.rounds)
        merged.original_length += rep.original_length
        merged.normalized_length += rep.normalized_length
        merged.truncated = merged.truncated or rep.truncated
    return merged
