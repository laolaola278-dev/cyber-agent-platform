"""Phase 27 -- ATT&CK technique mapping.

AttackTechniqueCandidateGenerator produces 0..N technique candidates from
platform facts + knowledge, deterministically. The HybridATTCMapper ranks
candidates with a deterministic score and -- when an LLM is available -- lets
the LLM re-rank and explain. The LLM can never invent a technique that is not
in the candidate set; no candidates => UNKNOWN (never a guess).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.hybrid.facts import SecurityFact
from app.hybrid.retrieval import KnowledgeRetriever

# Deterministic rule: fact text -> technique candidates (fallback keyword map).
_KEYWORD_TECHNIQUES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("phish", "mail", "attachment", "link", "social engineer"), "T1566"),
    (("powershell", "cmd", "script", "command", "shell", "exec"), "T1059"),
    (("credential", "password", "hash", "dump", "lsass", "keylog"), "T1003"),
    (("lateral", "remote", "wmi", "psexec", "smb", "rdp"), "T1021"),
    (("c2", "command and control", "beacon", "callback", "http", "dns"), "T1071"),
    (("persistence", "registry run", "scheduled task", "startup"), "T1547"),
    (("privilege", "uac", "token", "escalat"), "T1068"),
    (("exfil", "data transfer", "upload", "archive", "staging"), "T1567"),
    (("discovery", "recon", "enumerat", "netstat", "whoami"), "T1082"),
    (("malware", "trojan", "backdoor", "implant", "ransomware"), "T1204"),
    (("ioc", "indicator", "signature", "detection"), "T1083"),
)

# ATT&CK technique metadata (minimal deterministic knowledge for offline eval).
TECHNIQUE_CATALOG: dict[str, dict[str, str]] = {
    "T1566": {"name": "Phishing", "tactic": "Initial Access"},
    "T1059": {"name": "Command and Scripting Interpreter", "tactic": "Execution"},
    "T1053": {"name": "Scheduled Task/Job", "tactic": "Execution"},
    "T1003": {"name": "OS Credential Dumping", "tactic": "Credential Access"},
    "T1021": {"name": "Remote Services", "tactic": "Lateral Movement"},
    "T1071": {"name": "Application Layer Protocol", "tactic": "Command and Control"},
    "T1547": {"name": "Boot or Logon Autostart Execution", "tactic": "Persistence"},
    "T1068": {"name": "Exploitation for Privilege Escalation", "tactic": "Privilege Escalation"},
    "T1567": {"name": "Exfiltration Over Web Service", "tactic": "Exfiltration"},
    "T1082": {"name": "System Information Discovery", "tactic": "Discovery"},
    "T1204": {"name": "User Execution", "tactic": "Execution"},
    "T1083": {"name": "File and Directory Discovery", "tactic": "Discovery"},
    "T1078": {"name": "Valid Accounts", "tactic": "Defense Evasion"},
    "T1219": {"name": "Remote Access Software", "tactic": "Command and Control"},
    "T1498": {"name": "Network Denial of Service", "tactic": "Impact"},
    "T1027": {"name": "Obfuscated Files or Information", "tactic": "Defense Evasion"},
    "T1568": {"name": "Dynamic Resolution", "tactic": "Command and Control"},
}


@dataclass
class TechniqueCandidate:
    technique_id: str
    score: float
    source: str  # "knowledge" | "rule" | "event"
    supporting_evidence: list[str] = field(default_factory=list)


@dataclass
class TechniqueMapping:
    technique_id: str | None
    score: float
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)
    explanation: str = ""
    candidates: list[TechniqueCandidate] = field(default_factory=list)
    mapped_techniques: list[str] = field(default_factory=list)
    unknown: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "technique_id": self.technique_id,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "supporting_evidence": self.supporting_evidence,
            "explanation": self.explanation,
            "unknown": self.unknown,
            "mapped_techniques": self.mapped_techniques,
            "candidate_count": len(self.candidates),
        }


class AttackTechniqueCandidateGenerator:
    """Deterministic generator: candidates always come from knowledge + rules.

    Inputs: SecurityFacts, event metadata, retrieved knowledge. The LLM never
    runs inside this class.
    """

    def __init__(self, *, knowledge: KnowledgeRetriever | None = None) -> None:
        self._knowledge = knowledge

    def from_event_techniques(
        self,
        techniques: list[str],
        *,
        evidence: list[str] | None = None,
    ) -> list[TechniqueCandidate]:
        """Techniques declared on an event/finding (platform data).

        A declared technique is only accepted when it exists in the knowledge
        catalog; unknown declared IDs are rejected (never trusted blindly).
        """
        candidates: list[TechniqueCandidate] = []
        evidence = evidence or []
        for technique in techniques or []:
            technique_id = str(technique).strip().upper()
            if not re.fullmatch(r"T\d{4}(\.\d{3})?", technique_id):
                continue
            if technique_id not in TECHNIQUE_CATALOG:
                continue
            candidates.append(
                TechniqueCandidate(
                    technique_id=technique_id,
                    score=1.0,
                    source="event",
                    supporting_evidence=evidence[:1],
                )
            )
        return candidates

    def from_keywords(self, text: str, *, evidence: list[str]) -> list[TechniqueCandidate]:
        """Keyword-rule fallback over concatenated fact text."""
        candidates: list[TechniqueCandidate] = []
        lowered = text.lower()
        for keywords, technique_id in _KEYWORD_TECHNIQUES:
            if any(keyword in lowered for keyword in keywords):
                candidates.append(
                    TechniqueCandidate(
                        technique_id=technique_id,
                        score=0.6,
                        source="rule",
                        supporting_evidence=evidence[:1],
                    )
                )
        return candidates

    async def from_knowledge(self, fact: SecurityFact) -> list[TechniqueCandidate]:
        """Look up ATT&CK knowledge for a fact; knowledge hits score highest.

        For behavior/indicator facts we query the ATT&CK catalog directly with
        the fact value as the query so that behavior vocabulary matches
        technique keywords.
        """
        if self._knowledge is None:
            return []
        if fact.fact_type in ("technique", "vulnerability"):
            hits = await self._knowledge.lookup_fact(fact, limit=5)
        else:
            hits = await self._knowledge.lookup(knowledge_type="ATT&CK", query=fact.value, limit=5)
        candidates: list[TechniqueCandidate] = []
        for hit in hits:
            if hit.knowledge_type.upper() != "ATT&CK":
                continue
            technique_id = str(hit.external_id).upper()
            if not re.fullmatch(r"T\d{4}(\.\d{3})?", technique_id):
                continue
            candidates.append(
                TechniqueCandidate(
                    technique_id=technique_id,
                    score=0.9 * hit.score,
                    source="knowledge",
                    supporting_evidence=[hit.evidence_ref] if hit.evidence_ref else [],
                )
            )
        return candidates

    async def generate(
        self,
        *,
        facts: list[SecurityFact],
        event_techniques: list[str] | None = None,
    ) -> list[TechniqueCandidate]:
        """Combine all deterministic sources and deduplicate."""
        combined: dict[str, TechniqueCandidate] = {}

        evidence: list[str] = []
        for fact in facts:
            if fact.evidence_ref:
                evidence.append(fact.evidence_ref)

        for candidate in self.from_event_techniques(event_techniques or [], evidence=evidence):
            combined[candidate.technique_id] = candidate

        text_parts: list[str] = []
        for fact in facts:
            text_parts.append(fact.value)
            knowledge_hits = await self.from_knowledge(fact)
            for candidate in knowledge_hits:
                if (
                    candidate.technique_id not in combined
                    or candidate.score > combined[candidate.technique_id].score
                ):
                    combined[candidate.technique_id] = candidate

        for candidate in self.from_keywords(" ".join(text_parts), evidence=evidence):
            if (
                candidate.technique_id not in combined
                or candidate.score > combined[candidate.technique_id].score
            ):
                combined[candidate.technique_id] = candidate

        return sorted(combined.values(), key=lambda c: c.score, reverse=True)


class HybridATTCMapper:
    """Deterministic scoring + optional LLM ranking of technique candidates.

    The LLM receives ONLY the candidate set and can rank / explain. It cannot
    add techniques. With no candidates the result is UNKNOWN (no guessing).
    """

    def __init__(
        self,
        generator: AttackTechniqueCandidateGenerator,
        *,
        llm: Any | None = None,
        threshold: float = 0.35,
    ) -> None:
        self._generator = generator
        self._llm = llm
        self._threshold = threshold

    async def map(
        self,
        *,
        facts: list[SecurityFact],
        event_techniques: list[str] | None = None,
    ) -> TechniqueMapping:
        candidates = await self._generator.generate(facts=facts, event_techniques=event_techniques)
        if not candidates:
            return TechniqueMapping(
                technique_id=None,
                score=0.0,
                confidence=0.0,
                supporting_evidence=[],
                explanation="No ATT&CK candidates found; mapped as UNKNOWN (no guess).",
                unknown=True,
            )

        ranked = candidates
        llm_explanation = ""
        if self._llm is not None:
            ranked, llm_explanation = await self._rank_with_llm(candidates)

        # All candidates above threshold count as mapped (set output).
        mapped = [c.technique_id for c in ranked if c.score >= self._threshold]
        best = ranked[0]
        if not mapped:
            return TechniqueMapping(
                technique_id=None,
                score=best.score,
                confidence=0.0,
                supporting_evidence=best.supporting_evidence,
                explanation=f"No candidate above threshold ({self._threshold:.2f}); UNKNOWN.",
                unknown=True,
            )

        return TechniqueMapping(
            technique_id=mapped[0],
            score=best.score,
            confidence=min(1.0, 0.5 + best.score * 0.5),
            supporting_evidence=best.supporting_evidence,
            explanation=llm_explanation or f"Deterministic best candidate: {mapped[0]}",
            candidates=ranked,
            mapped_techniques=mapped,
        )

    async def _rank_with_llm(
        self, candidates: list[TechniqueCandidate]
    ) -> tuple[list[TechniqueCandidate], str]:
        """LLM re-ranks within the closed candidate set. Never expands it."""
        try:
            ids = [c.technique_id for c in candidates]
            response = await self._llm.rank_techniques(ids)
            order = [t.upper() for t in response.order]
            # keep only ids that actually exist in the candidate set
            valid = [t for t in order if t in set(ids)]
            if not valid:
                return candidates, response.explanation or ""
            by_id = {c.technique_id: c for c in candidates}
            ranked = [by_id[t] for t in valid] + [
                c for c in candidates if c.technique_id not in set(valid)
            ]
            return ranked, response.explanation or ""
        except Exception:  # noqa: BLE001 -- LLM failure fails closed to deterministic
            return candidates, ""
