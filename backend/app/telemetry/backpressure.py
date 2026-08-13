"""Bounded queue and explicit backpressure semantics."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from app.exceptions import TelemetryExecutionError, TelemetryPolicyViolation
from app.schemas.telemetry import BackpressureAction, TelemetryPolicy


class BackpressureDecision(StrEnum):
    ACCEPT = "ACCEPT"
    DROP = "DROP"
    RETRY = "RETRY"
    PAUSE = "PAUSE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class BackpressureResult:
    decision: BackpressureDecision
    attempts: int = 0


class BoundedTelemetryQueue[ItemT]:
    """A bounded queue whose full behavior is explicit and finite."""

    def __init__(self, policy: TelemetryPolicy) -> None:
        self._policy = policy
        self._queue: asyncio.Queue[ItemT] = asyncio.Queue(maxsize=policy.queue_capacity)

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    async def put(self, item: ItemT) -> BackpressureResult:
        action = self._policy.backpressure_action
        if not self._queue.full():
            self._queue.put_nowait(item)
            return BackpressureResult(BackpressureDecision.ACCEPT)
        if action is BackpressureAction.DROP:
            return BackpressureResult(BackpressureDecision.DROP)
        if action is BackpressureAction.REJECT:
            raise TelemetryPolicyViolation("Telemetry queue is full")
        if action is BackpressureAction.PAUSE:
            await asyncio.sleep(self._policy.pause_seconds)
            if self._queue.full():
                raise TelemetryPolicyViolation("Telemetry queue remained full after bounded pause")
            self._queue.put_nowait(item)
            return BackpressureResult(BackpressureDecision.PAUSE)
        return BackpressureResult(BackpressureDecision.RETRY)

    async def get(self) -> ItemT:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()


async def execute_with_backpressure[
    ItemT
](operation: Callable[[], Awaitable[ItemT]], policy: TelemetryPolicy) -> tuple[
    ItemT | None, BackpressureResult
]:
    """Retry only the configured finite number of times."""

    if policy.backpressure_action is not BackpressureAction.RETRY:
        try:
            return await operation(), BackpressureResult(BackpressureDecision.ACCEPT)
        except Exception:
            raise
    for attempt in range(policy.retry_attempts + 1):
        try:
            return await operation(), BackpressureResult(BackpressureDecision.RETRY, attempt)
        except Exception as error:
            if attempt >= policy.retry_attempts:
                raise TelemetryExecutionError("Telemetry operation retries exhausted") from error
            if policy.pause_seconds:
                await asyncio.sleep(policy.pause_seconds)
    raise TelemetryExecutionError("Telemetry retry loop ended unexpectedly")
