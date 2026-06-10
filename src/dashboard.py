"""Generate a self-contained HTML dashboard from the pipeline's stage outputs.

No dependencies, no server: open outputs/dashboard.html in any browser.
"""

import html
import json
import os

CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#f5f6f8;color:#1c2733}
header{background:#10243e;color:#fff;padding:24px 32px}
header h1{margin:0 0 4px;font-size:22px} header p{margin:0;opacity:.75;font-size:13px}
main{max-width:1180px;margin:24px auto;padding:0 16px}
section{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(16,36,62,.08);padding:20px 24px;margin-bottom:20px}
h2{font-size:16px;margin:0 0 12px;color:#10243e}
table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#eef1f5;text-align:left;padding:7px 10px;color:#44546a}
td{border-top:1px solid #eef1f5;padding:7px 10px;vertical-align:top}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
.kpi{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(16,36,62,.08);padding:14px 20px;min-width:130px}
.kpi b{display:block;font-size:24px;color:#10243e}.kpi span{font-size:12px;color:#67788e}
.tag{display:inline-block;border-radius:10px;padding:2px 9px;font-size:11.5px;font-weight:600}
.high,.escalate,.block_and_escalate{background:#fdecea;color:#b3261e}
.medium,.scheduled_follow_up,.revise{background:#fef3df;color:#9a6a00}
.low,.immediate_resolution,.approve{background:#e6f4ea;color:#1e7b34}
.note{font-size:12px;color:#67788e;margin-top:8px}
details{margin:6px 0}summary{cursor:pointer;font-size:13px;font-weight:600;color:#2b5b9e}
ul{margin:6px 0 6px 18px;padding:0;font-size:13px}li{margin:3px 0}
.flag{color:#b3261e;font-weight:600}
"""


def _e(x):
    return html.escape(str(x))


def _tag(v):
    return f'<span class="tag {_e(v)}">{_e(str(v).replace("_", " "))}</span>'


def build_dashboard(out_dir):
    def j(name):
        with open(os.path.join(out_dir, name)) as f:
            return json.load(f)

    s1, s2, s3 = j("01_account_review.json"), j("02_prioritization.json"), j("03_issue_triage.json")
    s4, s5, s6 = j("04_checkin_briefs.json"), j("05_quality_review.json"), j("06_intervention.json")

    with open(os.path.join(out_dir, "usage_log.csv")) as f:
        rows = [r.split(",") for r in f.read().strip().splitlines()[1:]]
    cost = sum(float(r[-1]) for r in rows)
    toks = sum(int(r[2]) + int(r[3]) + int(r[4]) for r in rows)

    kpis = [
        (s1["screened"], "accounts screened"), (s1["flagged"], "flagged at-risk"),
        (len(s1["expansion_candidates"]), "expansion candidates"),
        (len(s3["routes"]), "issues routed"),
        (s3["route_counts"]["escalate"], "escalations"),
        (len(s4), "check-in briefs"),
        (len(s5["reviews"]), "outputs reviewed"),
        (f"${cost:.3f}", f"LLM cost ({toks:,} tokens)"),
    ]
    kpi_html = "".join(f'<div class="kpi"><b>{_e(v)}</b><span>{_e(l)}</span></div>' for v, l in kpis)

    att = "".join(
        f"<tr><td>{r['rank']}</td><td><b>{_e(r['account_id'])}</b></td>"
        f"<td>{_e(r['reason'])}</td><td>{_e(r['owner_action'])}</td></tr>"
        for r in s2["attention_list"])
    pat = "".join(
        f"<tr><td>{_e(p['pattern'])}</td><td>{_e(', '.join(p['accounts']))}</td>"
        f"<td>{_e(p['suggested_response'])}</td></tr>" for p in s2["portfolio_patterns"])
    exp = ", ".join(_e(w["account_id"]) for w in s2["expansion_watchlist"])

    risk = "".join(
        f"<tr><td><b>{_e(b['account_id'])}</b></td><td>{_tag(b['risk_level'])}</td>"
        f"<td>{_e(', '.join(b['risk_types']))}</td><td>{_e('; '.join(b['drivers']))}</td>"
        f"<td>{_e(b['recommended_action'])}</td>"
        f"<td>{'<span class=flag>yes</span>' if b['needs_human'] else 'no'}</td></tr>"
        for b in s1["briefs"])

    tri = "".join(
        f"<tr><td><b>{_e(r['ticket_id'])}</b></td><td>{_tag(r['final_route'])}</td>"
        f"<td>{_e(r['priority'])}</td><td>{_e(r['owner'])}</td><td>{_e(r['first_action'])}</td>"
        f"<td>{('<span class=flag>guardrail: ' + _e(r['guardrail_override']) + '</span>') if r['guardrail_override'] else ''}</td></tr>"
        for r in s3["routes"])
    esc = "".join(
        f"<tr><td><b>{_e(p['ticket_id'])}</b> ({_e(p['account_id'])})</td>"
        f"<td>${p['revenue_at_risk_usd']:,}</td><td>{_e(p['recommended_owner'])}</td>"
        f"<td>{_e(p['deadline'])}</td><td>{_e(p['fallback_plan'])}</td></tr>"
        for p in s3["escalation_packets"])

    chk = ""
    for b in s4:
        cont = "".join(f"<li>{_e(i['item'])} — <i>{_e(i['status'])}</i></li>" for i in b["continuity_items"])
        talk = "".join(f"<li>{_e(t)}</li>" for t in b["talking_points"])
        acts = "".join(f"<li>{_e(a)}</li>" for a in b["post_call_actions"])
        chk += (f"<details><summary>{_e(b['checkin_id'])} — {_e(b['account_id'])} "
                f"(continuity check: {_e(b.get('_continuity_check', 'n/a'))})</summary>"
                f"<b>Objectives:</b><ul>{''.join(f'<li>{_e(o)}</li>' for o in b['objectives'])}</ul>"
                f"<b>Carry-over from last call:</b><ul>{cont or '<li>none</li>'}</ul>"
                f"<b>Talking points:</b><ul>{talk}</ul>"
                f"<b>Success criteria:</b> {_e(b['success_criteria'])}"
                f"<b><br>Post-call actions:</b><ul>{acts}</ul></details>")

    qua = ""
    for r in s5["reviews"]:
        std = "".join(
            f"<li>{_e(s['standard_id'])}: {'PASS' if s['pass'] else '<span class=flag>FAIL</span>'} — {_e(s['evidence'])}</li>"
            for s in r["standard_results"])
        qua += (f"<details><summary>{_e(r['output_id'])} — {_tag(r['verdict'])}</summary>"
                f"<ul>{std}</ul>"
                + (f"<b>Revision instructions:</b> {_e(r['revision_instructions'])}" if r.get("revision_instructions") else "")
                + "</details>")

    iv = s6.get("intervention", {})
    steps = "".join(f"<li><b>{_e(s['account_id'])}</b>: {_e(s['step'])}</li>"
                    for s in iv.get("per_account_steps", []))
    mets = "".join(
        f"<tr><td>{_e(m['name'])}</td><td>{_e(m['baseline'])}</td><td>{_e(m['target'])}</td></tr>"
        for m in s6.get("measurement_plan", {}).get("metrics", []))

    cost_rows = {}
    for r in rows:
        st = cost_rows.setdefault(r[0], [r[1], 0, 0, 0.0])
        st[1] += int(r[2]) + int(r[3]); st[2] += int(r[4]); st[3] += float(r[-1])
    cost_html = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(v[0])}</td><td>{v[1]:,}</td><td>{v[2]:,}</td><td>${v[3]:.4f}</td></tr>"
        for k, v in cost_rows.items())

    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Customer Success AI — Daily Operations Dashboard</title><style>{CSS}</style></head><body>
<header><h1>Customer Success AI — Daily Operations Dashboard</h1>
<p>Simulated business day 2026-05-01 · 18-account synthetic portfolio standing in for 750 accounts ·
models: Haiku 4.5 (volume) / Sonnet 4.6 (judgment) / Opus 4.8 (interventions)</p></header><main>
<div class="kpis">{kpi_html}</div>
<section><h2>1 · Today's attention list (prioritized)</h2><table>
<tr><th>#</th><th>Account</th><th>Why now</th><th>Owner action</th></tr>{att}</table>
<p class="note">Expansion watchlist: {exp}</p>
<h2 style="margin-top:18px">Portfolio patterns detected</h2><table>
<tr><th>Pattern</th><th>Accounts</th><th>Suggested response</th></tr>{pat}</table></section>
<section><h2>2 · Account risk briefs ({s1["flagged"]} flagged of {s1["screened"]} screened)</h2><table>
<tr><th>Account</th><th>Risk</th><th>Types</th><th>Drivers</th><th>Recommended action</th><th>Needs human</th></tr>
{risk}</table></section>
<section><h2>3 · Inbound issue routing ({len(s3["routes"])} issues)</h2><table>
<tr><th>Ticket</th><th>Route</th><th>Priority</th><th>Owner</th><th>First action</th><th>Overrides</th></tr>{tri}</table>
<h2 style="margin-top:18px">Escalation packets</h2><table>
<tr><th>Ticket</th><th>Revenue at risk</th><th>Owner</th><th>Deadline</th><th>Fallback</th></tr>{esc}</table></section>
<section><h2>4 · Check-in briefs ({len(s4)} upcoming calls)</h2>{chk}</section>
<section><h2>5 · Output quality review</h2>
<p class="note">Verdicts: {_e(json.dumps(s5["verdict_counts"]))}</p>{qua}</section>
<section><h2>6 · Targeted intervention — {_e(s6.get("segment_name", "n/a"))}</h2>
<p><b>Root cause hypothesis:</b> {_e(s6.get("root_cause_hypothesis", ""))}</p>
<p><b>Play:</b> {_e(iv.get("play", ""))} · <b>owner:</b> {_e(iv.get("owner", ""))} ·
<b>deploy by:</b> {_e(iv.get("deploy_by", ""))}</p><ul>{steps}</ul>
<table><tr><th>Metric</th><th>Baseline</th><th>Target</th></tr>{mets}</table>
<p class="note"><b>Checkpoint:</b> {_e(s6.get("measurement_plan", {}).get("checkpoint_date", ""))} ·
<b>Decision rule:</b> {_e(s6.get("measurement_plan", {}).get("decision_rule", ""))}</p></section>
<section><h2>7 · Token usage &amp; cost (this run)</h2><table>
<tr><th>Stage</th><th>Model</th><th>Input tok</th><th>Output tok</th><th>Cost</th></tr>{cost_html}
<tr><td><b>Total</b></td><td></td><td></td><td></td><td><b>${cost:.4f}</b></td></tr></table>
<p class="note">Full math and annual projection: COSTS.md and outputs/run_report.md</p></section>
</main></body></html>"""
    path = os.path.join(out_dir, "dashboard.html")
    with open(path, "w") as f:
        f.write(doc)
    return path
