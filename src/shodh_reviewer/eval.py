"""Stage 4: Controlled evaluation comparing v0 checkpoint, ablation, and final reviewer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .bundle_io import load_bundle_from_json
from .checkpoint_v0 import review_bundle_v0_checkpoint
from .generate_dataset import write_dataset
from .model_client import LocalFakeModelClient, ModelConfig
from .reviewer import review_bundle


def evaluate_mode(
    root: Path,
    labels: Dict[str, Dict[str, Any]],
    mode_name: str,
    use_exhaustive: bool = True,
    use_trust: bool = True,
    use_v0_checkpoint: bool = False,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    all_reports: Dict[str, Any] = {}

    defective_total = 0
    false_passes = 0
    valid_total = 0
    false_blocks = 0
    incomplete_total = 0
    incomplete_correct = 0

    defective_with_cx = 0
    defective_with_evidence_loc = 0
    defective_with_correction = 0
    unsupported_findings = 0

    total_runtime_ms = 0.0
    total_calls = 0
    total_retries = 0

    for bundle_id, meta in sorted(labels.items()):
        bundle = load_bundle_from_json(root / meta["bundle_path"])
        if use_v0_checkpoint:
            report = review_bundle_v0_checkpoint(bundle)
        else:
            client = LocalFakeModelClient(mode=meta.get("model_mode", "normal")) if bundle.require_model_check else None
            report = review_bundle(
                bundle,
                model_client=client,
                model_config=ModelConfig(),
                use_exhaustive_equivalence=use_exhaustive,
                use_trust_validation=use_trust,
                mode_label=mode_name,
            )

        all_reports[bundle_id] = report.to_dict()
        expected = meta["expected_status"]
        actual = report.status.value
        correct = actual == expected

        has_cx = any(f.counterexample is not None for f in report.findings)
        has_loc = any(bool(f.location and f.evidence) for f in report.findings)
        has_corr = any(bool(f.correction) for f in report.findings)
        unsupported_findings += sum(1 for f in report.findings if f.category == "invented_reference")

        if expected == "CHANGES_REQUIRED":
            defective_total += 1
            if actual == "PASS":
                false_passes += 1
            if has_cx:
                defective_with_cx += 1
            if has_loc:
                defective_with_evidence_loc += 1
            if has_corr:
                defective_with_correction += 1
        elif expected == "PASS":
            valid_total += 1
            if actual == "CHANGES_REQUIRED":
                false_blocks += 1
        elif expected == "INCOMPLETE":
            incomplete_total += 1
            if actual == "INCOMPLETE":
                incomplete_correct += 1

        total_runtime_ms += report.resource_use.get("runtime_ms", 0.0)
        total_calls += report.resource_use.get("model_calls", 0)
        total_retries += report.resource_use.get("retries", 0)

        results.append(
            {
                "bundle_id": bundle_id,
                "split": meta["split"],
                "case_type": meta["case_type"],
                "expected": expected,
                "actual": actual,
                "correct": correct,
                "finding_count": len(report.findings),
                "has_counterexample": has_cx,
                "runtime_ms": report.resource_use.get("runtime_ms", 0.0),
            }
        )

    exact_matches = sum(1 for r in results if r["correct"])
    heldout_results = [r for r in results if r["split"] == "heldout"]
    heldout_correct = sum(1 for r in heldout_results if r["correct"])

    return {
        "mode": mode_name,
        "client_type": "none (v0)" if use_v0_checkpoint else "LocalFakeModelClient (synthetic)",
        "total_bundles": len(results),
        "exact_accuracy": f"{exact_matches}/{len(results)}",
        "heldout_accuracy": f"{heldout_correct}/{len(heldout_results)}",
        "false_passes_on_defective": f"{false_passes}/{defective_total}",
        "false_blocks_on_valid": f"{false_blocks}/{valid_total}",
        "incomplete_correct": f"{incomplete_correct}/{incomplete_total}",
        "finding_quality": {
            "defective_with_counterexample": f"{defective_with_cx}/{defective_total}",
            "defective_with_evidence_and_location": f"{defective_with_evidence_loc}/{defective_total}",
            "defective_with_correction": f"{defective_with_correction}/{defective_total}",
            "unsupported_or_invented_findings": unsupported_findings,
        },
        "resource_use": {
            "total_runtime_ms": round(total_runtime_ms, 3),
            "model_calls": total_calls,
            "retries": total_retries,
        },
        "per_bundle": results,
        "_reports": all_reports,
    }


def run_all_evaluations(root_dir: str | Path | None = None) -> Dict[str, Any]:
    root = Path(root_dir) if root_dir else Path(__file__).resolve().parents[2]
    labels_path = root / "data" / "expected_labels.json"
    if not labels_path.exists():
        write_dataset(root)
    with labels_path.open("r", encoding="utf-8") as f:
        labels = json.load(f)

    # Explicit dataset split & class breakdown
    breakdown = {
        "total": len(labels),
        "valid_pass": {
            "total": sum(1 for v in labels.values() if v["expected_status"] == "PASS"),
            "dev": sum(1 for v in labels.values() if v["expected_status"] == "PASS" and v["split"] == "dev"),
            "heldout": sum(1 for v in labels.values() if v["expected_status"] == "PASS" and v["split"] == "heldout"),
        },
        "defective_changes_required": {
            "total": sum(1 for v in labels.values() if v["expected_status"] == "CHANGES_REQUIRED"),
            "dev": sum(1 for v in labels.values() if v["expected_status"] == "CHANGES_REQUIRED" and v["split"] == "dev"),
            "heldout": sum(1 for v in labels.values() if v["expected_status"] == "CHANGES_REQUIRED" and v["split"] == "heldout"),
        },
        "incomplete": {
            "total": sum(1 for v in labels.values() if v["expected_status"] == "INCOMPLETE"),
            "dev": sum(1 for v in labels.values() if v["expected_status"] == "INCOMPLETE" and v["split"] == "dev"),
            "heldout": sum(1 for v in labels.values() if v["expected_status"] == "INCOMPLETE" and v["split"] == "heldout"),
        },
    }

    v0 = evaluate_mode(root, labels, "checkpoint_v0", use_v0_checkpoint=True)
    ablation = evaluate_mode(root, labels, "ablation_no_exhaustive_equiv", use_exhaustive=False, use_trust=True)
    final = evaluate_mode(root, labels, "final", use_exhaustive=True, use_trust=True)

    final_reports = final.pop("_reports")
    v0.pop("_reports")
    ablation.pop("_reports")

    summary = {
        "note": (
            "All model checks in offline eval use LocalFakeModelClient (synthetic); "
            "live real-model performance was not measured. Held-out set has 4 bundles (1 valid, 2 defective, 1 incomplete)."
        ),
        "dataset_breakdown": breakdown,
        "checkpoint_v0": v0,
        "ablation_no_exhaustive_equiv": ablation,
        "final": final,
    }
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    with (reports_dir / "eval_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    with (reports_dir / "per_bundle_findings.json").open("w", encoding="utf-8") as f:
        json.dump(final_reports, f, indent=2)
    return summary


if __name__ == "__main__":
    res = run_all_evaluations()
    for key in ("checkpoint_v0", "ablation_no_exhaustive_equiv", "final"):
        m = res[key]
        fq = m["finding_quality"]
        print(
            f"[{m['mode']}] exact={m['exact_accuracy']} heldout={m['heldout_accuracy']} "
            f"false_passes={m['false_passes_on_defective']} false_blocks={m['false_blocks_on_valid']} "
            f"incomplete={m['incomplete_correct']} cx={fq['defective_with_counterexample']} "
            f"runtime_ms={m['resource_use']['total_runtime_ms']}"
        )
