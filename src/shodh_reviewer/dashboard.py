"""Generate a self-contained static HTML dashboard (reports/dashboard.html) for visual exploration."""

from __future__ import annotations

import json
from pathlib import Path


def generate_dashboard(root_dir: str | Path | None = None) -> Path:
    root = Path(root_dir) if root_dir else Path(__file__).resolve().parents[2]
    summary = json.loads((root / "reports" / "eval_summary.json").read_text(encoding="utf-8"))
    per_bundle = json.loads((root / "reports" / "per_bundle_findings.json").read_text(encoding="utf-8"))
    labels = json.loads((root / "data" / "expected_labels.json").read_text(encoding="utf-8"))

    bundles_raw = {}
    for bid, meta in labels.items():
        bundles_raw[bid] = json.loads((root / meta["bundle_path"]).read_text(encoding="utf-8"))

    payload = json.dumps({"summary": summary, "reports": per_bundle, "bundles": bundles_raw, "labels": labels})

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Shodh AI — Workflow Reviewer Dashboard</title>
  <style>
    :root { --bg:#0f172a; --panel:#1e293b; --panel2:#162033; --border:#334155; --text:#f1f5f9; --muted:#94a3b8; --pass:#10b981; --fail:#ef4444; --warn:#f59e0b; --accent:#38bdf8; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; }
    header { padding:18px 24px; border-bottom:1px solid var(--border); background:var(--panel2); }
    header h1 { margin:0; font-size:1.2rem; }
    header p { margin:4px 0 0; font-size:0.85rem; color:var(--muted); }
    .metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; padding:16px 24px; border-bottom:1px solid var(--border); }
    .mcard { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:12px 14px; }
    .mcard .lbl { font-size:0.75rem; color:var(--muted); text-transform:uppercase; }
    .mcard .val { font-size:1.35rem; font-weight:700; margin-top:4px; }
    .mcard .sub { font-size:0.78rem; color:var(--muted); margin-top:2px; }
    .layout { display:grid; grid-template-columns:320px 1fr; min-height:calc(100vh - 175px); }
    @media (max-width:900px) { .layout { grid-template-columns:1fr; } }
    aside { border-right:1px solid var(--border); padding:16px; background:var(--panel2); overflow-y:auto; }
    .bbtn { width:100%; text-align:left; background:var(--panel); border:1px solid var(--border); color:var(--text); padding:10px 12px; border-radius:8px; margin-bottom:8px; cursor:pointer; }
    .bbtn.active { border-color:var(--accent); background:#1e3a5f; }
    .btop { display:flex; justify-content:space-between; align-items:center; font-size:0.85rem; font-weight:600; gap:6px; }
    .bsub { font-size:0.75rem; color:var(--muted); margin-top:4px; }
    .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.7rem; font-weight:700; }
    .PASS { background:rgba(16,185,129,0.18); color:#34d399; border:1px solid rgba(16,185,129,0.4); }
    .CHANGES_REQUIRED { background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4); }
    .INCOMPLETE { background:rgba(245,158,11,0.18); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); }
    .HELDOUT { background:rgba(56,189,248,0.16); color:#7dd3fc; border:1px solid rgba(56,189,248,0.35); margin-left:6px; }
    main { padding:20px 24px; overflow-y:auto; }
    .card { background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:16px; }
    .finding { border-left:4px solid var(--accent); background:var(--panel2); padding:12px; border-radius:6px; margin-bottom:10px; }
    .finding.BLOCKER { border-left-color:var(--fail); }
    .finding.WARNING { border-left-color:var(--warn); }
    .cx { margin-top:8px; background:#0b1220; border:1px solid var(--border); border-radius:6px; padding:10px; font-family:monospace; font-size:0.8rem; }
    .grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }
    pre { background:#0b1220; border:1px solid var(--border); border-radius:8px; padding:10px; overflow-x:auto; font-size:0.78rem; margin:0; }
    .muted { color:var(--muted); font-size:0.85rem; }
  </style>
</head>
<body>
  <header>
    <h1>Shodh AI — Workflow Reviewer Explorer</h1>
    <p>Interactive offline dashboard showing all 14 frozen scenarios, counterexample tickets, and side-by-side artifacts</p>
  </header>
  <section class="metrics" id="metrics"></section>
  <div class="layout">
    <aside><div id="blist"></div></aside>
    <main id="main"></main>
  </div>
  <script id="app-data" type="application/json">__PAYLOAD__</script>
  <script>
    const DATA = JSON.parse(document.getElementById('app-data').textContent);
    function el(tag, cls, text) {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text !== undefined) n.textContent = text;
      return n;
    }
    function renderMetrics() {
      const m = document.getElementById('metrics');
      const cards = [
        ['Final Reviewer', DATA.summary.final.exact_accuracy, 'Held-out: ' + DATA.summary.final.heldout_accuracy + ' | False passes: ' + DATA.summary.final.false_passes_on_defective],
        ['Ablation (No Exhaustive)', DATA.summary.ablation_no_exhaustive_equiv.exact_accuracy, 'False passes: ' + DATA.summary.ablation_no_exhaustive_equiv.false_passes_on_defective + ' | Counterexamples: 0/7'],
        ['v0 Checkpoint', DATA.summary.checkpoint_v0.exact_accuracy, 'False blocks: ' + DATA.summary.checkpoint_v0.false_blocks_on_valid + ' | False passes: ' + DATA.summary.checkpoint_v0.false_passes_on_defective],
        ['Dataset Breakdown', DATA.summary.dataset_breakdown.total + ' Bundles', '4 valid | 7 defective | 3 incomplete']
      ];
      cards.forEach(([l, v, s]) => {
        const c = el('div', 'mcard');
        c.appendChild(el('div', 'lbl', l));
        c.appendChild(el('div', 'val', v));
        c.appendChild(el('div', 'sub', s));
        m.appendChild(c);
      });
    }
    function selectBundle(bid) {
      document.querySelectorAll('.bbtn').forEach(b => b.classList.toggle('active', b.dataset.id === bid));
      const rep = DATA.reports[bid];
      const bun = DATA.bundles[bid];
      const main = document.getElementById('main');
      main.replaceChildren();

      const hdr = el('div', 'card');
      const title = el('h2', '', rep.bundle_id + ' ');
      title.appendChild(el('span', 'badge ' + rep.status, rep.status));
      hdr.appendChild(title);
      hdr.appendChild(el('div', 'muted', rep.summary + ' (Coverage: ' + rep.coverage.reviewed_source_rules + '/' + rep.coverage.total_source_rules + ', Runtime: ' + rep.resource_use.runtime_ms + ' ms)'));
      main.appendChild(hdr);

      const fcard = el('div', 'card');
      fcard.appendChild(el('h3', '', 'Findings & Counterexamples (' + rep.findings.length + ')'));
      if (rep.findings.length === 0) {
        fcard.appendChild(el('p', 'muted', 'No issues found. All effective requirements, spec rules, workflow paths, and Python snippets agree.'));
      } else {
        rep.findings.forEach(f => {
          const box = el('div', 'finding ' + f.severity);
          box.appendChild(el('strong', '', '[' + f.severity + '] ' + f.category + ' (' + f.artifact + ' @ ' + f.location + (f.rule_ref ? ', Rule ' + f.rule_ref : '') + ')'));
          box.appendChild(el('div', '', f.message));
          box.appendChild(el('div', 'muted', 'Evidence: ' + f.evidence));
          box.appendChild(el('div', 'muted', 'Correction: ' + f.correction));
          if (f.counterexample) {
            const cx = el('div', 'cx');
            cx.appendChild(el('div', '', 'Counterexample Ticket: ' + JSON.stringify(f.counterexample.ticket)));
            cx.appendChild(el('div', '', 'Expected: ' + f.counterexample.expected_route + ' | Actual: ' + f.counterexample.actual_route));
            box.appendChild(cx);
          }
          fcard.appendChild(box);
        });
      }
      main.appendChild(fcard);

      const g = el('div', 'grid2');
      [
        ['Effective Requirements', JSON.stringify(rep.effective_requirements, null, 2)],
        ['Structured Spec', JSON.stringify(bun.structured_spec.rules, null, 2)],
        ['Workflow Graph', JSON.stringify(bun.workflow_graph.nodes, null, 2)],
        ['Python Snippets & Approvals', (bun.python_snippets.map(s => s.code).join('\\n---\\n') || '// None') + '\\n\\n// Approvals:\\n' + JSON.stringify(bun.approvals, null, 2)]
      ].forEach(([t, body]) => {
        const c = el('div', 'card');
        c.appendChild(el('h3', '', t));
        c.appendChild(el('pre', '', body));
        g.appendChild(c);
      });
      main.appendChild(g);
    }
    renderMetrics();
    const blist = document.getElementById('blist');
    DATA.summary.final.per_bundle.forEach((item, idx) => {
      const btn = el('button', 'bbtn');
      btn.dataset.id = item.bundle_id;
      const top = el('div', 'btop');
      top.appendChild(el('span', '', item.bundle_id.replace(/^bundle_\\d+_/, '')));
      top.appendChild(el('span', 'badge ' + item.expected, item.expected));
      btn.appendChild(top);
      const sub = el('div', 'bsub', item.case_type);
      if (item.split === 'heldout') sub.appendChild(el('span', 'badge HELDOUT', 'HELD-OUT'));
      btn.appendChild(sub);
      btn.addEventListener('click', () => selectBundle(item.bundle_id));
      blist.appendChild(btn);
      if (idx === 0) selectBundle(item.bundle_id);
    });
  </script>
</body>
</html>
"""
    out_path = root / "reports" / "dashboard.html"
    out_path.write_text(html.replace("__PAYLOAD__", payload), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = generate_dashboard()
    print(f"Generated interactive dashboard at {p}")
