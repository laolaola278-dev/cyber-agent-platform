"""Phase 28.5 -- versioned JSON execution protocol for the OCI sandbox.

Cross-trust-boundary transport for container sandbox executions. NO Python
object serialization (cloudpickle deserialization == arbitrary code
execution) is used between the worker and the container. The worker sends a
typed, validated JSON request; the in-container shim executes ONLY the named
operation against the URL/policy snapshot; results come back as typed JSON.

Only value data crosses the boundary:
  * request: version, operation kind, run_id, sandbox_execution_id, url,
    policy snapshot (schemes / user agent / timeouts / validator snapshot)
  * response: version, status, typed result, error

NEVER crossing the boundary: DB sessions, SQLAlchemy engines, worker/service
runtime objects, fencing tokens, secret values.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1

OperationKind = Literal["http_fetch", "browser_browse"]


class PolicySnapshot(BaseModel):
    """Serializable slice of AcquisitionPolicy + URLPolicyValidator needed by
    the in-container shim to enforce the SAME application-layer SSRF gate."""

    model_config = ConfigDict(extra="forbid")

    allowed_schemes: tuple[str, ...] = ("http", "https")
    user_agent: str = "cap-acquisition/1.0"
    timeout_seconds: float = 30.0
    max_response_bytes: int = 20 * 1024 * 1024
    allow_private: bool = False  # production default: never


class SandboxRequest(BaseModel):
    """What the worker sends to the sandbox shim."""

    model_config = ConfigDict(extra="forbid")

    version: int = PROTOCOL_VERSION
    operation: OperationKind
    run_id: str = Field(min_length=1)
    sandbox_execution_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    policy: PolicySnapshot = Field(default_factory=PolicySnapshot)
    # browser-specific
    wait_for_selector: str | None = None
    wait_network_idle_ms: int = 1500
    max_wait_ms: int = 15_000


class SandboxResponse(BaseModel):
    """What the shim returns to the worker."""

    model_config = ConfigDict(extra="forbid")

    version: int = PROTOCOL_VERSION
    status: Literal["ok", "error"]
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None


# -- typed result codecs ------------------------------------------------------
# HTTPFetchResult and BrowserObservation are converted to plain dicts for the
# JSON boundary; bytes payloads are base64 (JSON cannot carry raw bytes).


def http_fetch_result_to_dict(result: Any) -> dict[str, Any]:
    content = getattr(result, "content", b"") or b""
    return {
        "status": getattr(result, "status", 0),
        "final_url": getattr(result, "final_url", ""),
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_type": getattr(result, "content_type", "") or "",
        "etag": getattr(result, "etag", None),
        "last_modified": getattr(result, "last_modified", None),
        "blocked_reason": getattr(result, "blocked_reason", None),
        "blocked_detail": getattr(result, "blocked_detail", None),
        "duration_ms": getattr(result, "duration_ms", 0),
    }


def http_fetch_result_from_dict(data: dict[str, Any]):
    from app.acquisition.httpadapter import HTTPFetchResult

    content = base64.b64decode(data.get("content_b64") or "")
    return HTTPFetchResult(
        status=data.get("status", 0),
        final_url=data.get("final_url", ""),
        content=content,
        content_type=data.get("content_type", ""),
        etag=data.get("etag"),
        last_modified=data.get("last_modified"),
        blocked_reason=data.get("blocked_reason"),
        blocked_detail=data.get("blocked_detail"),
        duration_ms=data.get("duration_ms", 0),
    )


def browser_observation_to_dict(result: Any) -> dict[str, Any]:
    return {
        "url": getattr(result, "url", ""),
        "final_url": getattr(result, "final_url", ""),
        "status": getattr(result, "status", None),
        "html": getattr(result, "html", ""),
        "title": getattr(result, "title", ""),
        "available": bool(getattr(result, "available", False)),
        "error": getattr(result, "error", ""),
        "endpoints": [
            {
                "url": e.url,
                "method": getattr(e, "method", "GET"),
                "requested_at": (
                    e.requested_at.isoformat()
                    if isinstance(getattr(e, "requested_at", None), datetime)
                    else None
                ),
            }
            for e in (getattr(result, "endpoints", None) or [])
        ],
    }


def browser_observation_from_dict(data: dict[str, Any]):
    from app.acquisition.browseradapter import BrowserObservation
    from app.acquisition.models import PublicEndpointCandidate

    endpoints = [
        PublicEndpointCandidate(url=e["url"], method=e.get("method", "GET"))
        for e in (data.get("endpoints") or [])
    ]
    return BrowserObservation(
        url=data.get("url", ""),
        final_url=data.get("final_url", ""),
        status=data.get("status"),
        html=data.get("html", ""),
        title=data.get("title", ""),
        endpoints=endpoints,
        available=bool(data.get("available", False)),
        error=data.get("error", ""),
    )


def validate_request(request: SandboxRequest) -> None:
    """Fail closed before anything is handed to a container."""
    if request.version != PROTOCOL_VERSION:
        raise ValueError(
            f"sandbox protocol version mismatch: {request.version} != {PROTOCOL_VERSION}"
        )
    if request.operation not in ("http_fetch", "browser_browse"):
        raise ValueError(f"unknown sandbox operation: {request.operation}")
    if not request.sandbox_execution_id:
        raise ValueError("sandbox_execution_id is required")
    # fencing tokens / secrets must never ride the protocol
    for value in (request.url,):
        for forbidden in ("fencing", "claim_token", "secret=", "password="):
            if forbidden in value.casefold():
                raise ValueError(f"request carries a forbidden field: {forbidden}")
    try:
        UUID(request.sandbox_execution_id)
    except ValueError as error:
        raise ValueError("sandbox_execution_id must be a UUID") from error
