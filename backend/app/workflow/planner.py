"""Deterministic rule-based capability planning without LLM dependencies."""

from dataclasses import dataclass

from app.schemas.workflow import CapabilityPlan, WorkflowDocument


@dataclass(frozen=True, slots=True)
class PlanningRule:
    keywords: frozenset[str]
    capabilities: tuple[str, ...]


class CapabilityPlanner:
    """Map an explicit user goal to a stable capability sequence."""

    def __init__(self, rules: tuple[PlanningRule, ...] | None = None) -> None:
        self._rules = rules or (
            PlanningRule(
                frozenset({"采集", "抓取", "crawl", "collect", "网站"}),
                (
                    "crawl.html",
                    "browser.render",
                    "evidence.generate",
                    "report.generate",
                ),
            ),
        )

    def plan(self, goal: str) -> CapabilityPlan:
        normalized = goal.casefold()
        rule = next(
            (
                candidate
                for candidate in self._rules
                if any(keyword.casefold() in normalized for keyword in candidate.keywords)
            ),
            None,
        )
        if rule is None:
            raise ValueError("No deterministic capability planning rule matches the goal")
        nodes: list[dict[str, object]] = [{"id": "start", "type": "start"}]
        edges: list[dict[str, str]] = []
        previous = "start"
        for index, capability in enumerate(rule.capabilities, start=1):
            node_id = f"capability-{index}"
            nodes.append(
                {
                    "id": node_id,
                    "type": "agent",
                    "capability": capability,
                }
            )
            edges.append({"source": previous, "target": node_id})
            previous = node_id
        nodes.append({"id": "end", "type": "end"})
        edges.append({"source": previous, "target": "end"})
        workflow = WorkflowDocument.model_validate(
            {
                "name": "planned-workflow",
                "description": f"Rule-based plan for: {goal}",
                "nodes": nodes,
                "edges": edges,
            }
        )
        return CapabilityPlan(
            goal=goal,
            capabilities=list(rule.capabilities),
            workflow=workflow,
        )
