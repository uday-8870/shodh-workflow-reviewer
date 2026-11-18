"""JSON serialization/deserialization for ReviewBundle."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from .schemas import (
    ApprovalRecord,
    ApprovalStatus,
    PythonSnippet,
    ReviewBundle,
    SourceRequirement,
    StructuredSpec,
    StructuredSpecRule,
    WorkflowBranch,
    WorkflowGraph,
    WorkflowNode,
)


def save_bundle_to_json(bundle: ReviewBundle, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(asdict(bundle), f, indent=2)


def load_bundle_from_json(path: str | Path) -> ReviewBundle:
    with Path(path).open("r", encoding="utf-8") as f:
        d: Dict[str, Any] = json.load(f)

    reqs = [SourceRequirement(**r) for r in d["source_requirements"]]
    spec_rules = [StructuredSpecRule(**sr) for sr in d["structured_spec"]["rules"]]
    spec = StructuredSpec(
        spec_id=d["structured_spec"]["spec_id"],
        version=d["structured_spec"]["version"],
        rules=spec_rules,
        default_route=d["structured_spec"].get("default_route"),
    )
    nodes: Dict[str, WorkflowNode] = {}
    for nid, nd in d["workflow_graph"]["nodes"].items():
        branches = [WorkflowBranch(**b) for b in nd.get("branches", [])]
        nodes[nid] = WorkflowNode(
            node_id=nd["node_id"],
            version=nd["version"],
            location=nd["location"],
            branches=branches,
            terminal_route=nd.get("terminal_route"),
        )
    graph = WorkflowGraph(
        graph_id=d["workflow_graph"]["graph_id"],
        version=d["workflow_graph"]["version"],
        entry_node=d["workflow_graph"]["entry_node"],
        nodes=nodes,
    )
    snippets = [PythonSnippet(**s) for s in d.get("python_snippets", [])]
    approvals = [
        ApprovalRecord(
            approval_id=a["approval_id"],
            target_rule_id=a["target_rule_id"],
            revision_scope=a["revision_scope"],
            change_order=a["change_order"],
            status=ApprovalStatus(a["status"]),
            action=a["action"],
            new_condition=a.get("new_condition"),
            new_destination=a.get("new_destination"),
            approver=a.get("approver", ""),
            timestamp=a.get("timestamp", ""),
            location=a.get("location", ""),
        )
        for a in d.get("approvals", [])
    ]
    return ReviewBundle(
        bundle_id=d["bundle_id"],
        bundle_version=d["bundle_version"],
        revision_scope=d["revision_scope"],
        source_requirements=reqs,
        structured_spec=spec,
        workflow_graph=graph,
        python_snippets=snippets,
        approvals=approvals,
        prose_notes=d.get("prose_notes", []),
        require_model_check=d.get("require_model_check", False),
    )
