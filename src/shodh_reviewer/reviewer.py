"""Main reviewer orchestrator combining Stages 1, 2, and 3, plus CLI."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict, List, Optional

from .cache import ReviewCache, compute_bundle_cache_key
from .effective import derive_effective_requirements, serialize_effective_requirements
from .model_client import LocalFakeModelClient, ModelClient, ModelConfig
from .schemas import (
    CheckResult,
    CoverageReport,
    Finding,
    ReviewBundle,
    ReviewReport,
    ReviewStatus,
    Severity,
)
from .semantic_checker import run_semantic_review
from .trust_validator import detect_untrusted_injections, run_model_check_with_trust


def review_bundle(
    bundle: ReviewBundle,
    model_client: Optional[ModelClient] = None,
    model_config: Optional[ModelConfig] = None,
    cache: Optional[ReviewCache] = None,
    use_exhaustive_equivalence: bool = True,
    use_trust_validation: bool = True,
    mode_label: str = "final",
) -> ReviewReport:
    """Run full 3-stage review on a ReviewBundle and return a structured ReviewReport."""
    t0 = time.perf_counter()
    cfg = model_config or ModelConfig()

    cache_key = compute_bundle_cache_key(bundle, cfg, f"{mode_label}:{use_exhaustive_equivalence}:{use_trust_validation}")
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            cached.resource_use["cache_hit"] = True
            return cached

    all_findings: List[Finding] = []
    check_results: List[CheckResult] = []

    # Stage 1: Effective requirements & approval conflict check
    eff_rules, eff_findings, has_contradiction = derive_effective_requirements(bundle)
    all_findings.extend(eff_findings)
    check_results.append(
        CheckResult(
            check_name="approval_consistency_check",
            passed=not has_contradiction,
            status="PASSED" if not has_contradiction else "FAILED",
            details=(
                "All applicable approvals consistent."
                if not has_contradiction
                else "Contradictory APPROVED records detected."
            ),
        )
    )

    # Stage 2: Semantic review
    sem_findings, coverage, sem_checks = run_semantic_review(
        bundle,
        eff_rules,
        use_exhaustive_equivalence=use_exhaustive_equivalence,
    )
    all_findings.extend(sem_findings)
    check_results.extend(sem_checks)

    # Stage 3a: Injection / untrusted prose & comment detection
    if use_trust_validation:
        inj_findings = detect_untrusted_injections(bundle)
        all_findings.extend(inj_findings)

    det_has_blocker = any(f.severity == Severity.BLOCKER for f in all_findings)

    # Stage 3b: Optional/Required model check with trust validation
    model_findings, model_check, model_prov, calls_made, retries_made = run_model_check_with_trust(
        bundle,
        model_client,
        cfg,
        deterministic_has_blocker=det_has_blocker,
    )
    all_findings.extend(model_findings)
    check_results.append(model_check)

    # Determine final status: PASS | CHANGES_REQUIRED | INCOMPLETE
    has_blocker = any(f.severity == Severity.BLOCKER and f.category != "contradictory_approval" for f in all_findings)
    has_failed_required_check = any(not c.passed for c in check_results if c.check_name != "exhaustive_semantic_equivalence")

    if has_contradiction:
        # Contradictory approvals prevent establishing effective policy -> INCOMPLETE
        status = ReviewStatus.INCOMPLETE
        summary = "INCOMPLETE: Contradictory approval records prevent establishing effective requirements."
    elif has_blocker:
        status = ReviewStatus.CHANGES_REQUIRED
        blocker_count = sum(1 for f in all_findings if f.severity == Severity.BLOCKER)
        summary = f"CHANGES_REQUIRED: {blocker_count} evidenced blocker(s) found."
        if not coverage.is_complete:
            summary += f" ({len(coverage.unreviewed_scope)} unreviewed item(s) disclosed)."
    elif not coverage.is_complete or has_failed_required_check:
        status = ReviewStatus.INCOMPLETE
        summary = "INCOMPLETE: Unsupported scope, missing evidence, or failed required check prevents full review."
    else:
        status = ReviewStatus.PASS
        summary = f"PASS: All {coverage.reviewed_source_rules}/{coverage.total_source_rules} effective requirements verified."

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    report = ReviewReport(
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        status=status,
        summary=summary,
        findings=all_findings,
        coverage=coverage,
        check_results=check_results,
        effective_requirements=serialize_effective_requirements(eff_rules),
        model_provenance=model_prov,
        resource_use={
            "runtime_ms": elapsed_ms,
            "model_calls": calls_made,
            "retries": retries_made,
            "cache_hit": False,
        },
    )

    if cache is not None:
        cache.put(cache_key, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Shodh AI-Assisted Workflow Reviewer CLI")
    parser.add_argument("--bundle", type=str, help="Path to a JSON review bundle", required=False)
    parser.add_argument("--model-mode", type=str, default="normal", help="LocalFakeModelClient mode")
    args = parser.parse_args()

    if not args.bundle:
        print("Use `python3 -m shodh_reviewer.eval` to run the full evaluation suite, or pass --bundle <path>.")
        return

    from .bundle_io import load_bundle_from_json

    bundle = load_bundle_from_json(args.bundle)
    client = LocalFakeModelClient(mode=args.model_mode)
    report = review_bundle(bundle, model_client=client)
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
