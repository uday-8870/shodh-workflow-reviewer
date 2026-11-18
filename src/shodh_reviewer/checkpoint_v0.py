"""Preserved initial working checkpoint (v0) before exhaustive equivalence,
unit normalization, approval conflict detection, and trust/injection hardening.

Kept intact for Stage 4 checkpoint-vs-final comparison on the frozen 14-bundle set.
"""

from __future__ import annotations

import time
from typing import List

from .python_analyzer import analyze_python_snippet
from .schemas import (
    ApprovalStatus,
    CheckResult,
    CoverageReport,
    Finding,
    ReviewBundle,
    ReviewReport,
    ReviewStatus,
    Severity,
)


def review_bundle_v0_checkpoint(bundle: ReviewBundle) -> ReviewReport:
    """Initial v0 checkpoint: basic rule-ID + normalized-string matching and Python syntax check."""
    t0 = time.perf_counter()
    findings: List[Finding] = []
    checks: List[CheckResult] = []

    # v0 naive effective rules: applies APPROVED changes without conflict detection
    eff_conds = {r.rule_id: (r.condition or "").strip().lower() for r in bundle.source_requirements}
    for app in bundle.approvals:
        if app.revision_scope == bundle.revision_scope and app.status == ApprovalStatus.APPROVED:
            if app.action == "REPLACE_CONDITION" and app.new_condition:
                eff_conds[app.target_rule_id] = app.new_condition.strip().lower()

    # v0 checks only whether each source rule_id appears in spec and matches exact lowercase string
    spec_by_ref = {(sr.source_ref or sr.rule_id): sr for sr in bundle.structured_spec.rules}
    for r in bundle.source_requirements:
        if not r.supported:
            continue
        sr = spec_by_ref.get(r.rule_id)
        if sr is None:
            findings.append(
                Finding(
                    finding_id=f"V0-OMIT-{r.rule_id}",
                    severity=Severity.BLOCKER,
                    category="omitted_requirement",
                    message=f"Rule {r.rule_id} missing from structured_spec.",
                    rule_ref=r.rule_id,
                    artifact="structured_spec",
                    version=bundle.structured_spec.version,
                    location=r.location,
                    evidence="Missing source_ref in spec.",
                    correction=f"Add {r.rule_id} to structured_spec.",
                )
            )
        elif (sr.condition or "").strip().lower() != eff_conds.get(r.rule_id, ""):
            findings.append(
                Finding(
                    finding_id=f"V0-STR-DIFF-{r.rule_id}",
                    severity=Severity.BLOCKER,
                    category="string_mismatch",
                    message=f"Rule {r.rule_id} condition string differs from spec.",
                    rule_ref=r.rule_id,
                    artifact="structured_spec",
                    version=bundle.structured_spec.version,
                    location=sr.location,
                    evidence=f"expected='{eff_conds.get(r.rule_id)}' vs spec='{sr.condition}'",
                    correction="Align spec condition string.",
                )
            )

    # v0 Python check: syntax & restricted subset only (no semantic comparison)
    unsupported = []
    unreviewed = []
    for snip in bundle.python_snippets:
        res = analyze_python_snippet(snip.snippet_id, snip.code, snip.function_name)
        if not res.supported:
            reason = "; ".join(res.unsupported_reasons)
            unsupported.append({"artifact": "python_snippets", "id": snip.snippet_id, "location": snip.location, "reason": reason})
            unreviewed.append(f"python_snippets:{snip.snippet_id}")

    # v0 does not run model_client -> if require_model_check is True, marks incomplete
    if bundle.require_model_check:
        checks.append(CheckResult("v0_model_check", False, "SKIPPED", "v0 checkpoint has no model client."))
    checks.append(CheckResult("v0_basic_check", len(findings) == 0, "PASSED" if not findings else "FAILED", "v0 ID + string match."))

    has_blocker = any(f.severity == Severity.BLOCKER for f in findings)
    if has_blocker:
        status = ReviewStatus.CHANGES_REQUIRED
        summary = f"v0 CHANGES_REQUIRED: {len(findings)} basic finding(s)."
    elif unsupported or bundle.require_model_check:
        status = ReviewStatus.INCOMPLETE
        summary = "v0 INCOMPLETE: unsupported code or missing model check."
    else:
        status = ReviewStatus.PASS
        summary = "v0 PASS: basic ID and string checks passed."

    return ReviewReport(
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        status=status,
        summary=summary,
        findings=findings,
        coverage=CoverageReport(
            total_source_rules=len(bundle.source_requirements),
            reviewed_source_rules=len(bundle.source_requirements) - len(unsupported),
            unsupported_items=unsupported,
            unreviewed_scope=unreviewed,
            is_complete=(len(unsupported) == 0),
        ),
        check_results=checks,
        effective_requirements=[],
        model_provenance={"client": None, "synthetic": False},
        resource_use={"runtime_ms": round((time.perf_counter() - t0) * 1000.0, 3), "model_calls": 0, "retries": 0, "cache_hit": False},
    )
