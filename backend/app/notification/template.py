"""Safe declarative notification template provider."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.exceptions import NotificationValidationError
from app.schemas.notification import RenderedNotification, TemplateFormat

_VARIABLE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_-]{0,127})\s*}}")
_FORBIDDEN = re.compile(
    r"({%|{#|__|\beval\b|\bexec\b|\bimport\b|\bclass\b|\blambda\b|\(|\)|\[|\]|\{\{[^{}]*\.[^{}]*\}\})",
    re.IGNORECASE,
)
_CONTENT_TYPES = {
    TemplateFormat.MARKDOWN: "text/markdown; charset=utf-8",
    TemplateFormat.HTML: "text/html; charset=utf-8",
    TemplateFormat.JSON: "application/json",
    TemplateFormat.TEXT: "text/plain; charset=utf-8",
}


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    name: str
    format: TemplateFormat
    subject: str
    body: str
    variables: frozenset[str]


class TemplateProvider:
    """Render only scalar substitutions; expressions and attribute traversal are forbidden."""

    def __init__(self) -> None:
        self._templates: dict[str, TemplateDefinition] = {}

    def register(self, template: TemplateDefinition) -> None:
        name = template.name.strip().casefold()
        if not name or name in self._templates:
            raise NotificationValidationError(
                "Notification template identity is invalid or duplicated"
            )
        self._validate_source(template.subject)
        self._validate_source(template.body)
        discovered = set(_VARIABLE.findall(f"{template.subject}\n{template.body}"))
        if discovered - set(template.variables):
            raise NotificationValidationError(
                "Template references undeclared variables",
                details={"variables": sorted(discovered - set(template.variables))},
            )
        self._templates[name] = template

    def require(self, name: str) -> TemplateDefinition:
        try:
            return self._templates[name.strip().casefold()]
        except KeyError as error:
            raise NotificationValidationError(
                f"Notification template {name} is not registered"
            ) from error

    def render(
        self, template: TemplateDefinition, variables: Mapping[str, object]
    ) -> RenderedNotification:
        safe_values = {key: self._scalar(value) for key, value in variables.items()}
        missing = set(template.variables) - set(safe_values)
        if missing:
            raise NotificationValidationError(
                "Notification template variables are missing",
                details={"variables": sorted(missing)},
            )

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in safe_values:
                raise NotificationValidationError(f"Template variable {name} is unavailable")
            return safe_values[name]

        subject = _VARIABLE.sub(substitute, template.subject)
        body = _VARIABLE.sub(substitute, template.body)
        if _VARIABLE.search(subject) or _VARIABLE.search(body):
            raise NotificationValidationError("Template contains unresolved variables")
        if template.format == TemplateFormat.JSON:
            try:
                json.loads(body)
            except json.JSONDecodeError as error:
                raise NotificationValidationError(
                    "Rendered JSON notification is invalid"
                ) from error
        return RenderedNotification(
            subject=subject,
            body=body,
            format=template.format,
            content_type=_CONTENT_TYPES[template.format],
        )

    @staticmethod
    def _validate_source(source: str) -> None:
        if _FORBIDDEN.search(source):
            raise NotificationValidationError(
                "Notification templates cannot execute code or expressions"
            )

    @staticmethod
    def _scalar(value: object) -> str:
        if value is None or isinstance(value, str | int | float | bool):
            return str(value)
        raise NotificationValidationError("Notification template variables must be scalar values")

    @property
    def templates(self) -> tuple[TemplateDefinition, ...]:
        return tuple(sorted(self._templates.values(), key=lambda item: item.name))
