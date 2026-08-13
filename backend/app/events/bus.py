"""Dependency-injected event publishing and subscription boundaries."""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.events.contracts import EventType, PlatformEvent

EventHandler = Callable[[PlatformEvent], Awaitable[None]]


class EventPublisher(Protocol):
    """Publish platform events without coupling callers to a broker."""

    async def publish(self, event: PlatformEvent) -> None: ...


class EventSubscriber(Protocol):
    """Register event consumers without exposing publisher internals."""

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None: ...


class InMemoryEventBus:
    """Deterministic process-local publisher/subscriber for Phase 1."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)

    async def publish(self, event: PlatformEvent) -> None:
        for handler in tuple(self._handlers[event.type]):
            await handler(event)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)


EventBus = EventPublisher
