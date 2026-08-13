"""Unified Notification and Ticket Framework exports."""

from app.notification.contracts import NotificationPlugin, NotificationPluginContext
from app.notification.fake_plugin import FakeNotificationPlugin
from app.notification.planner import NotificationPlanner
from app.notification.policy import (
    NotificationPolicyDecision,
    NotificationPolicyEngine,
    NotificationPolicyInput,
)
from app.notification.registry import NotificationRegistry
from app.notification.routing import NotificationRouteResult, RoutingEngine
from app.notification.runtime import NotificationRuntime
from app.notification.service import NotificationService, default_templates
from app.notification.template import TemplateDefinition, TemplateProvider

__all__ = [
    "FakeNotificationPlugin",
    "NotificationPlanner",
    "NotificationPlugin",
    "NotificationPluginContext",
    "NotificationPolicyDecision",
    "NotificationPolicyEngine",
    "NotificationPolicyInput",
    "NotificationRegistry",
    "NotificationRouteResult",
    "NotificationRuntime",
    "NotificationService",
    "RoutingEngine",
    "TemplateDefinition",
    "TemplateProvider",
    "default_templates",
]
