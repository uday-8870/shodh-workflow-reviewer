"""Stage 2: Deterministic semantic review and exhaustive counterexample generator."""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Set, Tuple

from .dsl import (
    DSLParseError,
    Expr,
    canonicalize_field,
    collect_domain_hints,
    eval_expr,
    parse_condition,
)
from .python_analyzer import PythonAnalysisResult, analyze_python_snippet, eval_python_ast
from .schemas import (
    CheckResult,
    Counterexample,
    CoverageReport,
    Finding,
    ReviewBundle,
    Severity,
    SourceRequirement,
    StructuredSpecRule,
    WorkflowGraph,
)


def _eval_rule_list(
    rules: List[Tuple[str, Optional[Expr], str, bool]],
    ticket: Dict[str, Any],
    default_route: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Evaluate ordered (rule_id, parsed_expr, destination, is_default) on a ticket."""
    for rule_id, expr, dest, is_def in rules:
        if is_def or expr is None:
            return dest, rule_id
        if eval_expr(expr, ticket):
            return dest, rule_id
    return default_route, None


def _eval_workflow_graph(
    graph: WorkflowGraph,
    ticket: Dict[str, Any],
    parsed_branches: Dict[str, Optional[Expr]],
) -> Tuple[Optional[str], Optional[str], Set[str]]:
    """Trace workflow graph for a ticket. Returns (route, error_reason, visited_nodes)."""
    visited: Set[str] = set()
    curr = graph.entry_node
    steps = 0
    max_steps = len(graph.nodes) + 2

    while steps < max_steps:
        steps += 1
        if curr in visited:
            return None, f"Cycle detected at node '{curr}'", visited
        visited.add(curr)
        node = graph.nodes.get(curr)
        if node is None:
            return None, f"Missing target node '{curr}'", visited
        if node.terminal_route is not None and not node.branches:
            return node.terminal_route, None, visited

        matched_branch = None
        for br in sorted(node.branches, key=lambda b: b.order_index):
            if br.is_default or br.condition is None:
                matched_branch = br
                break
            expr = parsed_branches.get(br.branch_id)
            if expr is not None and eval_expr(expr, ticket):
                matched_branch = br
                break

        if matched_branch is None:
            if node.terminal_route is not None:
                return node.terminal_route, None, visited
            return None, f"Dead-end at node '{curr}': no branch matched", visited

        if matched_branch.route is not None:
            return matched_branch.route, None, visited
        if matched_branch.target_node is not None:
            curr = matched_branch.target_node
        else:
            return None, f"Branch '{matched_branch.branch_id}' has neither route nor target_node", visited

    return None, "Workflow exceeded max traversal depth", visited


def _generate_exhaustive_domain(
    fields: Set[str],
    numeric_bounds: Dict[str, Set[float]],
    string_values: Dict[str, Set[Any]],
) -> List[Dict[str, Any]]:
    """Generate exhaustive equivalence-class ticket inputs across all referenced fields."""
    if not fields:
        return [{}]

    per_field_vals: Dict[str, List[Any]] = {}
    for cf in sorted(fields):
        vals: List[Any] = [None]  # Always include missing value!
        if cf in numeric_bounds and numeric_bounds[cf]:
            pts: Set[float] = set()
            for b in numeric_bounds[cf]:
                pts.update([b - 60.0, b - 1.0, b, b + 1.0, b + 60.0])
                if b != int(b):
                    pts.update([b - 0.5, b + 0.5])
            vals.extend(sorted(p for p in pts if p >= 0))
        elif cf in string_values and string_values[cf]:
            svals = list(string_values[cf])
            # Add an unseen sentinel value to test negative/fallback branches
            if all(isinstance(v, str) for v in svals):
                svals.append("__other__")
            elif all(isinstance(v, bool) for v in svals):
                for bv in (True, False):
                    if bv not in svals:
                        svals.append(bv)
            vals.extend(svals)
        else:
            # Field only checked via MISSING(field) / PRESENT(field)
            vals.append("sample_value")
        per_field_vals[cf] = vals

    ordered_fields = sorted(per_field_vals.keys())
    # Cap combinatorial explosion only if > 5 fields by keeping tight boundary points
    total = 1
    for f in ordered_fields:
        total *= len(per_field_vals[f])
    if total > 4000:
        for f in ordered_fields:
            if len(per_field_vals[f]) > 5:
                # Keep None, min, mid, max
                arr = per_field_vals[f]
                per_field_vals[f] = [arr[0], arr[1], arr[len(arr) // 2], arr[-1]]

    tickets: List[Dict[str, Any]] = []
    for combo in itertools.product(*(per_field_vals[f] for f in ordered_fields)):
        t = dict(zip(ordered_fields, combo))
        tickets.append(t)
    return tickets


def run_semantic_review(
    bundle: ReviewBundle,
    effective_rules: List[SourceRequirement],
    use_exhaustive_equivalence: bool = True,
) -> Tuple[List[Finding], CoverageReport, List[CheckResult]]:
    """Run deterministic semantic checks across effective requirements, spec, workflow, and code."""
    findings: List[Finding] = []
    check_results: List[CheckResult] = []
    unsupported_items: List[Dict[str, str]] = []
    unreviewed_scope: List[str] = []

    numeric_bounds: Dict[str, Set[float]] = {}
    string_values: Dict[str, Set[Any]] = {}
    fields: Set[str] = set()

    # 1. Parse effective source requirements
    parsed_eff: List[Tuple[str, Optional[Expr], str, bool]] = []
    reviewed_source_count = 0
    for r in effective_rules:
        if not r.supported:
            unsupported_items.append(
                {
                    "artifact": "source_requirements",
                    "id": r.rule_id,
                    "location": r.location,
                    "reason": r.unsupported_reason or "Marked unsupported",
                }
            )
            unreviewed_scope.append(f"source_requirements:{r.rule_id} ({r.unsupported_reason or 'unsupported'})")
            continue
        if r.is_default or r.condition is None:
            parsed_eff.append((r.rule_id, None, r.destination or "", True))
            reviewed_source_count += 1
            continue
        try:
            expr = parse_condition(r.condition)
            collect_domain_hints(expr, numeric_bounds, string_values, fields)
            parsed_eff.append((r.rule_id, expr, r.destination or "", False))
            reviewed_source_count += 1
        except DSLParseError as exc:
            unsupported_items.append(
                {
                    "artifact": "source_requirements",
                    "id": r.rule_id,
                    "location": r.location,
                    "reason": str(exc),
                }
            )
            unreviewed_scope.append(f"source_requirements:{r.rule_id} ({exc})")

    # 2. Parse structured spec rules
    parsed_spec: List[Tuple[str, Optional[Expr], str, bool]] = []
    spec_by_source_ref: Dict[str, StructuredSpecRule] = {}
    for sr in sorted(bundle.structured_spec.rules, key=lambda x: (x.is_default, x.order_index)):
        if sr.source_ref:
            spec_by_source_ref[sr.source_ref] = sr
        if not sr.supported:
            unsupported_items.append(
                {
                    "artifact": "structured_spec",
                    "id": sr.rule_id,
                    "location": sr.location,
                    "reason": sr.unsupported_reason or "Marked unsupported",
                }
            )
            unreviewed_scope.append(f"structured_spec:{sr.rule_id}")
            continue
        if sr.is_default or sr.condition is None:
            parsed_spec.append((sr.rule_id, None, sr.destination, True))
            continue
        try:
            expr = parse_condition(sr.condition)
            collect_domain_hints(expr, numeric_bounds, string_values, fields)
            parsed_spec.append((sr.rule_id, expr, sr.destination, False))
        except DSLParseError as exc:
            unsupported_items.append(
                {
                    "artifact": "structured_spec",
                    "id": sr.rule_id,
                    "location": sr.location,
                    "reason": str(exc),
                }
            )
            unreviewed_scope.append(f"structured_spec:{sr.rule_id} ({exc})")

    # 3. Parse workflow graph branches & check structural reachability
    parsed_branches: Dict[str, Optional[Expr]] = {}
    for node_id, node in bundle.workflow_graph.nodes.items():
        for br in node.branches:
            if br.condition and not br.is_default:
                try:
                    expr = parse_condition(br.condition)
                    collect_domain_hints(expr, numeric_bounds, string_values, fields)
                    parsed_branches[br.branch_id] = expr
                except DSLParseError as exc:
                    unsupported_items.append(
                        {
                            "artifact": "workflow_graph",
                            "id": br.branch_id,
                            "location": br.location or node.location,
                            "reason": str(exc),
                        }
                    )
                    unreviewed_scope.append(f"workflow_graph:{br.branch_id} ({exc})")

    # 4. Analyze Python snippets (syntax + restricted subset)
    py_analyses: Dict[str, PythonAnalysisResult] = {}
    for snip in bundle.python_snippets:
        res = analyze_python_snippet(snip.snippet_id, snip.code, snip.function_name)
        py_analyses[snip.snippet_id] = res
        if not res.supported:
            reason = "; ".join(res.unsupported_reasons)
            unsupported_items.append(
                {
                    "artifact": "python_snippets",
                    "id": snip.snippet_id,
                    "location": snip.location,
                    "reason": reason,
                }
            )
            unreviewed_scope.append(f"python_snippets:{snip.snippet_id} ({reason})")

    # 5. Check for omitted source requirements in structured spec (even when IDs are absent)
    if not use_exhaustive_equivalence:
        # Ablation / baseline mode: naive ID + normalized string match only
        spec_ids = {sr.source_ref or sr.rule_id for sr in bundle.structured_spec.rules}
        for r in effective_rules:
            if r.rule_id not in spec_ids:
                findings.append(
                    Finding(
                        finding_id=f"OMIT-SPEC-{r.rule_id}",
                        severity=Severity.BLOCKER,
                        category="omitted_requirement",
                        message=f"Source requirement '{r.rule_id}' is missing from structured specification.",
                        rule_ref=r.rule_id,
                        artifact="structured_spec",
                        version=bundle.structured_spec.version,
                        location=r.location,
                        evidence=f"Rule {r.rule_id} not referenced in spec.",
                        correction=f"Add rule {r.rule_id} ({r.condition} -> {r.destination}) to structured_spec.",
                    )
                )
        check_results.append(
            CheckResult(
                check_name="semantic_equivalence_check",
                passed=len(findings) == 0,
                status="PASSED" if len(findings) == 0 else "FAILED",
                details="Naive ID/string check only (exhaustive equivalence disabled).",
            )
        )
        cov = CoverageReport(
            total_source_rules=len(effective_rules),
            reviewed_source_rules=reviewed_source_count,
            unsupported_items=unsupported_items,
            unreviewed_scope=unreviewed_scope,
            is_complete=(len(unsupported_items) == 0),
        )
        return findings, cov, check_results

    # Exhaustive domain evaluation!
    tickets = _generate_exhaustive_domain(fields, numeric_bounds, string_values)

    # Compare Effective Requirements vs Structured Spec
    spec_mismatch_cx: Optional[Counterexample] = None
    spec_mismatch_rule: Optional[str] = None
    for t in tickets:
        exp_route, exp_rule = _eval_rule_list(parsed_eff, t)
        act_route, act_rule = _eval_rule_list(parsed_spec, t, bundle.structured_spec.default_route)
        if exp_route != act_route:
            spec_mismatch_rule = exp_rule
            spec_mismatch_cx = Counterexample(
                ticket=t,
                expected_route=exp_route,
                actual_route=act_route,
                artifact="structured_spec",
                details=f"Effective rule '{exp_rule}' routes to '{exp_route}', but spec rule '{act_rule}' routes to '{act_route}'.",
            )
            break

    if spec_mismatch_cx:
        # Diagnose specific cause (omitted rule, threshold, precedence, destination)
        eff_dests = {r.destination for r in effective_rules if r.destination}
        spec_dests = {sr.destination for sr in bundle.structured_spec.rules}
        missing_dests = eff_dests - spec_dests
        cat = "omitted_requirement" if missing_dests else "spec_semantic_mismatch"
        findings.append(
            Finding(
                finding_id="SEM-SPEC-MISMATCH",
                severity=Severity.BLOCKER,
                category=cat,
                message=f"Structured specification disagrees with effective requirements: {spec_mismatch_cx.details}",
                rule_ref=spec_mismatch_rule,
                artifact="structured_spec",
                version=bundle.structured_spec.version,
                location=f"spec:{bundle.structured_spec.spec_id}",
                evidence=f"Counterexample ticket {spec_mismatch_cx.ticket} -> expected '{spec_mismatch_cx.expected_route}', got '{spec_mismatch_cx.actual_route}'.",
                correction="Align structured_spec conditions, thresholds, units, and rule ordering with effective requirements.",
                counterexample=spec_mismatch_cx,
            )
        )

    # Compare Effective Requirements vs Workflow Graph
    wf_mismatch_cx: Optional[Counterexample] = None
    wf_mismatch_rule: Optional[str] = None
    visited_all: Set[str] = set()
    for t in tickets:
        exp_route, exp_rule = _eval_rule_list(parsed_eff, t)
        wf_route, wf_err, visited = _eval_workflow_graph(bundle.workflow_graph, t, parsed_branches)
        visited_all.update(visited)
        if wf_err is not None or wf_route != exp_route:
            wf_mismatch_rule = exp_rule
            wf_mismatch_cx = Counterexample(
                ticket=t,
                expected_route=exp_route,
                actual_route=wf_route or f"ERROR:{wf_err}",
                artifact="workflow_graph",
                details=wf_err or f"Effective rule '{exp_rule}' routes to '{exp_route}', but workflow routes to '{wf_route}'.",
            )
            break

    if wf_mismatch_cx:
        findings.append(
            Finding(
                finding_id="SEM-WF-MISMATCH",
                severity=Severity.BLOCKER,
                category="workflow_semantic_mismatch",
                message=f"Workflow graph disagrees with effective requirements: {wf_mismatch_cx.details}",
                rule_ref=wf_mismatch_rule,
                artifact="workflow_graph",
                version=bundle.workflow_graph.version,
                location=f"graph:{bundle.workflow_graph.graph_id}",
                evidence=f"Counterexample ticket {wf_mismatch_cx.ticket} -> expected '{wf_mismatch_cx.expected_route}', got '{wf_mismatch_cx.actual_route}'.",
                correction="Fix workflow branch conditions, thresholds, ordering, or missing destination branches.",
                counterexample=wf_mismatch_cx,
            )
        )

    # Check unreachable nodes in workflow graph
    unreachable = set(bundle.workflow_graph.nodes.keys()) - visited_all
    for unode in sorted(unreachable):
        node = bundle.workflow_graph.nodes[unode]
        findings.append(
            Finding(
                finding_id=f"WF-UNREACHABLE-{unode}",
                severity=Severity.WARNING,
                category="unreachable_workflow_node",
                message=f"Workflow node '{unode}' is unreachable from entry_node '{bundle.workflow_graph.entry_node}'.",
                rule_ref=None,
                artifact="workflow_graph",
                version=node.version,
                location=node.location,
                evidence=f"Exhaustive domain traversal visited {sorted(visited_all)}, never reaching '{unode}'.",
                correction=f"Connect '{unode}' from an active branch or remove dead node.",
            )
        )

    # Compare Effective Requirements vs Supported Python Snippets
    for snip in bundle.python_snippets:
        analysis = py_analyses[snip.snippet_id]
        if not analysis.supported or analysis.function_node is None:
            continue
        py_cx: Optional[Counterexample] = None
        for t in tickets:
            exp_route, exp_rule = _eval_rule_list(parsed_eff, t)
            py_route = eval_python_ast(analysis.function_node, t)
            if py_route != exp_route:
                py_cx = Counterexample(
                    ticket=t,
                    expected_route=exp_route,
                    actual_route=py_route,
                    artifact=f"python_snippets:{snip.snippet_id}",
                    details=f"Effective rule '{exp_rule}' routes to '{exp_route}', but Python '{snip.function_name}' returns '{py_route}'.",
                )
                break
        if py_cx:
            findings.append(
                Finding(
                    finding_id=f"SEM-PY-MISMATCH-{snip.snippet_id}",
                    severity=Severity.BLOCKER,
                    category="code_disagreement",
                    message=f"Python snippet '{snip.snippet_id}' disagrees with effective requirements: {py_cx.details}",
                    rule_ref=snip.linked_rule_ids[0] if snip.linked_rule_ids else None,
                    artifact="python_snippets",
                    version=snip.version,
                    location=snip.location,
                    evidence=f"Counterexample ticket {py_cx.ticket} -> expected '{py_cx.expected_route}', got '{py_cx.actual_route}'.",
                    correction=f"Update '{snip.function_name}' in {snip.location} to match effective policy thresholds, units, and precedence.",
                    counterexample=py_cx,
                )
            )

    check_results.append(
        CheckResult(
            check_name="exhaustive_semantic_equivalence",
            passed=(spec_mismatch_cx is None and wf_mismatch_cx is None and not any(f.category == "code_disagreement" for f in findings)),
            status="PASSED" if (spec_mismatch_cx is None and wf_mismatch_cx is None and not any(f.category == "code_disagreement" for f in findings)) else "FAILED",
            details=f"Evaluated {len(tickets)} equivalence-class ticket inputs across effective requirements, spec, workflow, and supported Python snippets.",
        )
    )

    cov = CoverageReport(
        total_source_rules=len(effective_rules),
        reviewed_source_rules=reviewed_source_count,
        unsupported_items=unsupported_items,
        unreviewed_scope=unreviewed_scope,
        is_complete=(len(unsupported_items) == 0),
    )
    return findings, cov, check_results
