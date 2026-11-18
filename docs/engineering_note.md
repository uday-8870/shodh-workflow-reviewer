# Engineering Note: Shodh AI-Assisted Workflow Reviewer

## 1. Supported Scope & Input Contracts (Stage 1)
The reviewer targets ticket-routing policy workflows across five versioned artifacts (`schemas.py`): `SourceRequirement`, `StructuredSpec`, `WorkflowGraph`, `PythonSnippet`, and `ApprovalRecord`. Each item preserves `version` and `location` (e.g., `policy.md:L10`, `spec.yaml:L5`, `router.py:L1`).

- **Declared Condition DSL (`dsl.py`):** Supports missing-value predicates (`MISSING(field)`, `PRESENT(field)`, `field == None`, `field != None`), inclusive/exclusive numeric comparisons (`<, <=, >, >=`), string/boolean equality (`==, !=`), nested `AND`/`OR`/`NOT`, ordered exceptions, and declared time units (`seconds`, `minutes`, `hours`) normalized to canonical seconds.
- **Restricted Python Subset (`python_analyzer.py`):** Uses `ast.parse` and an explicit AST whitelist (`def route_ticket(ticket):` with `if/elif/else`, `return`, simple assignments, `ticket.get(...)`, boolean/comparison ops, and arithmetic unit scaling). Arbitrary Python (loops, imports, `eval`, external calls) is never executed via `exec()` and is flagged as unsupported/unreviewed in `CoverageReport`.
- **Effective Requirements (`effective.py`):** Filters `ApprovalRecord` entries by `revision_scope`, ignores `UNAPPROVED`/`REJECTED`/`PROPOSED` records and prose claims, detects contradictory `APPROVED` records at the same `change_order` (halting with `INCOMPLETE`), and applies valid changes in ascending `change_order`.

## 2. Semantic Review & Counterexample Generation (Stage 2)
Rather than relying on rule-ID matching or numeric substring checks, `semantic_checker.py` performs **exhaustive equivalence-class domain partitioning** over all referenced fields:
- Every field includes `None` (missing).
- Numeric fields include boundary neighborhoods (`b - 60, b - 1, b, b + 1, b + 60` in canonical seconds, plus `b ± 0.5` for fractional thresholds).
- String/boolean fields include all referenced constants plus a `__other__` sentinel.

Every generated ticket is evaluated against (1) effective requirements, (2) structured spec, (3) reachable workflow graph paths (also detecting cycles, dead-ends, and unreachable nodes), and (4) AST-validated Python snippets. Because evaluation is exhaustive over the partitioned domain, equivalent formulations (`waiting_seconds >= 1800` vs `waiting_minutes >= 30`, De Morgan rewrites, and disjoint rule reordering) pass cleanly, while omitted rules, `>` vs `>=` boundary bugs, unit mismatches, precedence swaps, and code disagreements produce an exact **concrete counterexample ticket** with `expected_route`, `actual_route`, `rule_ref`, `location`, and `correction`.

## 3. Model Integration, Trust & Failure Handling (Stage 3)
`model_client.py` defines an abstract `ModelClient` interface, `ModelConfig` (temperature, max_tokens, timeout_s, max_retries), `ReviewerPromptBuilder`, and a deterministic `LocalFakeModelClient` for reproducible offline testing.
- **What model judgment contributes:** Beyond deterministic DSL/AST checks, a live model can flag subtle semantic drift in unstructured `prose_notes` or unsupported text; however, synthetic fake-client tests do **not** establish live model accuracy.
- **Trust & Failure Handling (`trust_validator.py`):**
  - Treats `prose_notes`, requirement text, and Python comments as **untrusted data**; detects instruction/approval-override attempts (`prompt_injection_ignored`).
  - Validates model JSON structure with bounded retries (`max_retries`) on timeouts or malformed/truncated JSON.
  - Cross-checks every cited `rule_ref` and `location` against known bundle references, rejecting hallucinated citations (`invented_reference`).
  - Never converts a failed required check into `PASS`: `PASS` requires full coverage and zero blockers; `CHANGES_REQUIRED` indicates evidenced blockers (while disclosing any unreviewed scope); `INCOMPLETE` indicates missing/unsupported scope, contradictory approvals, or failed required checks.
- **Cache Invalidation (`cache.py`):** `ReviewCache` keys reports by the SHA-256 hash of `(bundle, approvals, model_config, reviewer_mode)` so any edit to artifacts or approvals invalidates stale `PASS` results.

## 4. Controlled Evaluation, Ablation & Debugging (Stage 4)
Evaluation runs on **14 frozen synthetic bundles** (`data/bundles/dev/` and `data/bundles/heldout/`) with external labels in `data/expected_labels.json` cross-checked against an independent routing oracle (`oracle.py`).
- **Dataset Breakdown:** `4` valid (`PASS`: `3` dev + `1` held-out), `7` defective (`CHANGES_REQUIRED`: `5` dev + `2` held-out), and `3` incomplete (`INCOMPLETE`: `2` dev + `1` held-out). Note: `4/4` on held-out meets the minimum 4-bundle requirement, but is a small evaluation set and does not imply generalization beyond our declared DSL/AST scope.
- **Model Provenance:** All offline runs use `LocalFakeModelClient (synthetic)`; live real-model performance was not measured.
- **Preserved Checkpoint vs. Controlled Ablation vs. Final:**
  - `checkpoint_v0` (`src/shodh_reviewer/checkpoint_v0.py`): Initial working checkpoint using basic rule-ID + lowercase string matching and Python syntax checks—no unit normalization, no approval conflict detection, no exhaustive counterexamples, and no model trust layer. Scores `7/14` with `3/4` false blocks on valid unusual/reordered formulations and `3/7` false passes on defective code/workflow bundles.
  - `ablation_no_exhaustive_equiv`: Holds all Stage 1 (approval conflict detection, effective requirements) and Stage 3 (trust/injection/model validation) settings fixed while switching off `use_exhaustive_equivalence` (falling back to rule-ID check only). Eliminates the `3/4` false blocks (`0/4`), but suffers `6/7` false passes on defective bundles and produces `0/7` counterexamples.
  - `final`: Full 3-stage reviewer (`use_exhaustive_equivalence=True`, `use_trust_validation=True`). Achieves `14/14` exact, `0/7` false passes, `0/4` false blocks, `3/3` incomplete, `7/7` counterexamples with evidence/location/correction, and `0` unsupported findings.

| Configuration | Exact | Held-out | False Passes (Defective) | False Blocks (Valid) | Incomplete | Counterexamples | Runtime (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `checkpoint_v0` (`checkpoint_v0.py`) | 7/14 | 2/4 | 3/7 | 3/4 | 2/3 | 0/7 | 0.506 |
| `ablation_no_exhaustive_equiv` (fixed Stage 1+3) | 8/14 | 2/4 | 6/7 | 0/4 | 3/3 | 0/7 | 3.858 |
| `final` (full 3-stage reviewer) | **14/14** | **4/4** | **0/7** | **0/4** | **3/3** | **7/7** | 10.265 |

*(Full per-bundle findings and test logs are saved in `reports/per_bundle_findings.json`, `reports/eval_summary.json`, and `reports/test_and_eval_output.txt`.)*

### Substantive Debugging Examples
1. **Real Failure Caught During Final Integration (`!=` on missing values):**
   - *Failing output:* Before the fix in `dsl.py`, `test_full_14_bundle_evaluation` failed with `AssertionError: '13/14' != '14/14'` because `bundle_11_heldout_valid_nested_exception` returned `CHANGES_REQUIRED` instead of `PASS`. (Note: this occurred on the near-final Stage 2/3 implementation, separate from `checkpoint_v0` at `7/14`.)
   - *Diagnosis:* Source rule `R2` used `NOT (tier == "sandbox")` while spec `S2` used `tier != "sandbox"`. In `dsl.py`, `CompareExpr` returned `False` whenever a field was missing (`None`), so `tier != "sandbox"` evaluated to `False` on `tier=None` while `NOT (tier == "sandbox")` evaluated to `True`.
   - *Fix & Regression:* Updated `CompareExpr` in `dsl.py` to return `expr.op == "!="` when a field is missing, restoring `a != b <=> NOT (a == b)`. Verified via `test_regression_debug_ex1_not_equal_missing` (`OK`).
2. **Deliberate Fault Injection (`category=""` vs `None`):**
   - *Fault injected:* Empty-string `category=""` in ticket inputs can bypass `!= None` checks unless normalized consistently across DSL, Python AST view, and the independent oracle.
   - *Fix & Regression:* Normalized whitespace/empty strings to missing in `get_ticket_field` (`dsl.py`) and verified in `test_deliberate_fault_injection_empty_string_missing` (`OK`).

## 5. Limitations
- Exhaustive domain partitioning assumes ticket fields are independent booleans, enums, or thresholded numbers; arbitrary string regexes, loops, or external API calls in Python are outside the supported subset and are surfaced as `INCOMPLETE` rather than silently passed.
- Live LLM semantic accuracy was not measured offline (`LocalFakeModelClient` used for reproducible CI).
