"""YAML workflow parsing, validation, and DAG compilation."""

from collections import deque

import yaml

from app.schemas.workflow import WorkflowDocument


class WorkflowDefinitionLoader:
    """Parse untrusted YAML safely and validate the normalized DAG contract."""

    def load(self, source: str) -> WorkflowDocument:
        raw = yaml.safe_load(source)
        if not isinstance(raw, dict):
            raise ValueError("Workflow YAML must contain a mapping")
        raw = self._normalize_steps(raw)
        document = WorkflowDocument.model_validate(raw)
        self._validate_graph(document)
        return document

    def _normalize_steps(self, raw: dict[str, object]) -> dict[str, object]:
        """Compile the concise capability sequence syntax into an explicit DAG."""

        if "nodes" in raw:
            return raw
        steps = raw.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Workflow requires nodes or a non-empty steps list")
        nodes: list[dict[str, object]] = [{"id": "start", "type": "start"}]
        edges: list[dict[str, str]] = []
        previous = "start"
        for index, item in enumerate(steps, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("capability"), str):
                raise ValueError("Each concise workflow step requires a capability")
            node_id = str(item.get("id") or f"step-{index}")
            node = {
                "id": node_id,
                "type": "agent",
                "capability": item["capability"],
                "input": item.get("input", {}),
                "timeout_seconds": item.get("timeout_seconds", 60),
                "retry": item.get("retry", {"max_attempts": 1}),
            }
            nodes.append(node)
            edges.append({"source": previous, "target": node_id})
            previous = node_id
        nodes.append({"id": "end", "type": "end"})
        edges.append({"source": previous, "target": "end"})
        return {**raw, "nodes": nodes, "edges": edges}

    def _validate_graph(self, document: WorkflowDocument) -> None:
        node_ids = [node.id for node in document.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Workflow node IDs must be unique")
        nodes = {node.id: node for node in document.nodes}
        starts = [node.id for node in document.nodes if node.type.value == "start"]
        ends = [node.id for node in document.nodes if node.type.value == "end"]
        if len(starts) != 1 or len(ends) != 1:
            raise ValueError("Workflow requires exactly one StartNode and one EndNode")

        adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        indegree = dict.fromkeys(nodes, 0)
        for edge in document.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("Workflow edge references an unknown node")
            if nodes[edge.source].type.value == "condition" and edge.when is None:
                raise ValueError("ConditionNode edges require when=true or when=false")
            if nodes[edge.source].type.value != "condition" and edge.when is not None:
                raise ValueError("Only ConditionNode edges may declare when")
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(nodes):
            raise ValueError("Workflow must be an acyclic graph")
        if not self._reachable(starts[0], adjacency) == set(nodes):
            raise ValueError("Every workflow node must be reachable from StartNode")
        if adjacency[ends[0]]:
            raise ValueError("EndNode cannot have outgoing edges")

    @staticmethod
    def _reachable(start: str, adjacency: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency[current])
        return seen
