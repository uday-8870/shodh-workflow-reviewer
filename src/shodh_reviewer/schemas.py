"""Stage 1: Input contracts and schemas for the AI-assisted workflow reviewer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ReviewStatus(str, Enum):
    PASS = "PASS"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    INCOMPLETE = "INCOMPLETE"


class Severity(str, Enum):
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


class ApprovalStatus(str, Enum):
    APPROVED = "APPROVED"
    UNAPPROVED = "UNAPPROVED"
    REJECTED = "REJECTED"
    PROPOSED = "PROPOSED"


@dataclass(frozen=True)
class ArtifactLocation:
    artifact: str
    version: str
    location: str


@dataclass
class SourceRequirement:
    rule_id: str
    version: str
    location: str
    text: str
    order_index: int
    condition: Optional[str] = None
    destination: Optional[str] = None
    is_default: bool = False
    supported: bool = True
    unsupported_reason: Optional[str] = None


@dataclass
class StructuredSpecRule:
    rule_id: str
    version: str
    location: str
    order_index: int
    condition: Optional[str]
    destination: str
    source_ref: Optional[str] = None
    is_default: bool = False
    supported: bool = True
    unsupported_reason: Optional[str] = None


@dataclass
class StructuredSpec:
    spec_id: str
    version: str
    rules: List[StructuredSpecRule]
    default_route: Optional[str] = None


@dataclass
class WorkflowBranch:
    branch_id: str
    order_index: int
    condition: Optional[str]
    target_node: Optional[str] = None
    route: Optional[str] = None
    is_default: bool = False
    location: str = ""
    rule_ref: Optional[str] = None
    linked_snippet_id: Optional[str] = None


@dataclass
class WorkflowNode:
    node_id: str
    version: str
    location: str
    branches: List[WorkflowBranch] = field(default_factory=list)
    terminal_route: Optional[str] = None


@dataclass
class WorkflowGraph:
    graph_id: str
    version: str
    entry_node: str
    nodes: Dict[str, WorkflowNode]


@dataclass
class PythonSnippet:
    snippet_id: str
    version: str
    location: str
    function_name: str
    code: str
    linked_rule_ids: List[str] = field(default_factory=list)


@dataclass
class ApprovalRecord:
    approval_id: str
    target_rule_id: str
    revision_scope: str
    change_order: int
    status: ApprovalStatus
    action: str  # REPLACE_CONDITION, REPLACE_DESTINATION, ADD_RULE, REMOVE_RULE
    new_condition: Optional[str] = None
    new_destination: Optional[str] = None
    approver: str = ""
    timestamp: str = ""
    location: str = ""


@dataclass
class ReviewBundle:
    bundle_id: str
    bundle_version: str
    revision_scope: str
    source_requirements: List[SourceRequirement]
    structured_spec: StructuredSpec
    workflow_graph: WorkflowGraph
    python_snippets: List[PythonSnippet] = field(default_factory=list)
    approvals: List[ApprovalRecord] = field(default_factory=list)
    prose_notes: List[str] = field(default_factory=list)
    require_model_check: bool = False


@dataclass
class Counterexample:
    ticket: Dict[str, Any]
    expected_route: Optional[str]
    actual_route: Optional[str]
    artifact: str
    details: str


@dataclass
class Finding:
    finding_id: str
    severity: Severity
    category: str
    message: str
    rule_ref: Optional[str]
    artifact: str
    version: str
    location: str
    evidence: str
    correction: str
    counterexample: Optional[Counterexample] = None


@dataclass
class CoverageReport:
    total_source_rules: int
    reviewed_source_rules: int
    unsupported_items: List[Dict[str, str]] = field(default_factory=list)
    unreviewed_scope: List[str] = field(default_factory=list)
    is_complete: bool = True


@dataclass
class CheckResult:
    check_name: str
    passed: bool
    status: str  # PASSED, FAILED, SKIPPED, ERROR
    details: str


@dataclass
class ReviewReport:
    bundle_id: str
    bundle_version: str
    status: ReviewStatus
    summary: str
    findings: List[Finding]
    coverage: CoverageReport
    check_results: List[CheckResult]
    effective_requirements: List[Dict[str, Any]]
    model_provenance: Optional[Dict[str, Any]] = None
    resource_use: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "status": self.status.value,
            "summary": self.summary,
            "findings": [
                {
                    "finding_id": f.finding_id,
                    "severity": f.severity.value,
                    "category": f.category,
                    "message": f.message,
                    "rule_ref": f.rule_ref,
                    "artifact": f.artifact,
                    "version": f.version,
                    "location": f.location,
                    "evidence": f.evidence,
                    "correction": f.correction,
                    "counterexample": (
                        {
                            "ticket": f.counterexample.ticket,
                            "expected_route": f.counterexample.expected_route,
                            "actual_route": f.counterexample.actual_route,
                            "artifact": f.counterexample.artifact,
                            "details": f.counterexample.details,
                        }
                        if f.counterexample
                        else None
                    ),
                }
                for f in self.findings
            ],
            "coverage": {
                "total_source_rules": self.coverage.total_source_rules,
                "reviewed_source_rules": self.coverage.reviewed_source_rules,
                "unsupported_items": self.coverage.unsupported_items,
                "unreviewed_scope": self.coverage.unreviewed_scope,
                "is_complete": self.coverage.is_complete,
            },
            "check_results": [
                {
                    "check_name": c.check_name,
                    "passed": c.passed,
                    "status": c.status,
                    "details": c.details,
                }
                for c in self.check_results
            ],
            "effective_requirements": self.effective_requirements,
            "model_provenance": self.model_provenance,
            "resource_use": self.resource_use,
        }
