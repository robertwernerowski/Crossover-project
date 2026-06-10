"""Deterministic mock LLM for offline runs (no ANTHROPIC_API_KEY).

Each function mirrors what the corresponding prompt asks the real model to
produce, derived rule-fully from the same structured inputs. This keeps the
end-to-end pipeline runnable and lets the eval/guardrail/escalation logic be
exercised for real. One mock (ticket T005) returns malformed output on its
first attempt to demonstrate the validate->retry path.
"""

import json

from evals import days_until


def risk_brief(acct, signals, today):
    def fn(attempt):
        score = signals["risk_score"]
        level = "high" if score >= 60 else ("medium" if score >= 35 else "low")
        risk_types = []
        if signals["health_drop"] >= 15 or acct["product_usage_trend"] == "declining":
            risk_types.append("churn")
        if days_until(today, acct["renewal_date"]) <= 60:
            risk_types.append("renewal")
        if "implementation" in acct["notes"].lower() or "sso" in acct["notes"].lower():
            risk_types.append("implementation")
        if int(acct["nps_score"]) <= 4:
            risk_types.append("sentiment")
        drivers = signals["drivers"][:4]
        needs_human = (int(acct["contract_value"]) >= 150_000 and level == "high") \
            or "exec" in acct["notes"].lower()
        action = {
            "high": f"Same-week save plan: CSM call within 48h, address '{drivers[0]}'",
            "medium": "Add to weekly watchlist; targeted enablement outreach this week",
            "low": "Continue routine monitoring",
        }[level]
        return json.dumps({
            "account_id": acct["account_id"], "risk_level": level,
            "risk_types": risk_types or ["churn"], "drivers": drivers,
            "recommended_action": action, "needs_human": needs_human,
            "rationale": f"Risk score {score}: " + "; ".join(drivers[:2]),
        })
    return fn


def prioritization(flagged, expansion, today):
    def fn(attempt):
        ranked = sorted(
            flagged,
            key=lambda b: -(b["signals"]["risk_score"] * (1 + int(b["acct"]["contract_value"]) / 300_000)
                            + max(0, 90 - days_until(today, b["acct"]["renewal_date"]))),
        )
        attention = [{
            "account_id": b["acct"]["account_id"], "rank": i + 1,
            "reason": f"{b['brief']['risk_level']} risk, ${int(b['acct']['contract_value']):,} ARR, "
                      f"renewal in {days_until(today, b['acct']['renewal_date'])}d",
            "owner_action": b["brief"]["recommended_action"],
        } for i, b in enumerate(ranked)]
        watch = [{"account_id": a["account_id"],
                  "signal": f"expansion_signal={a['expansion_signal']}, health {a['current_health_score']}"}
                 for a in expansion]
        declining = [b["acct"]["account_id"] for b in flagged if b["signals"]["health_drop"] >= 15]
        ticket_heavy = [b["acct"]["account_id"] for b in flagged
                        if int(b["acct"]["support_ticket_count_30d"]) >= 6]
        renewal_window = [b["acct"]["account_id"] for b in flagged
                          if days_until(today, b["acct"]["renewal_date"]) <= 60]
        patterns = []
        if len(declining) >= 2:
            patterns.append({"pattern": "Sharp health-score declines (>=15 pts) clustered in accounts with champion loss or implementation blockers",
                             "accounts": declining,
                             "suggested_response": "Launch a save-segment intervention (see biweekly intervention stage)"})
        if len(ticket_heavy) >= 2:
            patterns.append({"pattern": "Rising support-ticket volume on integration/permissions topics",
                             "accounts": ticket_heavy,
                             "suggested_response": "Coordinate with support engineering on a root-cause sweep; publish a permissions enablement guide"})
        if len(renewal_window) >= 2:
            patterns.append({"pattern": "Multiple at-risk renewals inside 60 days",
                             "accounts": renewal_window,
                             "suggested_response": "Prepare renewal value snapshots and exec outreach for each"})
        return json.dumps({"attention_list": attention,
                           "expansion_watchlist": watch,
                           "portfolio_patterns": patterns})
    return fn


def triage(ticket, acct, today):
    def fn(attempt):
        if ticket["ticket_id"] == "T005" and attempt == 0:
            # Simulated malformed model output (truncated JSON) to exercise retry.
            return '{"ticket_id": "T005", "route": "immediate_resolution", "priority":'
        sev, sent = ticket["severity"], ticket["customer_sentiment"]
        if sev == "High" and sent in ("frustrated", "negative", "concerned"):
            route, priority, owner = "escalate", "P1", "csm"
            action = "Open escalation packet; CSM + support engineering joint owner within 24h"
            draft = None
        elif sev == "Low":
            route, priority, owner = "immediate_resolution", "P3", "ai_system"
            action = "Send tailored answer with relevant guide and offer a walkthrough"
            draft = (f"Hi {acct['account_name']} team — thanks for reaching out about "
                     f"'{ticket['issue_summary'][:60]}...'. Here is the specific guidance for your "
                     f"setup, plus a short guide. Happy to do a 20-minute walkthrough this week — "
                     f"I have held two slots for you.")
        else:
            route, priority, owner = "scheduled_follow_up", "P2", "csm"
            action = "Schedule investigation with support; reply to customer with timeline today"
            draft = None
        return json.dumps({
            "ticket_id": ticket["ticket_id"], "route": route, "priority": priority,
            "reasoning": f"{sev} severity, {sent} sentiment, status {ticket['current_status']}",
            "first_action": action, "draft_reply": draft, "owner": owner,
        })
    return fn


def escalation_packet(ticket, acct, today):
    def fn(attempt):
        return json.dumps({
            "ticket_id": ticket["ticket_id"], "account_id": acct["account_id"],
            "severity_assessment": f"{ticket['severity']} severity on {acct['segment']} account, "
                                   f"health {acct['current_health_score']}, sentiment {ticket['customer_sentiment']}",
            "revenue_at_risk_usd": int(acct["contract_value"]),
            "recommended_owner": "CSM + support engineering" if "sync" in ticket["issue_summary"].lower()
                                 or "sso" in ticket["issue_summary"].lower() else "CSM + leadership sponsor",
            "talking_points": [
                f"Acknowledge: {ticket['issue_summary'][:80]}",
                f"Commit to a named owner and a dated next update",
                f"Renewal context: {acct['renewal_date']} — protect the relationship now",
            ],
            "deadline": "next business day",
            "fallback_plan": "If unresolved in 5 business days, exec-to-exec call and remediation credit review",
        })
    return fn


def checkin_brief(checkin, acct, note, tickets, usage_trend, today):
    def fn(attempt):
        continuity = []
        if note:
            for item in note["follow_up_items"].split(";"):
                continuity.append({"item": item.strip(),
                                   "status": "open — verify before call"})
        topics = [t.strip() for t in checkin["topics_to_cover"].split(",")]
        talking = [f"Topic: {t}" for t in topics]
        talking.append(f"Usage trend: {usage_trend}; health {acct['current_health_score']} "
                       f"(was {acct['previous_health_score']})")
        for t in tickets:
            talking.append(f"Open ticket {t['ticket_id']}: {t['issue_summary'][:70]}")
        return json.dumps({
            "checkin_id": checkin["checkin_id"], "account_id": acct["account_id"],
            "objectives": [f"Advance: {topics[0]}", "Confirm owner + dated next steps"],
            "continuity_items": continuity,
            "talking_points": talking,
            "risks_to_address": [acct["notes"]],
            "success_criteria": f"Customer agrees to a dated plan on '{topics[0]}' and "
                                f"confirms engagement before renewal {acct['renewal_date']}",
            "post_call_actions": ["Send recap within 4h with owners/dates",
                                  "Update health watchlist and schedule follow-up checkpoint"],
        })
    return fn


# Keyword heuristics per quality standard (mock judge).
_QS_CHECKS = {
    "QS001": lambda d, ctx: any(k in d for k in ctx["keywords"]),
    "QS002": lambda d, ctx: any(k in d for k in ("phase", "plan", "step", "by ", "schedule", "training", "metrics", "escalate", "collect requirements")),
    "QS003": lambda d, ctx: "probably going to churn" not in d and "definitely" not in d,
    "QS004": lambda d, ctx: len(d) > 80 and "miss seeing" not in d,
    "QS005": lambda d, ctx: ("escalat" in d) or ctx["severity_low"],
    "QS006": lambda d, ctx: any(k in d for k in ctx["commitments"]) if ctx["commitments"] else True,
}


def quality_review(output_row, acct, note):
    def fn(attempt):
        d = output_row["draft_text"].lower()
        note_words = (note["risk_or_blocker"] + " " + note["follow_up_items"]).lower().split() if note else []
        ctx = {
            "keywords": [w.strip(".,") for w in (acct["notes"].lower().split() + note_words) if len(w) > 4],
            "severity_low": int(acct["current_health_score"]) >= 70,
            "commitments": [w.strip(".,").lower() for w in note["follow_up_items"].split() if len(w) > 5] if note else [],
        }
        results, fails = [], []
        for sid in output_row["quality_standard_ids"].split(";"):
            ok = bool(_QS_CHECKS[sid](d, ctx))
            results.append({"standard_id": sid, "pass": ok,
                            "evidence": ("met" if ok else "not met") + f" per {sid} heuristic on draft text"})
            if not ok:
                fails.append(sid)
        high_value = int(acct["contract_value"]) >= 150_000
        risky_fail = any(s in fails for s in ("QS003", "QS005")) and high_value
        if not fails:
            verdict, instr = "approve", None
        elif len(fails) == 1 and not risky_fail:
            verdict = "revise"
            instr = f"Fix {fails[0]}: add account-specific detail, concrete next steps with owner and date."
        else:
            verdict = "block_and_escalate"
            instr = f"Failed {', '.join(fails)} on a ${int(acct['contract_value']):,} account — route to CSM before sending."
        return json.dumps({"output_id": output_row["output_id"],
                           "standard_results": results, "verdict": verdict,
                           "revision_instructions": instr,
                           "rewritten_draft": None})
    return fn


def intervention(segment_name, members, today):
    def fn(attempt):
        steps = []
        for m in members:
            note = m["acct"]["notes"]
            if "champion" in note.lower() or "stakeholder" in note.lower():
                step = "Map stakeholders; secure a new executive sponsor meeting this week"
            elif "implementation" in note.lower() or "sso" in note.lower():
                step = "Technical escalation review with support engineering; unblock rollout"
            else:
                step = "Adoption reset call: re-anchor use case, agree success plan with dates"
            steps.append({"account_id": m["acct"]["account_id"], "step": step})
        return json.dumps({
            "segment_name": segment_name,
            "root_cause_hypothesis": "Declines cluster around lost champions and stalled "
                                     "implementations rather than product dissatisfaction; "
                                     "engagement collapsed before sentiment did.",
            "intervention": {
                "play": "Champion-rebuild + implementation-unblock sprint (2 weeks)",
                "per_account_steps": steps,
                "owner": "CS team lead (AI system drafts outreach, tracks completion)",
                "deploy_by": "2026-05-08",
            },
            "measurement_plan": {
                "metrics": [
                    {"name": "avg health score (segment)", "baseline": "50.0", "target": "+8 pts"},
                    {"name": "weekly active users (segment)", "baseline": "per usage_events 2026-04-24", "target": "+15%"},
                    {"name": "named champion coverage", "baseline": "1/4 accounts", "target": "4/4 accounts"},
                ],
                "checkpoint_date": "2026-05-15",
                "decision_rule": "If >=2 metrics hit target: expand play to next decile. "
                                 "If 1: iterate messaging and re-run 1 week. If 0: escalate "
                                 "segment to leadership with churn-reserve recommendation.",
            },
        })
    return fn
