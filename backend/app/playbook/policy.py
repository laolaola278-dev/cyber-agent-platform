"""Fail-closed Playbook policy and capability boundary."""

from dataclasses import dataclass
from typing import Any

from app.exceptions import PlaybookPolicyViolation, PlaybookValidationError
from app.playbook.contracts import PlaybookDocument, PlaybookNodeType


@dataclass(frozen=True, slots=True)
class PlaybookPolicy:
    allowed_runners: frozenset[str] = frozenset({"api-user", "playbook-event"})
    allowed_approvers: frozenset[str] = frozenset()
    allowed_plugins: frozenset[str] = frozenset()
    allowed_capabilities: frozenset[str] = frozenset()
    max_timeout_seconds: int = 86_400
    max_retry_attempts: int = 5
    max_parallel: int = 1

    def validate_document(self, document: PlaybookDocument) -> None:
        if document.max_parallel > self.max_parallel:
            raise PlaybookValidationError("Playbook max_parallel exceeds platform policy")
        if document.timeout_seconds > self.max_timeout_seconds:
            raise PlaybookValidationError("Playbook timeout exceeds platform policy")
        for step in document.steps:
            if step.retry.max_attempts > self.max_retry_attempts:
                raise PlaybookValidationError("Playbook retry exceeds platform policy")
            if (
                step.type
                in {
                    PlaybookNodeType.ASSESSMENT,
                    PlaybookNodeType.DETECTION,
                    PlaybookNodeType.RESPONSE,
                    PlaybookNodeType.NOTIFICATION,
                    PlaybookNodeType.TICKET,
                }
                and self.allowed_capabilities
                and step.capability not in self.allowed_capabilities
            ):
                raise PlaybookPolicyViolation(
                    f"Capability is not allowed by Playbook policy: {step.capability}"
                )
            if (
                document.allowed_plugins
                and self.allowed_plugins
                and not set(document.allowed_plugins) <= self.allowed_plugins
            ):
                raise PlaybookPolicyViolation("Playbook plugin is not allowed by platform policy")

    def authorize_runner(self, actor: str) -> None:
        if self.allowed_runners and actor not in self.allowed_runners:
            raise PlaybookPolicyViolation(f"Runner is not allowed to execute Playbook: {actor}")

    def authorize_approver(self, approver: str) -> None:
        if self.allowed_approvers and approver not in self.allowed_approvers:
            raise PlaybookPolicyViolation(f"Approver is not allowed for Playbook: {approver}")

    @staticmethod
    def matches_filters(filters: dict[str, Any], payload: dict[str, Any]) -> bool:
        return all(payload.get(key) == value for key, value in filters.items())
