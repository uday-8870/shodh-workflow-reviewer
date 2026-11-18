# Shodh AI-Assisted Workflow Reviewer

A reproducible, CPU-only Python application that reviews ticket-routing requirements, structured specifications, workflow graphs, restricted Python snippets, and approval records to produce evidence-backed findings (`PASS`, `CHANGES_REQUIRED`, or `INCOMPLETE`).

## Quickstart (One CPU-Only Command)

Requires Python 3.9+ (zero external runtime dependencies):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v && PYTHONPATH=src python3 -m shodh_reviewer.eval
```

This runs all unit/trust/cache/regression tests and executes the frozen 14-bundle evaluation (10 dev + 4 held-out), writing machine-readable results to `reports/eval_summary.json`.

### Review a Single Bundle via CLI

```bash
PYTHONPATH=src python3 -m shodh_reviewer.reviewer --bundle data/bundles/dev/bundle_01_valid_approved_rev.json
```

## Project Structure

- `src/shodh_reviewer/schemas.py` — Stage 1 input/output contracts (`ReviewBundle`, `ReviewReport`, `Finding`, `Counterexample`, `CoverageReport`).
- `src/shodh_reviewer/dsl.py` — Structured condition DSL parser, time-unit normalizer (`s`/`m`/`h` -> canonical seconds), and evaluator.
- `src/shodh_reviewer/effective.py` — Revision-scoped `ApprovalRecord` application in `change_order` and contradictory approval detection.
- `src/shodh_reviewer/python_analyzer.py` — Restricted Python subset AST validator and safe interpreter (never executes arbitrary untrusted code).
- `src/shodh_reviewer/semantic_checker.py` — Stage 2 exhaustive domain-partitioning equivalence checker and counterexample generator.
- `src/shodh_reviewer/model_client.py` — Stage 3 `ModelClient` interface, injection-hardened prompt builder, and `LocalFakeModelClient`.
- `src/shodh_reviewer/trust_validator.py` — Citation/reference validation, prompt-injection detection, and bounded retries.
- `src/shodh_reviewer/cache.py` — SHA-256 content-addressed cache that invalidates on any artifact or approval change.
- `src/shodh_reviewer/oracle.py` — Independent routing oracle for non-circular label verification.
- `data/bundles/` — 14 frozen synthetic review bundles (10 `dev/`, 4 `heldout/`) + `data/expected_labels.json`.
- `docs/engineering_note.md` — Engineering note (≤4 pages).
- `docs/ai_assistance_disclosure.md` — Material AI assistance disclosure (≤1 page).
