"""Agentic engine exceptions (v2.0 / Phase 25).

Re-exported from :mod:`app.exceptions` so the whole platform shares one
exception catalogue and error envelope. Agent exceptions inherit
:class:`app.exceptions.PlatformError`.
"""

from __future__ import annotations

from app.exceptions import (
    AgentError,
    AgentExecutionError,
    AgentGuardrailViolation,
    AgentLoopLimit,
    AgentPlanningError,
)

__all__ = [
    "AgentError",
    "AgentExecutionError",
    "AgentGuardrailViolation",
    "AgentLoopLimit",
    "AgentPlanningError",
]
