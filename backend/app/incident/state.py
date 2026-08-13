"""Incident lifecycle state machine owned by the Incident bounded context."""

from app.core.enums import IncidentStatus
from app.core.state_machine import StateMachine

INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.NEW: frozenset({IncidentStatus.TRIAGED}),
    IncidentStatus.TRIAGED: frozenset({IncidentStatus.INVESTIGATING}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.CONTAINED}),
    IncidentStatus.CONTAINED: frozenset({IncidentStatus.RESOLVED}),
    IncidentStatus.RESOLVED: frozenset({IncidentStatus.CLOSED, IncidentStatus.REOPENED}),
    IncidentStatus.CLOSED: frozenset({IncidentStatus.REOPENED}),
    IncidentStatus.REOPENED: frozenset({IncidentStatus.TRIAGED, IncidentStatus.INVESTIGATING}),
}

IncidentStateMachine = StateMachine(INCIDENT_TRANSITIONS, IncidentStatus)
