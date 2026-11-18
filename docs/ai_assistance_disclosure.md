# Material AI Assistance Disclosure

**Candidate:** Udayadithya Reddy Singireddy  
**Assessment:** Shodh AI — AI-Assisted Workflow Reviewer

## Use of AI Assistance
AI assistance (Gemini) was used during this assessment to:
- Scaffold the modular Python package structure (`schemas.py`, `dsl.py`, `effective.py`, `python_analyzer.py`, `semantic_checker.py`, `model_client.py`, `trust_validator.py`, `cache.py`, `reviewer.py`, `eval.py`).
- Draft the 14 synthetic review bundles (`generate_dataset.py`) and initial `unittest` cases (`tests/test_reviewer.py`).
- Draft and format the engineering note (`docs/engineering_note.md`).

## Verification & Ownership
I verified all generated code, logic, and evaluation results independently:
1. **Non-Circular Label Verification:** Implemented an independent routing oracle (`src/shodh_reviewer/oracle.py`) separate from the reviewer's semantic engine to cross-check expected routing behavior.
2. **Offline Execution & Debugging:** Executed the full `unittest` suite and `shodh_reviewer.eval` offline on CPU (`Python 3.9.6`), caught and diagnosed the real `!=` missing-value equivalence bug on held-out bundle `bundle_11_heldout_valid_nested_exception`, and verified the fix (`14/14` exact, `4/4` held-out, `0/7` false passes, `0/4` false blocks).
3. **Trust & Safety Checks:** Verified via unit tests that arbitrary Python (`while True`) is never executed via `exec()`, prompt/comment injections do not alter approval authority, model failures/invented citations yield `INCOMPLETE`, and cache keys invalidate on any approval edit.
