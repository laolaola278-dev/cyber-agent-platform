"""Lab policy -- acquisition policy that EXPLICITLY allows localhost.

Production default SSRF policy (URLPolicyValidator, allow_private=False) is
never modified; the lab validator is a distinct, explicitly-opted-in variant
used only against the 127.0.0.1 synthetic server.
"""

from __future__ import annotations

from app.acquisition.models import AcquisitionPolicy
from app.acquisition.urlpolicy import URLPolicyValidator

LAB_RESOLVER = lambda host: ["127.0.0.1"]  # noqa: E731 -- lab-only resolver


def lab_url_validator() -> URLPolicyValidator:
    return URLPolicyValidator(allow_private=True, resolver=LAB_RESOLVER)


def lab_policy() -> AcquisitionPolicy:
    return AcquisitionPolicy(
        request_rate=50.0,
        max_pages=10,
        max_records=500,
        max_bytes=20 * 1024 * 1024,
        max_document_bytes=10 * 1024 * 1024,
        redirect_limit=4,
        timeout_seconds=4.0,
        retry=1,
    )
