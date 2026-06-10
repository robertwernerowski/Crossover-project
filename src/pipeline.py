"""End-to-end customer success AI workflow simulation.

Stages (mirrors the daily/weekly operating cadence):
  1. account_review   - deterministic screen of all accounts (tier 0, free)
                        -> Haiku risk brief per flagged account  [batchable]
  2. prioritization   - Sonnet ranks attention list + portfolio patterns
  3. issue_triage     - Haiku routes each inbound ticket; deterministic
                        guardrails can force escalation; Sonnet builds
                        escalation packets for everything escalated
  4. checkin_prep     - Sonnet meeting briefs with prior-call continuity
  5. quality_review   - Sonnet judges junior drafts vs quality standards
  6. interventions    - segment detection (tier 0) -> Opus intervention design

Run:  python3 src/pipeline.py
Outputs land in outputs/ (stage JSON, usage_log.csv, run_report.md).
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import mocks
import prompts
from config import (ANNUAL_VOLUMES, MODEL_DEEP, MODEL_SYNTH, MODEL_TRIAGE,
                    PRICING, TODAY)
from evals import (days_until, quality_cross_check, triage_guardrail,
                   validate_checkin_brief, validate_escalation_packet,
                   validate_intervention, validate_prioritization,
                   validate_quality_review, validate_risk_brief,
                   validate_triage)
from llm import LLMRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "outputs")


def load(name):
    with open(os.path.join(DATA, name), newline="") as f:
        return list(csv.DictReader(f))


def save(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2)


# ------------------------------------------------- tier 0: deterministic screen

def screen_account(acct, usage_rows, today):
    """Free rule-based risk score; only flagged accounts consume LLM tokens."""
    health = int(acct["current_health_score"])
    drop = int(acct["previous_health_score"]) - health
    score, drivers = 0, []
    if health < 60:
        score += 30; drivers.append(f"health score critical at {health}")
    elif health <= 70:
        score += 15; drivers.append(f"health score weak at {health}")
    if drop >= 15:
        score += 20; drivers.append(f"health dropped {drop} pts since last period")
    elif drop >= 8:
        score += 10; drivers.append(f"health dropped {drop} pts since last period")
    if acct["product_usage_trend"] == "declining":
        score += 15; drivers.append("product usage declining")
    if int(acct["support_ticket_count_30d"]) >= 5:
        score += 10; drivers.append(f"{acct['support_ticket_count_30d']} tickets in 30d")
    if int(acct["nps_score"]) <= 4:
        score += 10; drivers.append(f"NPS {acct['nps_score']}")
    renewal_days = days_until(today, acct["renewal_date"])
    if renewal_days <= 60 and health < 75:
        score += 15; drivers.append(f"renewal in {renewal_days}d with sub-par health")
    latest = [u for u in usage_rows if u["account_id"] == acct["account_id"]]
    usage_trend = latest[-1]["usage_trend"] if latest else "unknown"
    return {"risk_score": score, "drivers": drivers or ["no major risk drivers"],
            "health_drop": drop, "usage_trend": usage_trend,
            "renewal_days": renewal_days}


def account_context(acct, usage_rows, tickets, notes):
    """Compact context block for the user turn of per-account calls."""
    u = [f"{r['event_date']}: {r['active_users']} active / {r['key_feature_users']} key-feature users ({r['usage_trend']})"
         for r in usage_rows if r["account_id"] == acct["account_id"]]
    t = [f"{r['ticket_id']} [{r['severity']}/{r['customer_sentiment']}]: {r['issue_summary']}"
         for r in tickets if r["account_id"] == acct["account_id"]]
    n = next((r for r in notes if r["account_id"] == acct["account_id"]), None)
    parts = [f"ACCOUNT: {json.dumps(acct)}", "USAGE HISTORY: " + (" | ".join(u) or "n/a"),
             "OPEN TICKETS: " + (" | ".join(t) or "none")]
    if n:
        parts.append(f"LAST CALL ({n['call_date']}): {n['summary']}; goal: {n['customer_goal']}; "
                     f"blocker: {n['risk_or_blocker']}; follow-ups: {n['follow_up_items']}")
    return "\n".join(parts)


# --------------------------------------------------------------------- stages

def stage1_account_review(runner, accounts, usage, tickets, notes):
    flagged, all_screens = [], {}
    for acct in accounts:
        signals = screen_account(acct, usage, TODAY)
        all_screens[acct["account_id"]] = signals
        if signals["risk_score"] < 35:
            continue
        brief = runner.call(
            stage="account_review", model=MODEL_TRIAGE,
            system=prompts.ACCOUNT_REVIEW,
            user=account_context(acct, usage, tickets, notes)
            + f"\nDETERMINISTIC SCREEN: {json.dumps(signals)}",
            mock_fn=mocks.risk_brief(acct, signals, TODAY),
            validator=validate_risk_brief, batchable=True, max_tokens=600)
        flagged.append({"acct": acct, "signals": signals, "brief": brief})
    expansion = [a for a in accounts
                 if a["expansion_signal"] == "high" and int(a["current_health_score"]) >= 75]
    save("01_account_review.json", {
        "screened": len(accounts), "flagged": len(flagged),
        "screen_scores": {k: v["risk_score"] for k, v in all_screens.items()},
        "briefs": [f["brief"] for f in flagged],
        "expansion_candidates": [a["account_id"] for a in expansion]})
    return flagged, expansion


def stage2_prioritization(runner, accounts, flagged, expansion):
    table = "\n".join(
        f"{a['account_id']} | {a['account_name']} | {a['segment']} | ${a['contract_value']} | "
        f"renews {a['renewal_date']} | health {a['current_health_score']} "
        f"(prev {a['previous_health_score']}) | {a['product_usage_trend']} | "
        f"tickets30d {a['support_ticket_count_30d']} | NPS {a['nps_score']} | "
        f"expansion {a['expansion_signal']}" for a in accounts)
    briefs = json.dumps([f["brief"] for f in flagged], indent=1)
    plan = runner.call(
        stage="prioritization", model=MODEL_SYNTH,
        system=prompts.PRIORITIZATION,
        user=f"DATE: {TODAY}\nPORTFOLIO TABLE:\n{table}\n\nTODAY'S RISK BRIEFS:\n{briefs}",
        mock_fn=mocks.prioritization(flagged, expansion, TODAY),
        validator=validate_prioritization, max_tokens=2000)
    save("02_prioritization.json", plan)
    return plan


def stage3_issue_triage(runner, tickets, accounts, usage, notes):
    by_id = {a["account_id"]: a for a in accounts}
    results, escalations, failures = [], [], []
    for t in tickets:
        acct = by_id[t["account_id"]]
        try:
            route = runner.call(
                stage="issue_triage", model=MODEL_TRIAGE,
                system=prompts.ISSUE_TRIAGE,
                user=f"TICKET: {json.dumps(t)}\n" + account_context(acct, usage, tickets, notes),
                mock_fn=mocks.triage(t, acct, TODAY),
                validator=validate_triage, max_tokens=700)
        except RuntimeError as e:
            failures.append({"ticket_id": t["ticket_id"], "error": str(e),
                             "disposition": "routed to human queue"})
            continue
        final, override = triage_guardrail(t, acct, route, TODAY)
        route["final_route"] = final
        route["guardrail_override"] = override
        results.append(route)
        if final == "escalate":
            packet = runner.call(
                stage="escalation_packets", model=MODEL_SYNTH,
                system=prompts.ESCALATION_PACKET,
                user=f"ESCALATED TICKET: {json.dumps(t)}\nMODEL ROUTING: {json.dumps(route)}\n"
                     + account_context(acct, usage, tickets, notes),
                mock_fn=mocks.escalation_packet(t, acct, TODAY),
                validator=validate_escalation_packet, max_tokens=800)
            escalations.append(packet)
    save("03_issue_triage.json", {
        "routes": results, "escalation_packets": escalations,
        "validation_failures": failures,
        "route_counts": {r: sum(1 for x in results if x["final_route"] == r)
                         for r in ("immediate_resolution", "scheduled_follow_up", "escalate")},
        "guardrail_overrides": [
            {"ticket_id": x["ticket_id"], "model_route": x["route"], "reason": x["guardrail_override"]}
            for x in results if x["guardrail_override"]]})
    return results, escalations


def stage4_checkin_prep(runner, checkins, accounts, usage, tickets, notes):
    by_id = {a["account_id"]: a for a in accounts}
    briefs = []
    for c in checkins:
        acct = by_id[c["account_id"]]
        note = next((n for n in notes if n["account_id"] == c["account_id"]), None)
        acct_tickets = [t for t in tickets if t["account_id"] == c["account_id"]]
        u = [r for r in usage if r["account_id"] == c["account_id"]]
        trend = u[-1]["usage_trend"] if u else "unknown"
        brief = runner.call(
            stage="checkin_prep", model=MODEL_SYNTH,
            system=prompts.CHECKIN_PREP,
            user=f"CHECK-IN: {json.dumps(c)}\n" + account_context(acct, usage, tickets, notes),
            mock_fn=mocks.checkin_brief(c, acct, note, acct_tickets, trend, TODAY),
            validator=validate_checkin_brief, max_tokens=900)
        # Continuity eval: prior follow-up items must be carried into the brief.
        if note:
            expected = [i.strip() for i in note["follow_up_items"].split(";")]
            carried = " ".join(i["item"] for i in brief["continuity_items"])
            brief["_continuity_check"] = ("pass" if all(e[:25].lower() in carried.lower()
                                                        for e in expected) else "FAIL")
        briefs.append(brief)
    save("04_checkin_briefs.json", briefs)
    return briefs


def stage5_quality_review(runner, outputs_rows, accounts, notes):
    by_id = {a["account_id"]: a for a in accounts}
    reviews, spot_review = [], []
    for row in outputs_rows:
        acct = by_id[row["account_id"]]
        note = next((n for n in notes if n["account_id"] == row["account_id"]), None)
        note_txt = json.dumps(note) if note else "none"
        review = runner.call(
            stage="quality_review", model=MODEL_SYNTH,
            system=prompts.QUALITY_REVIEW,
            user=f"DRAFT OUTPUT: {json.dumps(row)}\nACCOUNT: {json.dumps(acct)}\n"
                 f"LAST CALL NOTE: {note_txt}\nSTANDARDS APPLY: {row['quality_standard_ids']}",
            mock_fn=mocks.quality_review(row, acct, note),
            validator=validate_quality_review, max_tokens=900)
        red_flags, disagreement = quality_cross_check(row, review)
        review["_heuristic_red_flags"] = red_flags
        if disagreement:
            spot_review.append(row["output_id"])
        reviews.append(review)
    save("05_quality_review.json", {
        "reviews": reviews,
        "verdict_counts": {v: sum(1 for r in reviews if r["verdict"] == v)
                           for v in ("approve", "revise", "block_and_escalate")},
        "judge_vs_heuristic_disagreements": spot_review})
    return reviews


def stage6_interventions(runner, accounts, usage, tickets, notes):
    members = [{"acct": a} for a in accounts
               if int(a["previous_health_score"]) - int(a["current_health_score"]) >= 15]
    if len(members) < 2:
        save("06_intervention.json", {"skipped": "no declining segment detected"})
        return None
    seg_name = "sharp_decliners_q2"
    ctx = "\n\n".join(account_context(m["acct"], usage, tickets, notes) for m in members)
    plan = runner.call(
        stage="interventions", model=MODEL_DEEP,
        system=prompts.INTERVENTION,
        user=f"SEGMENT: {seg_name} — health drop >=15 pts this period "
             f"({len(members)} of {len(accounts)} accounts)\nMEMBERS:\n{ctx}\n"
             f"TODAY: {TODAY}",
        mock_fn=mocks.intervention(seg_name, members, TODAY),
        validator=validate_intervention, max_tokens=1500)
    plan["_segment_members"] = [m["acct"]["account_id"] for m in members]
    save("06_intervention.json", plan)
    return plan


# --------------------------------------------------------------------- report

def write_report(runner, stage_meta):
    agg = runner.log.by_stage()
    lines = [
        "# Run Report — Customer Success AI Workflow", "",
        f"Mode: **{runner.mode}** | Reference date: {TODAY} | "
        f"Models: triage={MODEL_TRIAGE}, synth={MODEL_SYNTH}, deep={MODEL_DEEP}", "",
        "## Measured usage by stage", "",
        "| Stage | Calls | Input tok | Cached tok | Output tok | Retries | List cost | Effective cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tot_list = tot_eff = 0.0
    for stage, s in agg.items():
        tot_list += s["cost_list"]; tot_eff += s["cost_effective"]
        lines.append(f"| {stage} | {s['calls']} | {s['input_tokens']:,} | {s['cached_tokens']:,} | "
                     f"{s['output_tokens']:,} | {s['retries']} | ${s['cost_list']:.4f} | ${s['cost_effective']:.4f} |")
    lines += [f"| **TOTAL** | | | | | | **${tot_list:.4f}** | **${tot_eff:.4f}** |", "",
              "Effective cost applies prompt caching (0.1x on cached prefix reads, "
              "1.25x first write) and the 50% Batch API discount on batchable stages.", "",
              "## Annual projection at portfolio scale (750 accounts)", "",
              "| Stage | Units/yr | Tok in/unit | Tok out/unit | Eff. cost/unit | Annual cost |",
              "|---|---|---|---|---|---|"]
    annual_total = 0.0
    for stage, s in agg.items():
        if stage not in ANNUAL_VOLUMES:
            continue
        units, desc = ANNUAL_VOLUMES[stage]
        per_in = (s["input_tokens"] + s["cached_tokens"]) / s["calls"]
        per_out = s["output_tokens"] / s["calls"]
        per_cost = s["cost_effective"] / s["calls"]
        annual = per_cost * units
        annual_total += annual
        lines.append(f"| {stage} ({desc}) | {units:,} | {per_in:,.0f} | {per_out:,.0f} | "
                     f"${per_cost:.5f} | ${annual:,.2f} |")
    # Stages with no direct measurement are priced from the closest-shaped call.
    for proxy_stage, vol_key in (("issue_triage", "monitor_alerts"),
                                 ("checkin_prep", "checkin_followup")):
        if proxy_stage not in agg:
            continue
        s = agg[proxy_stage]
        per_cost = s["cost_effective"] / s["calls"]
        units, desc = ANNUAL_VOLUMES[vol_key]
        annual = per_cost * units
        annual_total += annual
        lines.append(f"| {vol_key} ({desc}) | {units:,} | ~same as {proxy_stage} | | "
                     f"${per_cost:.5f} | ${annual:,.2f} |")
    lines += [f"| **TOTAL projected** | | | | | **${annual_total:,.2f}** |", "",
              f"Budget: $50,000/yr -> projected core spend ${annual_total:,.0f} "
              f"({annual_total / 50000:.1%} of budget). Remaining headroom funds retries, "
              "context growth, eval sampling, and surge volume (see TOKEN_MATH.md).", "",
              "Caveat: this synthetic dataset's records are small, so measured per-unit "
              "context is ~5-10x below the production context budgets assumed in "
              "TOKEN_MATH.md (which prices full CRM/usage/ticket history per account). "
              "Treat this projection as a lower bound; the Token Math Sheet is the "
              "budget-of-record.", "",
              "## Reliability events this run", ""]
    lines += [f"- {m}" for m in stage_meta]
    with open(os.path.join(OUT, "run_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return tot_list, tot_eff, annual_total


def main():
    os.makedirs(OUT, exist_ok=True)
    accounts = load("accounts.csv")
    usage = load("usage_events.csv")
    tickets = load("support_tickets.csv")
    notes = load("call_notes.csv")
    checkins = load("scheduled_checkins.csv")
    junior = load("junior_outputs.csv")

    runner = LLMRunner()
    print(f"[mode={runner.mode}] {len(accounts)} accounts, {len(tickets)} tickets, "
          f"{len(checkins)} check-ins, {len(junior)} outputs to review")

    meta = []
    flagged, expansion = stage1_account_review(runner, accounts, usage, tickets, notes)
    print(f"stage1 account_review: {len(flagged)}/{len(accounts)} flagged, "
          f"{len(expansion)} expansion candidates")

    plan = stage2_prioritization(runner, accounts, flagged, expansion)
    print(f"stage2 prioritization: top account {plan['attention_list'][0]['account_id']}, "
          f"{len(plan['portfolio_patterns'])} portfolio patterns")

    routes, escalations = stage3_issue_triage(runner, tickets, accounts, usage, notes)
    overrides = [r for r in routes if r["guardrail_override"]]
    retries = sum(r.retries for r in runner.log.records if r.stage == "issue_triage")
    print(f"stage3 issue_triage: {len(routes)} routed, {len(escalations)} escalated, "
          f"{len(overrides)} guardrail overrides, {retries} retries")
    meta.append(f"issue_triage: {len(overrides)} deterministic guardrail override(s): "
                + "; ".join(f"{r['ticket_id']} ({r['guardrail_override']})" for r in overrides))
    meta.append(f"issue_triage: {retries} malformed response(s) recovered via retry")

    briefs = stage4_checkin_prep(runner, checkins, accounts, usage, tickets, notes)
    cont_fail = [b["checkin_id"] for b in briefs if b.get("_continuity_check") == "FAIL"]
    print(f"stage4 checkin_prep: {len(briefs)} briefs, continuity check failures: {cont_fail or 'none'}")
    meta.append(f"checkin_prep: continuity eval pass {len(briefs) - len(cont_fail)}/{len(briefs)}")

    reviews = stage5_quality_review(runner, junior, accounts, notes)
    vc = {v: sum(1 for r in reviews if r["verdict"] == v)
          for v in ("approve", "revise", "block_and_escalate")}
    print(f"stage5 quality_review: {vc}")
    meta.append(f"quality_review verdicts: {vc}")

    iv = stage6_interventions(runner, accounts, usage, tickets, notes)
    if iv:
        print(f"stage6 intervention: '{iv['intervention']['play']}' for "
              f"{len(iv['_segment_members'])} accounts, checkpoint {iv['measurement_plan']['checkpoint_date']}")
        meta.append(f"intervention deployed for segment {iv['_segment_members']}")

    runner.log.write_csv(os.path.join(OUT, "usage_log.csv"))
    tot_list, tot_eff, annual = write_report(runner, meta)
    print(f"\nTokens+cost: list ${tot_list:.4f} | effective ${tot_eff:.4f} per full cycle")
    print(f"Annual projection at 750-account scale: ${annual:,.2f} vs $50,000 budget")
    print(f"Artifacts: outputs/*.json, outputs/usage_log.csv, outputs/run_report.md")


if __name__ == "__main__":
    main()
