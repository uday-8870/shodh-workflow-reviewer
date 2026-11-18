"""Comprehensive unit, trust, oracle, cache, finding-quality, and debugging regression tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shodh_reviewer.cache import ReviewCache
from shodh_reviewer.dsl import eval_expr, parse_condition
from shodh_reviewer.eval import run_all_evaluations
from shodh_reviewer.generate_dataset import build_all_bundles
from shodh_reviewer.model_client import LocalFakeModelClient, ModelConfig
from shodh_reviewer.oracle import independent_ticket_oracle
from shodh_reviewer.reviewer import review_bundle
from shodh_reviewer.schemas import ApprovalRecord, ApprovalStatus, ReviewStatus


class TestWorkflowReviewer(unittest.TestCase):
    def test_dsl_units_and_missing(self) -> None:
        expr_min = parse_condition("waiting_minutes >= 30")
        expr_sec = parse_condition("waiting_seconds >= 1800")
        expr_hr = parse_condition("waiting_hours >= 0.5")
        for val_min in (0, 29, 29.9, 30, 31):
            t = {"waiting_minutes": val_min}
            self.assertEqual(eval_expr(expr_min, t), eval_expr(expr_sec, t))
            self.assertEqual(eval_expr(expr_min, t), eval_expr(expr_hr, t))
        self.assertTrue(eval_expr(parse_condition("MISSING(category)"), {"category": None}))
        self.assertFalse(eval_expr(parse_condition("MISSING(category)"), {"category": "billing"}))
        # Real bug regression: != on missing field must match NOT (==)
        self.assertTrue(eval_expr(parse_condition('tier != "sandbox"'), {}))
        self.assertTrue(eval_expr(parse_condition('NOT (tier == "sandbox")'), {}))

    def test_model_failures_and_trust(self) -> None:
        bundles = {x["bundle"].bundle_id: x["bundle"] for x in build_all_bundles()}
        b = bundles["bundle_01_valid_approved_rev"]
        b.require_model_check = True

        for bad_mode in ("timeout", "malformed_json", "truncated_json", "invented_ref"):
            client = LocalFakeModelClient(mode=bad_mode)
            rep = review_bundle(b, model_client=client, model_config=ModelConfig(max_retries=1))
            self.assertEqual(rep.status, ReviewStatus.INCOMPLETE, f"Expected INCOMPLETE for {bad_mode}")
            self.assertEqual(rep.resource_use["retries"], 1 if bad_mode != "invented_ref" else 0)

    def test_cache_invalidation_on_approval_change(self) -> None:
        bundles = {x["bundle"].bundle_id: x["bundle"] for x in build_all_bundles()}
        b = bundles["bundle_01_valid_approved_rev"]
        cache = ReviewCache()

        rep1 = review_bundle(b, cache=cache)
        self.assertEqual(rep1.status, ReviewStatus.PASS)
        self.assertFalse(rep1.resource_use["cache_hit"])

        rep2 = review_bundle(b, cache=cache)
        self.assertTrue(rep2.resource_use["cache_hit"])

        b.approvals.append(
            ApprovalRecord("APR-NEW", "R3", "rev-2026-11", 2, ApprovalStatus.APPROVED, "REPLACE_CONDITION", new_condition="waiting_minutes >= 99")
        )
        rep3 = review_bundle(b, cache=cache)
        self.assertFalse(rep3.resource_use["cache_hit"])
        self.assertEqual(rep3.status, ReviewStatus.CHANGES_REQUIRED)

    def test_deliberate_fault_injection_empty_string_missing(self) -> None:
        """Deliberate Fault Injection: empty string category='' must behave identically to None."""
        self.assertEqual(independent_ticket_oracle({"category": ""}), "manual_review")
        self.assertTrue(eval_expr(parse_condition("MISSING(category)"), {"category": ""}))

    def test_regression_debug_ex1_not_equal_missing(self) -> None:
        """Real Bug Regression: bundle_11_heldout_valid_nested_exception must PASS."""
        bundles = {x["bundle"].bundle_id: x["bundle"] for x in build_all_bundles()}
        rep = review_bundle(bundles["bundle_11_heldout_valid_nested_exception"])
        self.assertEqual(rep.status, ReviewStatus.PASS)

    def test_full_14_bundle_evaluation_and_finding_quality(self) -> None:
        root = Path(__file__).resolve().parents[1]
        summary = run_all_evaluations(root)
        final = summary["final"]
        self.assertEqual(final["exact_accuracy"], "14/14")
        self.assertEqual(final["heldout_accuracy"], "4/4")
        self.assertEqual(final["false_passes_on_defective"], "0/7")
        self.assertEqual(final["false_blocks_on_valid"], "0/4")
        self.assertEqual(final["incomplete_correct"], "3/3")
        self.assertEqual(final["finding_quality"]["defective_with_counterexample"], "7/7")
        self.assertEqual(final["finding_quality"]["defective_with_evidence_and_location"], "7/7")
        self.assertEqual(final["finding_quality"]["defective_with_correction"], "7/7")
        self.assertEqual(final["finding_quality"]["unsupported_or_invented_findings"], 0)

        # Verify checkpoint_v0 and ablation differ from final
        self.assertNotEqual(summary["checkpoint_v0"]["exact_accuracy"], "14/14")
        self.assertNotEqual(summary["ablation_no_exhaustive_equiv"]["exact_accuracy"], "14/14")

        # Verify per-bundle finding quality on every defective bundle
        per_bundle = json.loads((root / "reports" / "per_bundle_findings.json").read_text(encoding="utf-8"))
        for r in final["per_bundle"]:
            if r["expected"] == "CHANGES_REQUIRED":
                rep = per_bundle[r["bundle_id"]]
                blockers = [f for f in rep["findings"] if f["severity"] == "BLOCKER"]
                self.assertTrue(len(blockers) >= 1, f"Missing blocker on {r['bundle_id']}")
                self.assertTrue(any(f["counterexample"] is not None for f in blockers), f"Missing counterexample on {r['bundle_id']}")
                self.assertTrue(all(f["rule_ref"] and f["location"] and f["correction"] for f in blockers), f"Incomplete finding fields on {r['bundle_id']}")


if __name__ == "__main__":
    unittest.main()
