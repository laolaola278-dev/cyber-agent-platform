"""Pluggable report rendering templates."""

from html import escape
from typing import Any, Protocol


class ReportTemplate(Protocol):
    """Render a normalized report payload into one output representation."""

    media_type: str

    def render(self, payload: dict[str, Any]) -> object:
        """Render the payload without persistence side effects."""


class JsonReportTemplate:
    """Return the normalized JSON-compatible payload."""

    media_type = "application/json"

    def render(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class MarkdownReportTemplate:
    """Render a concise human-readable Markdown report."""

    media_type = "text/markdown; charset=utf-8"

    def render(self, payload: dict[str, Any]) -> str:
        task = payload["task"]
        lines = [
            f"# CAP Task Report: {task['name']}",
            "",
            f"- Task ID: `{task['id']}`",
            f"- Task type: `{task['type']}`",
            f"- Agent: `{payload['agent_id']}`",
            f"- Trace ID: `{payload['trace_id']}`",
            f"- Status: **{payload['status']}**",
            "",
            "## Evidence",
        ]
        for item in payload["evidence"]:
            lines.append(f"- [{item['http_status']}] {item['title'] or 'Untitled'} - {item['url']}")
        if payload["error"]:
            lines.extend(["", "## Error", payload["error"]])
        lines.extend(
            [
                "",
                "## Statistics",
                f"- Evidence count: {payload['statistics']['evidence_count']}",
            ]
        )
        return "\n".join(lines)


class HtmlReportTemplate:
    """Render a self-contained semantic HTML report fragment."""

    media_type = "text/html; charset=utf-8"

    def render(self, payload: dict[str, Any]) -> str:
        task = payload["task"]
        evidence_items = "".join(
            "<li>"
            f"[{escape(str(item['http_status']))}] "
            f"{escape(item['title'] or 'Untitled')} - "
            f'<a href="{escape(item["url"], quote=True)}">'
            f"{escape(item['url'])}</a>"
            "</li>"
            for item in payload["evidence"]
        )
        error = ""
        if payload["error"]:
            error = f"<section><h2>Error</h2><pre>{escape(payload['error'])}</pre></section>"
        return (
            "<article>"
            f"<h1>CAP Task Report: {escape(task['name'])}</h1>"
            "<dl>"
            f"<dt>Task ID</dt><dd>{escape(task['id'])}</dd>"
            f"<dt>Task type</dt><dd>{escape(task['type'])}</dd>"
            f"<dt>Agent</dt><dd>{escape(payload['agent_id'])}</dd>"
            f"<dt>Trace ID</dt><dd>{escape(payload['trace_id'])}</dd>"
            f"<dt>Status</dt><dd>{escape(payload['status'])}</dd>"
            "</dl>"
            f"<section><h2>Evidence</h2><ul>{evidence_items}</ul></section>"
            f"{error}"
            "<section><h2>Statistics</h2>"
            f"<p>Evidence count: {payload['statistics']['evidence_count']}</p>"
            "</section></article>"
        )


class ReportTemplateRegistry:
    """Resolve report templates by stable format names."""

    def __init__(self) -> None:
        self._templates: dict[str, ReportTemplate] = {}

    def register(self, name: str, template: ReportTemplate) -> None:
        self._templates[name] = template

    def render(self, name: str, payload: dict[str, Any]) -> object:
        try:
            template = self._templates[name]
        except KeyError as error:
            raise LookupError(f"Report template {name} is not registered") from error
        return template.render(payload)

    @classmethod
    def with_platform_defaults(cls) -> "ReportTemplateRegistry":
        registry = cls()
        registry.register("json", JsonReportTemplate())
        registry.register("markdown", MarkdownReportTemplate())
        registry.register("html", HtmlReportTemplate())
        return registry
