"""Stage 1: Effective requirements derivation and approval conflict detection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

from .schemas import (
    ApprovalRecord,
    ApprovalStatus,
    Finding,
    ReviewBundle,
    Severity,
    SourceRequirement,
)


def derive_effective_requirements(
    bundle: ReviewBundle,
) -> Tuple[List[SourceRequirement], List[Finding], bool]:
    """Apply approved changes within revision_scope in change_order.

    Returns:
        (effective_rules, findings, has_contradiction)
    """
    rules: List[SourceRequirement] = [deepcopy(r) for r in sorted(bundle.source_requirements, key=lambda x: x.order_index)]
    findings: List[Finding] = []
    has_contradiction = False

    # Filter approvals applicable to the bundle's revision_scope
    scoped_approvals = [a for a in bundle.approvals if a.revision_scope == bundle.revision_scope]

    # Separate approved vs non-approved
    approved = [a for a in scoped_approvals if a.status == ApprovalStatus.APPROVED]
    unapproved = [a for a in scoped_approvals if a.status != ApprovalStatus.APPROVED]

    for ua in unapproved:
        findings.append(
            Finding(
                finding_id=f"APP-IGNORED-{ua.approval_id}",
                severity=Severity.INFO,
                category="unapproved_change_ignored",
                message=f"Ignored {ua.status.value} change '{ua.approval_id}' targeting {ua.target_rule_id}.",
                rule_ref=ua.target_rule_id,
                artifact="approvals",
                version=bundle.revision_scope,
                location=ua.location or f"approvals:{ua.approval_id}",
                evidence=f"Approval status is {ua.status.value} (approver={ua.approver or 'none'}).",
                correction="Do not apply unapproved changes to workflow or specification.",
            )
        )

    # Detect contradictory APPROVED records:
    # Two approved records for the same target_rule_id with the same change_order
    # OR two approved records for the same target_rule_id and action that disagree without clear superseding order,
    # OR same change_order across different approvals for the same rule.
    by_rule: Dict[str, List[ApprovalRecord]] = {}
    for app in approved:
        by_rule.setdefault(app.target_rule_id, []).append(app)

    for rule_id, app_list in by_rule.items():
        seen_orders: Dict[int, ApprovalRecord] = {}
        for app in app_list:
            if app.change_order in seen_orders:
                other = seen_orders[app.change_order]
                if (
                    app.action != other.action
                    or app.new_condition != other.new_condition
                    or app.new_destination != other.new_destination
                ):
                    has_contradiction = True
                    findings.append(
                        Finding(
                            finding_id=f"APP-CONFLICT-{rule_id}-{app.change_order}",
                            severity=Severity.BLOCKER,
                            category="contradictory_approval",
                            message=(
                                f"Contradictory APPROVED records for {rule_id} at change_order={app.change_order}: "
                                f"'{other.approval_id}' vs '{app.approval_id}'."
                            ),
                            rule_ref=rule_id,
                            artifact="approvals",
                            version=bundle.revision_scope,
                            location=f"{other.location or other.approval_id} | {app.location or app.approval_id}",
                            evidence=(
                                f"{other.approval_id} ({other.new_condition}/{other.new_destination}) conflicts with "
                                f"{app.approval_id} ({app.new_condition}/{app.new_destination})."
                            ),
                            correction="Resolve the contradictory approval records with distinct change_order or single authoritative decision.",
                        )
                    )
            else:
                seen_orders[app.change_order] = app

        # Also flag conflicting approved values at different orders if they have identical timestamps or conflicting approvers
        if len(app_list) > 1 and not has_contradiction:
            # Check if timestamps are missing/identical and actions conflict
            actions_vals = {(a.action, a.new_condition, a.new_destination) for a in app_list}
            if len(actions_vals) > 1 and all(a.change_order == app_list[0].change_order for a in app_list):
                has_contradiction = True

    if has_contradiction:
        return rules, findings, True

    # Apply approved changes in ascending change_order
    for app in sorted(approved, key=lambda a: a.change_order):
        matched = [r for r in rules if r.rule_id == app.target_rule_id]
        if app.action == "REPLACE_CONDITION" and matched:
            for r in matched:
                r.condition = app.new_condition
                r.version = f"{r.version}+{app.approval_id}"
        elif app.action == "REPLACE_DESTINATION" and matched:
            for r in matched:
                r.destination = app.new_destination
                r.version = f"{r.version}+{app.approval_id}"
        elif app.action == "REMOVE_RULE" and matched:
            rules = [r for r in rules if r.rule_id != app.target_rule_id]
        elif app.action == "ADD_RULE":
            new_order = max((r.order_index for r in rules if not r.is_default), default=0) + 1
            rules.append(
                SourceRequirement(
                    rule_id=app.target_rule_id,
                    version=app.revision_scope,
                    location=app.location or f"approvals:{app.approval_id}",
                    text=f"Added via {app.approval_id}",
                    order_index=new_order,
                    condition=app.new_condition,
                    destination=app.new_destination,
                    is_default=False,
                    supported=True,
                )
            )
            rules.sort(key=lambda x: (x.is_default, x.order_index))

    return rules, findings, False


def serialize_effective_requirements(rules: List[SourceRequirement]) -> List[Dict[str, Any]]:
    return [
        {
            "rule_id": r.rule_id,
            "version": r.version,
            "location": r.location,
            "order_index": r.order_index,
            "condition": r.condition,
            "destination": r.destination,
            "is_default": r.is_default,
            "supported": r.supported,
            "unsupported_reason": r.unsupported_reason,
        }
        for r in rules
    ]
