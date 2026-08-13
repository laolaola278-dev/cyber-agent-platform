"""Unified platform exception hierarchy."""

from typing import Any


class PlatformError(Exception):
    """Base exception with a stable machine-readable error code."""

    code = "PLATFORM_ERROR"
    status_code = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AgentNotFound(PlatformError):
    code = "AGENT_NOT_FOUND"
    status_code = 404


class ToolNotFound(PlatformError):
    code = "TOOL_NOT_FOUND"
    status_code = 404


class CapabilityNotFound(PlatformError):
    code = "CAPABILITY_NOT_FOUND"
    status_code = 404


class PermissionDenied(PlatformError):
    code = "PERMISSION_DENIED"
    status_code = 403


class TaskNotFound(PlatformError):
    code = "TASK_NOT_FOUND"
    status_code = 404


class RegistryError(PlatformError):
    code = "REGISTRY_ERROR"
    status_code = 409


class InvalidStateTransition(PlatformError):
    code = "INVALID_STATE_TRANSITION"
    status_code = 409


class ValidationError(PlatformError):
    code = "VALIDATION_ERROR"
    status_code = 422


class AssetNotFound(PlatformError):
    code = "ASSET_NOT_FOUND"
    status_code = 404


class AssetConflict(PlatformError):
    code = "ASSET_CONFLICT"
    status_code = 409


class AssetResolutionError(PlatformError):
    code = "ASSET_RESOLUTION_ERROR"
    status_code = 422


class KnowledgeNotFound(PlatformError):
    code = "KNOWLEDGE_NOT_FOUND"
    status_code = 404


class KnowledgeConflict(PlatformError):
    code = "KNOWLEDGE_CONFLICT"
    status_code = 409


class KnowledgeValidationError(PlatformError):
    code = "KNOWLEDGE_VALIDATION_ERROR"
    status_code = 422


class AssessmentNotFound(PlatformError):
    code = "ASSESSMENT_NOT_FOUND"
    status_code = 404


class FindingNotFound(PlatformError):
    code = "FINDING_NOT_FOUND"
    status_code = 404


class AssessmentValidationError(PlatformError):
    code = "ASSESSMENT_VALIDATION_ERROR"
    status_code = 422


class AssessmentPolicyViolation(PlatformError):
    code = "ASSESSMENT_POLICY_VIOLATION"
    status_code = 403


class AssessmentExecutionError(PlatformError):
    code = "ASSESSMENT_EXECUTION_ERROR"
    status_code = 422


class DetectionNotFound(PlatformError):
    code = "DETECTION_NOT_FOUND"
    status_code = 404


class SecurityEventNotFound(PlatformError):
    code = "SECURITY_EVENT_NOT_FOUND"
    status_code = 404


class DetectionValidationError(PlatformError):
    code = "DETECTION_VALIDATION_ERROR"
    status_code = 422


class DetectionPolicyViolation(PlatformError):
    code = "DETECTION_POLICY_VIOLATION"
    status_code = 403


class DetectionExecutionError(PlatformError):
    code = "DETECTION_EXECUTION_ERROR"
    status_code = 422


class IncidentNotFound(PlatformError):
    code = "INCIDENT_NOT_FOUND"
    status_code = 404


class InvestigationCaseNotFound(PlatformError):
    code = "INVESTIGATION_CASE_NOT_FOUND"
    status_code = 404


class IncidentValidationError(PlatformError):
    code = "INCIDENT_VALIDATION_ERROR"
    status_code = 422


class IncidentPolicyViolation(PlatformError):
    code = "INCIDENT_POLICY_VIOLATION"
    status_code = 403


class IncidentExecutionError(PlatformError):
    code = "INCIDENT_EXECUTION_ERROR"
    status_code = 422


class IncidentConflict(PlatformError):
    code = "INCIDENT_CONFLICT"
    status_code = 409


class TelemetryNotFound(PlatformError):
    code = "TELEMETRY_NOT_FOUND"
    status_code = 404


class TelemetryValidationError(PlatformError):
    code = "TELEMETRY_VALIDATION_ERROR"
    status_code = 422


class TelemetryPolicyViolation(PlatformError):
    code = "TELEMETRY_POLICY_VIOLATION"
    status_code = 403


class TelemetryExecutionError(PlatformError):
    code = "TELEMETRY_EXECUTION_ERROR"
    status_code = 422


class TelemetryConflict(PlatformError):
    code = "TELEMETRY_CONFLICT"
    status_code = 409


class ResponseNotFound(PlatformError):
    code = "RESPONSE_NOT_FOUND"
    status_code = 404


class ResponseValidationError(PlatformError):
    code = "RESPONSE_VALIDATION_ERROR"
    status_code = 422


class ResponsePolicyViolation(PlatformError):
    code = "RESPONSE_POLICY_VIOLATION"
    status_code = 403


class ResponseExecutionError(PlatformError):
    code = "RESPONSE_EXECUTION_ERROR"
    status_code = 422


class ResponseConflict(PlatformError):
    code = "RESPONSE_CONFLICT"
    status_code = 409


class NotificationNotFound(PlatformError):
    code = "NOTIFICATION_NOT_FOUND"
    status_code = 404


class NotificationValidationError(PlatformError):
    code = "NOTIFICATION_VALIDATION_ERROR"
    status_code = 422


class NotificationPolicyViolation(PlatformError):
    code = "NOTIFICATION_POLICY_VIOLATION"
    status_code = 403


class NotificationExecutionError(PlatformError):
    code = "NOTIFICATION_EXECUTION_ERROR"
    status_code = 422


class NotificationConflict(PlatformError):
    code = "NOTIFICATION_CONFLICT"
    status_code = 409


class TicketNotFound(PlatformError):
    code = "TICKET_NOT_FOUND"
    status_code = 404


class PlaybookNotFound(PlatformError):
    code = "PLAYBOOK_NOT_FOUND"
    status_code = 404


class PlaybookConflict(PlatformError):
    code = "PLAYBOOK_CONFLICT"
    status_code = 409


class PlaybookValidationError(PlatformError):
    code = "PLAYBOOK_VALIDATION_ERROR"
    status_code = 422


class PlaybookPolicyViolation(PlatformError):
    code = "PLAYBOOK_POLICY_VIOLATION"
    status_code = 403


class PlaybookExecutionError(PlatformError):
    code = "PLAYBOOK_EXECUTION_ERROR"
    status_code = 422


class WorkflowNotFound(PlatformError):
    code = "WORKFLOW_NOT_FOUND"
    status_code = 404


class WorkflowConflict(PlatformError):
    code = "WORKFLOW_CONFLICT"
    status_code = 409


class WorkflowExecutionError(PlatformError):
    code = "WORKFLOW_EXECUTION_ERROR"
    status_code = 422


class SandboxPolicyViolation(PlatformError):
    code = "SANDBOX_POLICY_VIOLATION"
    status_code = 403


class SandboxExecutionError(PlatformError):
    code = "SANDBOX_EXECUTION_ERROR"
    status_code = 422


class SecretNotFound(PlatformError):
    code = "SECRET_NOT_FOUND"
    status_code = 404


class SecretPolicyViolation(PlatformError):
    code = "SECRET_POLICY_VIOLATION"
    status_code = 403


class WorkerConflict(PlatformError):
    code = "WORKER_CONFLICT"
    status_code = 409


class WorkerNotFound(PlatformError):
    code = "WORKER_NOT_FOUND"
    status_code = 404


class WorkerLeaseConflict(PlatformError):
    code = "WORKER_LEASE_CONFLICT"
    status_code = 409


class WorkerLeaseNotFound(PlatformError):
    code = "WORKER_LEASE_NOT_FOUND"
    status_code = 404


class WorkerUnavailable(PlatformError):
    code = "WORKER_UNAVAILABLE"
    status_code = 503


class WorkerExecutionError(PlatformError):
    code = "WORKER_EXECUTION_ERROR"
    status_code = 422


class WorkerCancelledError(PlatformError):
    """Plugin execution was cancelled by the sandbox (not a failure).

    Raised when the worker runtime reports a CANCELLED terminal state so the
    plugin caller can finalize the owning run as CANCELLED (resources already
    closed by the sandbox boundary).
    """

    code = "WORKER_CANCELLED"
    status_code = 202


class AgentError(PlatformError):
    """Base class for Agentic engine errors."""

    code = "AGENT_ERROR"
    status_code = 400


class AgentPlanningError(AgentError):
    """Plan generation failed validation or the model returned an invalid plan."""

    code = "AGENT_PLANNING_ERROR"
    status_code = 400


class AgentGuardrailViolation(AgentError):
    """A guardrail rejected an agent action (fail closed)."""

    code = "AGENT_GUARDRAIL_VIOLATION"
    status_code = 403


class AgentExecutionError(AgentError):
    """A capability execution failed."""

    code = "AGENT_EXECUTION_ERROR"
    status_code = 422


class AgentLoopLimit(AgentError):
    """An agent loop exceeded its budget limits."""

    code = "AGENT_LOOP_LIMIT"
    status_code = 429
