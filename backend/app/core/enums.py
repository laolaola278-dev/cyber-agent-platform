"""Stable platform state values shared by services and persistence."""

from enum import StrEnum


class AgentStatus(StrEnum):
    """Lifecycle state of a registered Agent runtime."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class HealthStatus(StrEnum):
    """Health result reported by an Agent runtime."""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class ToolStatus(StrEnum):
    """Lifecycle state of a Tool definition."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class AssetType(StrEnum):
    DOMAIN = "DOMAIN"
    IP = "IP"
    HOST = "HOST"
    WEBSITE = "WEBSITE"
    APPLICATION = "APPLICATION"
    CONTAINER = "CONTAINER"
    CLOUD_RESOURCE = "CLOUD_RESOURCE"
    REPOSITORY = "REPOSITORY"
    DOCUMENT = "DOCUMENT"
    USER = "USER"
    AGENT = "AGENT"


class AssetRelationType(StrEnum):
    RESOLVES_TO = "resolves_to"
    HOSTED_ON = "hosted_on"
    RUNS_ON = "runs_on"
    DEPLOYED_IN = "deployed_in"
    REFERENCES = "references"
    RELATED_TO = "related_to"


class KnowledgeType(StrEnum):
    CVE = "CVE"
    CWE = "CWE"
    CAPEC = "CAPEC"
    CPE = "CPE"
    ATTACK_TECHNIQUE = "ATTACK_TECHNIQUE"
    ATTACK_TACTIC = "ATTACK_TACTIC"
    CISA_KEV = "CISA_KEV"
    OWASP_CATEGORY = "OWASP_CATEGORY"
    VENDOR_ADVISORY = "VENDOR_ADVISORY"
    IOC = "IOC"
    RULE_METADATA = "RULE_METADATA"


class KnowledgeRelationType(StrEnum):
    AFFECTS = "affects"
    MAPS_TO = "maps_to"
    EXPLOITED_BY = "exploited_by"
    RELATED_TO = "related_to"
    BELONGS_TO = "belongs_to"
    DERIVED_FROM = "derived_from"


class KnowledgeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"
    ARCHIVED = "ARCHIVED"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FindingStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    CONFIRMED = "CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    FIXED = "FIXED"
    REOPENED = "REOPENED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AssessmentTaskStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DetectionTaskStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SecurityEventStatus(StrEnum):
    NEW = "NEW"
    CORRELATED = "CORRELATED"
    TRIAGED = "TRIAGED"
    IGNORED = "IGNORED"
    ARCHIVED = "ARCHIVED"


class IncidentStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    CONTAINED = "CONTAINED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class IncidentPriority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class InvestigationStatus(StrEnum):
    OPEN = "OPEN"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"


class IncidentArtifactType(StrEnum):
    ASSET = "ASSET"
    EVIDENCE = "EVIDENCE"
    FINDING = "FINDING"
    SECURITY_EVENT = "SECURITY_EVENT"
    KNOWLEDGE = "KNOWLEDGE"
    REPORT = "REPORT"
    URL = "URL"
    HASH = "HASH"
    IP = "IP"
    DOMAIN = "DOMAIN"


class IncidentTimelineType(StrEnum):
    CREATED = "CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    INVESTIGATION_ACTION = "INVESTIGATION_ACTION"
    ARTIFACT_LINKED = "ARTIFACT_LINKED"
    COMMENTED = "COMMENTED"
    ASSIGNMENT_CHANGED = "ASSIGNMENT_CHANGED"
    MERGED = "MERGED"
    REOPENED = "REOPENED"


class EvidenceType(StrEnum):
    """Normalized evidence categories produced by platform Agents and Tools."""

    HTML = "HTML"
    SCREENSHOT = "SCREENSHOT"
    TEXT = "TEXT"
    JSON = "JSON"
    FILE = "FILE"


class TaskStatus(StrEnum):
    """Task lifecycle governed by the Orchestrator."""

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowStatus(StrEnum):
    """Durable workflow instance lifecycle."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    FAILED = "FAILED"
    SUCCESS = "SUCCESS"
    CANCELLED = "CANCELLED"


class WorkflowStepStatus(StrEnum):
    """Checkpoint state for one workflow node."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    FAILED = "FAILED"
    SUCCESS = "SUCCESS"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class WorkflowNodeType(StrEnum):
    """Stable plugin identifiers for built-in workflow node handlers."""

    START = "start"
    AGENT = "agent"
    CONDITION = "condition"
    APPROVAL = "approval"
    END = "end"
