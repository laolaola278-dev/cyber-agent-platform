"""Finding lifecycle state machine."""

from app.core.enums import FindingStatus
from app.exceptions import InvalidStateTransition


class FindingStateMachine:
    _transitions = {
        FindingStatus.NEW: {FindingStatus.TRIAGED},
        FindingStatus.TRIAGED: {
            FindingStatus.CONFIRMED,
            FindingStatus.FALSE_POSITIVE,
            FindingStatus.ACCEPTED_RISK,
        },
        FindingStatus.CONFIRMED: {FindingStatus.FIXED, FindingStatus.ACCEPTED_RISK},
        FindingStatus.FALSE_POSITIVE: {FindingStatus.REOPENED},
        FindingStatus.ACCEPTED_RISK: {FindingStatus.REOPENED, FindingStatus.FIXED},
        FindingStatus.FIXED: {FindingStatus.REOPENED},
        FindingStatus.REOPENED: {
            FindingStatus.TRIAGED,
            FindingStatus.CONFIRMED,
            FindingStatus.FALSE_POSITIVE,
            FindingStatus.ACCEPTED_RISK,
            FindingStatus.FIXED,
        },
    }

    @classmethod
    def validate(cls, current: FindingStatus, target: FindingStatus) -> None:
        if target not in cls._transitions[current]:
            raise InvalidStateTransition(
                f"Finding cannot transition from {current.value} to {target.value}",
                details={"from": current.value, "to": target.value},
            )
