"""Stage 3: Trust, injection defense, citation validation, and bounded retry handler."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .model_client import ModelClient, ModelConfig, ModelResponse, build_reviewer_prompt
from .python_analyzer import extract_comments
from .schemas import CheckResult, Finding, ReviewBundle, Severity

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"(?:force|return|output|mark)\s+(?:status\s*[:=]\s*)?PASS\b", re.IGNORECASE),
    re.compile(r"override\s+approval", re.IGNORECASE),
    re.compile(r"approved\s+by\s+.*?\bin\s+prose\b", re.IGNORECASE),
    re.compile(r"replace\s+\d+\s+minutes?\s+with\s+\d+", re.IGNORECASE),
    re.compile(r"unapproved\s+suggestion.*?\bapply\b", re.IGNORECASE),
]


def detect_untrusted_injections(bundle: ReviewBundle) -> List[Finding]:
    """Detect prompt-injection or unauthorized approval claims inside prose or code comments."""
    findings: List[Finding] = []

    for idx, note in enumerate(bundle.prose_notes):
        for pat in INJECTION_PATTERNS:
            if pat.search(note):
                findings.append(
                    Finding(
                        finding_id=f"TRUST-INJ-PROSE-{idx}",
                        severity=Severity.WARNING,
                        category="prompt_injection_ignored",
                        message="Detected instruction/approval-override attempt inside untrusted prose_notes; ignored.",
                        rule_ref=None,
                        artifact="prose_notes",
                        version=bundle.bundle_version,
                        location=f"prose_notes[{idx}]",
                        evidence=note[:180],
                        correction="Use top-level ApprovalRecord metadata for policy changes; remove instructions from prose.",
                    )
                )
                break

    for r in bundle.source_requirements:
        for pat in INJECTION_PATTERNS:
            if pat.search(r.text):
                findings.append(
                    Finding(
                        finding_id=f"TRUST-INJ-REQ-{r.rule_id}",
                        severity=Severity.WARNING,
                        category="prompt_injection_ignored",
                        message=f"Detected instruction/override text in requirement '{r.rule_id}'; approval authority unchanged.",
                        rule_ref=r.rule_id,
                        artifact="source_requirements",
                        version=r.version,
                        location=r.location,
                        evidence=r.text[:180],
                        correction="Remove meta-instructions from requirement text.",
                    )
                )
                break

    for snip in bundle.python_snippets:
        for c_idx, comment in enumerate(extract_comments(snip.code)):
            for pat in INJECTION_PATTERNS:
                if pat.search(comment):
                    findings.append(
                        Finding(
                            finding_id=f"TRUST-INJ-CODE-{snip.snippet_id}-{c_idx}",
                            severity=Severity.WARNING,
                            category="prompt_injection_ignored",
                            message=f"Detected instruction/override attempt in code comment of '{snip.snippet_id}'; ignored.",
                            rule_ref=snip.linked_rule_ids[0] if snip.linked_rule_ids else None,
                            artifact="python_snippets",
                            version=snip.version,
                            location=f"{snip.location}#comment-{c_idx}",
                            evidence=comment[:180],
                            correction="Remove reviewer-targeting instructions from code comments.",
                        )
                    )
                    break

    return findings


def _known_references(bundle: ReviewBundle) -> Tuple[Set[str], Set[str]]:
    known_rules: Set[str] = set()
    known_locs: Set[str] = set()
    for r in bundle.source_requirements:
        known_rules.add(r.rule_id)
        known_locs.add(r.location)
    for sr in bundle.structured_spec.rules:
        known_rules.add(sr.rule_id)
        if sr.source_ref:
            known_rules.add(sr.source_ref)
        known_locs.add(sr.location)
    for node in bundle.workflow_graph.nodes.values():
        known_locs.add(node.location)
        for br in node.branches:
            if br.location:
                known_locs.add(br.location)
            if br.rule_ref:
                known_rules.add(br.rule_ref)
    for snip in bundle.python_snippets:
        known_locs.add(snip.location)
        for rid in snip.linked_rule_ids:
            known_rules.add(rid)
    return known_rules, known_locs


def run_model_check_with_trust(
    bundle: ReviewBundle,
    client: Optional[ModelClient],
    config: ModelConfig,
    deterministic_has_blocker: bool,
) -> Tuple[List[Finding], CheckResult, Dict[str, Any], int, int]:
    """Execute bounded-retry model check and validate schema, references, and conflicts.

    Returns:
        (findings, check_result, provenance, calls_made, retries_made)
    """
    if client is None:
        return (
            [],
            CheckResult(
                check_name="model_review_check",
                passed=not bundle.require_model_check,
                status="SKIPPED" if not bundle.require_model_check else "FAILED",
                details="No model client configured." + (" Required by bundle." if bundle.require_model_check else ""),
            ),
            {"client": None, "synthetic": False},
            0,
            0,
        )

    prompt = build_reviewer_prompt(bundle)
    known_rules, known_locs = _known_references(bundle)

    calls = 0
    retries = 0
    last_resp: Optional[ModelResponse] = None
    parsed: Optional[Dict[str, Any]] = None
    failure_reason: Optional[str] = None

    for attempt in range(config.max_retries + 1):
        calls += 1
        if attempt > 0:
            retries += 1
        resp = client.review(prompt, config)
        last_resp = resp
        if resp.timed_out or resp.error:
            failure_reason = resp.error or "Model request timed out"
            continue
        try:
            obj = json.loads(resp.raw_text)
            if not isinstance(obj, dict) or "assessment" not in obj:
                failure_reason = "Model JSON missing required 'assessment' field"
                continue
            parsed = obj
            failure_reason = None
            break
        except json.JSONDecodeError as exc:
            failure_reason = f"Malformed/truncated model JSON: {exc.msg}"
            continue

    provenance = dict(last_resp.provenance) if last_resp else {}
    provenance["calls"] = calls
    provenance["retries"] = retries
    provenance["latency_ms"] = round(last_resp.latency_ms, 3) if last_resp else 0.0
    provenance["tokens_used"] = last_resp.tokens_used if last_resp else 0

    if parsed is None:
        finding = Finding(
            finding_id="MODEL-FAILURE",
            severity=Severity.WARNING,
            category="model_failure",
            message=f"Model review failed after {calls} attempt(s): {failure_reason}",
            rule_ref=None,
            artifact="model_client",
            version=config.model_name,
            location="model_response",
            evidence=failure_reason or "Unknown model error",
            correction="Inspect model endpoint/timeout or retry with valid JSON response.",
        )
        return (
            [finding],
            CheckResult(
                check_name="model_review_check",
                passed=False,
                status="ERROR",
                details=failure_reason or "Model failure",
            ),
            provenance,
            calls,
            retries,
        )

    findings: List[Finding] = []
    # Validate cited references
    for idx, cf in enumerate(parsed.get("cited_findings", [])):
        if not isinstance(cf, dict):
            continue
        ref = cf.get("rule_ref")
        loc = cf.get("location")
        if (ref and ref not in known_rules) or (loc and loc not in known_locs):
            findings.append(
                Finding(
                    finding_id=f"MODEL-INVENTED-REF-{idx}",
                    severity=Severity.WARNING,
                    category="invented_reference",
                    message=f"Model cited non-existent reference (rule_ref={ref}, location={loc}).",
                    rule_ref=None,
                    artifact="model_client",
                    version=config.model_name,
                    location=f"model_response.cited_findings[{idx}]",
                    evidence=json.dumps(cf),
                    correction="Reject unverified model citations and rely on deterministic evidence.",
                )
            )

    # Check conflicting claim (e.g. model says PASS when deterministic checks found a blocker)
    if parsed.get("assessment") == "PASS" and deterministic_has_blocker:
        findings.append(
            Finding(
                finding_id="MODEL-CONFLICT-CLAIM",
                severity=Severity.INFO,
                category="conflicting_model_claim",
                message="Model returned PASS despite deterministic blocker findings; deterministic evidence takes precedence.",
                rule_ref=None,
                artifact="model_client",
                version=config.model_name,
                location="model_response.assessment",
                evidence=f"model={parsed.get('assessment')}, deterministic=BLOCKER",
                correction="Deterministic counterexamples override model PASS claims.",
            )
        )

    has_invented = any(f.category == "invented_reference" for f in findings)
    return (
        findings,
        CheckResult(
            check_name="model_review_check",
            passed=not has_invented,
            status="PASSED" if not has_invented else "FAILED",
            details=(
                "Model response validated."
                if not has_invented
                else "Model response contained invented rule/location references."
            ),
        ),
        provenance,
        calls,
        retries,
    )
