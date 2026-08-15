"""Phase 27 -- attack chain graph.

The graph is built deterministically from facts / events / assets / evidence
(temporal, same_asset, same_identity, network_flow, causes_candidate,
supports, contradicts). The LLM only analyses the graph to produce an
AttackChainHypothesis -- it never fabricates nodes or edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.hybrid.facts import SecurityFact

EDGE_KINDS = (
    "temporal",
    "same_asset",
    "same_identity",
    "network_flow",
    "causes_candidate",
    "supports",
    "contradicts",
)


@dataclass
class ChainNode:
    node_id: str
    kind: str  # security_fact | security_event | asset | evidence | technique_candidate
    label: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainEdge:
    source: str
    target: str
    kind: str
    weight: float = 1.0
    detail: str = ""


@dataclass
class AttackChainGraph:
    nodes: list[ChainNode] = field(default_factory=list)
    edges: list[ChainEdge] = field(default_factory=list)

    def add_node(self, node: ChainNode) -> None:
        if not any(existing.node_id == node.node_id for existing in self.nodes):
            self.nodes.append(node)

    def add_edge(self, edge: ChainEdge) -> None:
        if any(
            existing.source == edge.source
            and existing.target == edge.target
            and existing.kind == edge.kind
            for existing in self.edges
        ):
            return
        self.edges.append(edge)

    def neighbors(self, node_id: str) -> list[str]:
        result: list[str] = []
        for edge in self.edges:
            if edge.source == node_id:
                result.append(edge.target)
            if edge.target == node_id:
                result.append(edge.source)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [{"id": n.node_id, "kind": n.kind, "label": n.label} for n in self.nodes],
            "edges": [
                {"source": e.source, "target": e.target, "kind": e.kind, "weight": e.weight}
                for e in self.edges
            ],
        }


def _event_ts(event: dict[str, Any]) -> datetime:
    value = event.get("timestamp")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now()


class AttackChainBuilder:
    """Deterministic graph construction from platform inputs."""

    def build(
        self,
        *,
        events: list[dict[str, Any]],
        facts: list[SecurityFact] | None = None,
        assets: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        technique_candidates: list[str] | None = None,
    ) -> AttackChainGraph:
        graph = AttackChainGraph()

        # asset nodes
        for asset in assets or []:
            graph.add_node(
                ChainNode(
                    node_id=f"asset:{asset.get('id', '')}",
                    kind="asset",
                    label=str(asset.get("name") or asset.get("value") or "asset"),
                    facts={
                        "criticality": asset.get("criticality", ""),
                        "value": asset.get("value", ""),
                    },
                )
            )

        # event nodes + entity indices
        entity_to_events: dict[str, list[str]] = {}
        asset_of_event: dict[str, str] = {}
        sorted_events = sorted(events, key=_event_ts)
        for index, event in enumerate(sorted_events):
            event_id = f"event:{event.get('id', index)}"
            graph.add_node(
                ChainNode(
                    node_id=event_id,
                    kind="security_event",
                    label=str(event.get("title") or event.get("event_type") or event_id),
                    facts={
                        "timestamp": str(event.get("timestamp", "")),
                        "severity": event.get("severity", ""),
                        "techniques": event.get("techniques", []),
                    },
                )
            )
            for entity in event.get("entities", []) or []:
                entity_key = str(entity)
                entity_to_events.setdefault(entity_key, []).append(event_id)
            asset_id = event.get("asset_id")
            if asset_id:
                asset_of_event[event_id] = f"asset:{asset_id}"

        # temporal edges (strict ordering, capped to avoid O(n^2) blowup)
        for index in range(1, len(sorted_events)):
            previous = f"event:{sorted_events[index - 1].get('id', index - 1)}"
            current = f"event:{sorted_events[index].get('id', index)}"
            graph.add_edge(
                ChainEdge(previous, current, "temporal", weight=1.0, detail="timestamp order")
            )

        # same_identity edges
        for entity, ids in entity_to_events.items():
            for index in range(len(ids) - 1):
                graph.add_edge(
                    ChainEdge(ids[index], ids[index + 1], "same_identity", detail=entity)
                )

        # asset association edges
        for event_id, asset_id in asset_of_event.items():
            graph.add_edge(ChainEdge(event_id, asset_id, "same_asset", detail="on asset"))

        # fact nodes + supports edges
        for fact in facts or []:
            node_id = f"fact:{abs(hash(fact.value))}"
            graph.add_node(
                ChainNode(
                    node_id=node_id,
                    kind="security_fact",
                    label=fact.value[:64],
                    facts={
                        "fact_type": fact.fact_type,
                        "confidence": fact.confidence,
                    },
                )
            )
            for event in sorted_events:
                event_id = f"event:{event.get('id', '')}"
                entity_hit = any(
                    str(entity) in fact.value for entity in (event.get("entities", []) or [])
                )
                if entity_hit:
                    graph.add_edge(
                        ChainEdge(event_id, node_id, "supports", weight=0.8, detail="entity match")
                    )

        # technique candidate nodes
        for technique_id in technique_candidates or []:
            graph.add_node(
                ChainNode(
                    node_id=f"technique:{technique_id}",
                    kind="technique_candidate",
                    label=technique_id,
                )
            )
            for event in sorted_events:
                if technique_id in (event.get("techniques", []) or []):
                    event_id = f"event:{event.get('id', '')}"
                    graph.add_edge(
                        ChainEdge(
                            event_id, f"technique:{technique_id}", "supports", detail="declared"
                        )
                    )

        return graph


def order_stages_deterministically(graph: AttackChainGraph) -> list[str]:
    """Deterministic stage ordering: longest temporal path (simple heuristic).

    Returns technique-candidate node ids in causal-ish order. Pure rules;
    used when no LLM is present.
    """
    technique_nodes = [n for n in graph.nodes if n.kind == "technique_candidate"]
    if not technique_nodes:
        return []
    # simple topological-ish sort by node id order of appearance in edges
    ordered: list[str] = []
    for edge in graph.edges:
        if edge.source.startswith("event:") and edge.target.startswith("technique:"):
            if edge.target not in ordered:
                ordered.append(edge.target)
    for node in technique_nodes:
        if node.node_id not in ordered:
            ordered.append(node.node_id)
    return ordered
