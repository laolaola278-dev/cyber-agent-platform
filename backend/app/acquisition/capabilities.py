"""Phase 28 -- acquisition.* capability seed (spec 4).

Registers the acquisition capabilities into the SINGLE platform Capability
Registry. There is no second registry. Idempotent; safe to re-run.
"""

from __future__ import annotations

from app.capabilities import CapabilityRegistryService

ACQUISITION_CAPABILITIES: dict[str, str] = {
    "acquisition.http": "controlled public HTTP(S) acquisition (GET, bounded redirects)",
    "acquisition.browser": "public page browser acquisition via Playwright tool (observation only)",
    "acquisition.document": "document acquisition and parsing (PDF/DOCX/XLSX/HTML/JSON/TEXT)",
    "acquisition.extract": "structured content extraction into ExtractedDocument",
    "acquisition.paginate": "bounded pagination (next-link/page/cursor/load-more)",
    "acquisition.discover": "public endpoint observation (XHR/Fetch from the page itself)",
    "acquisition.verify": "public endpoint candidate validation",
    "acquisition.public": "public-only acquisition policy (SSRF/robots/auth boundaries)",
}


async def seed_acquisition_capabilities(service: CapabilityRegistryService) -> None:
    for name, description in ACQUISITION_CAPABILITIES.items():
        await service.register(name, description=description, risk_level="LOW")
