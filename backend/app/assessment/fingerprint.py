"""Replaceable Finding fingerprint providers."""

import json
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from app.schemas.assessment import RawFinding


class FingerprintProvider(Protocol):
    def fingerprint(self, raw: RawFinding, plugin_name: str, asset_id: UUID) -> str: ...


class SHA256FingerprintProvider:
    """Stable default compatible with Phase 6 identity semantics."""

    def fingerprint(self, raw: RawFinding, plugin_name: str, asset_id: UUID) -> str:
        override = raw.attributes.get("fingerprint_material")
        identity = (
            override
            if isinstance(override, dict)
            else {
                "asset_id": str(asset_id),
                "plugin": plugin_name.casefold(),
                "tool": (raw.tool or "").casefold(),
                "rule": (raw.rule or "").casefold(),
                "unique_id": (raw.unique_id_from_tool or "").casefold(),
                "title": raw.title.casefold(),
                "affected_asset": raw.affected_asset.casefold(),
            }
        )
        return sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
