from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ..schemas import WorkflowDefinition, WorkflowEdge, WorkflowNode
from .output_utils import output_text


class WorkflowValidationError(ValueError):
    pass


def validate_workflow(workflow: WorkflowDefinition) -> None:
    node_ids = {node.id for node in workflow.nodes}
    if len(node_ids) != len(workflow.nodes):
        raise WorkflowValidationError("duplicate node id found")
    for edge in workflow.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise WorkflowValidationError(f"edge {edge.id} references unknown node")
    for entry in workflow.entry_nodes:
        if entry not in node_ids:
            raise WorkflowValidationError(f"entry node '{entry}' does not exist")

    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
    continuous = str(simulation.get("execution_model", "")).strip().lower() == "continuous"
    dynamic_config = simulation.get("dynamic_state", {})
    dynamic_enabled = not isinstance(dynamic_config, dict) or bool(dynamic_config.get("enabled", True))
    if continuous and dynamic_enabled and not any(node.type == "director" for node in workflow.nodes):
        raise WorkflowValidationError("continuous dynamic state requires a Director node")
    if not continuous:
        topological_sort(workflow.nodes, workflow.edges)


def topological_sort(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> list[str]:
    node_ids = [node.id for node in nodes]
    indegree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1

    queue = deque(node_id for node_id in node_ids if indegree[node_id] == 0)
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(ordered) != len(node_ids):
        raise WorkflowValidationError("workflow contains cycle")
    return ordered


def should_follow_condition(edge: WorkflowEdge, source_output: dict[str, Any]) -> bool:
    if edge.condition is None:
        return True
    expected = str(edge.condition).strip().lower() in {"true", "1", "yes", "y"}
    return bool(source_output.get("result", False)) == expected


def build_interaction_message(edge: WorkflowEdge, source_output: dict[str, Any]) -> dict[str, Any]:
    interaction = edge.interaction
    mode = interaction.mode if interaction else "dialogue"
    relation = interaction.relation if interaction else "peer"
    template = interaction.template if interaction else None
    intensity = interaction.intensity if interaction else 1.0

    readable_output = output_text(source_output) or str(
        source_output.get("global_guidance") or source_output.get("response") or source_output.get("result") or ""
    ).strip()
    if not readable_output:
        readable_output = "Output completed."

    if template:
        message = (
            template.replace("{{source}}", edge.source)
            .replace("{{target}}", edge.target)
            .replace("{{source_output}}", readable_output)
        )
    else:
        labels = {
            "report": f"[REPORT] {edge.source} -> {edge.target}: {readable_output}",
            "instruction": f"[INSTRUCTION] {edge.source} requests action from {edge.target}: {readable_output}",
            "feedback": f"[FEEDBACK] {edge.source} feedback to {edge.target}: {readable_output}",
            "handoff": f"[HANDOFF] {edge.source} hands work to {edge.target}: {readable_output}",
        }
        message = labels.get(mode, f"[DIALOGUE] {edge.source} to {edge.target}: {readable_output}")

    return {
        "source": edge.source,
        "target": edge.target,
        "mode": mode,
        "relation": relation,
        "message": message,
        "intensity": intensity,
        "payload": source_output,
    }


def descendants(start: str, edges: list[WorkflowEdge]) -> set[str]:
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        graph[edge.source].append(edge.target)
    queue = deque([start])
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(graph[current])
    return seen
