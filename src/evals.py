"""Eval checks and deterministic guardrails.

Three layers of reliability:
  1. Schema validators -- every LLM response must parse and carry the required
     fields/enums; failures retry once, then route to human escalation.
  2. Deterministic guardrails -- business rules that override or second-guess
     model routing (e.g. renewal inside 45 days can never be quietly
     'scheduled for follow-up'). Overrides are logged, never silent.
  3. Cross-checks -- judge verdicts are compared against heuristic flags;
     disagreements are surfaced in the run report for human spot-review.
"""

from datetime import date


def _require(obj, fields):
    return [f"missing field: {f}" for f in fields if f not in obj]


def _enum(obj, field_name, allowed):
    if field_name in obj and obj[field_name] not in allowed:
        return [f"{field_name} must be one of {sorted(allowed)}, got {obj[field_name]!r}"]
    return []


def validate_risk_brief(obj):
    p = _require(obj, ["account_id", "risk_level", "risk_types", "drivers",
                       "recommended_action", "needs_human", "rationale"])
    p += _enum(obj, "risk_level", {"low", "medium", "high"})
    if not isinstance(obj.get("needs_human"), bool):
        p.append("needs_human must be boolean")
    return p


def validate_prioritization(obj):
    p = _require(obj, ["attention_list", "expansion_watchlist", "portfolio_patterns"])
    for item in obj.get("attention_list", []):
        p += _require(item, ["account_id", "rank", "reason", "owner_action"])
    for pat in obj.get("portfolio_patterns", []):
        if len(pat.get("accounts", [])) < 2:
            p.append(f"pattern '{pat.get('pattern', '?')}' cites <2 accounts")
    return p


def validate_triage(obj):
    p = _require(obj, ["ticket_id", "route", "priority", "reasoning",
                       "first_action", "owner"])
    p += _enum(obj, "route", {"immediate_resolution", "scheduled_follow_up", "escalate"})
    p += _enum(obj, "priority", {"P1", "P2", "P3"})
    if obj.get("route") == "immediate_resolution" and not obj.get("draft_reply"):
        p.append("immediate_resolution requires a draft_reply")
    return p


def validate_escalation_packet(obj):
    return _require(obj, ["ticket_id", "account_id", "severity_assessment",
                          "revenue_at_risk_usd", "recommended_owner",
                          "talking_points", "deadline", "fallback_plan"])


def validate_checkin_brief(obj):
    return _require(obj, ["checkin_id", "account_id", "objectives",
                          "continuity_items", "talking_points",
                          "risks_to_address", "success_criteria",
                          "post_call_actions"])


def validate_quality_review(obj):
    p = _require(obj, ["output_id", "standard_results", "verdict"])
    p += _enum(obj, "verdict", {"approve", "revise", "block_and_escalate"})
    if obj.get("verdict") == "revise" and not obj.get("revision_instructions"):
        p.append("revise verdict requires revision_instructions")
    return p


def validate_intervention(obj):
    p = _require(obj, ["segment_name", "root_cause_hypothesis",
                       "intervention", "measurement_plan"])
    mp = obj.get("measurement_plan", {})
    if not mp.get("metrics"):
        p.append("measurement_plan must define metrics")
    if not mp.get("decision_rule"):
        p.append("measurement_plan must define a decision_rule")
    return p


# ---------------------------------------------------------------- guardrails

def days_until(today_iso: str, future_iso: str) -> int:
    return (date.fromisoformat(future_iso) - date.fromisoformat(today_iso)).days


def triage_guardrail(ticket, account, route_obj, today):
    """Return (final_route, override_reason|None).

    Deterministic floor under the model's routing decision: conditions that
    must escalate regardless of what the model chose.
    """
    renewal_days = days_until(today, account["renewal_date"])
    force = None
    if ticket["severity"] == "High" and ticket["customer_sentiment"] in ("frustrated", "negative"):
        force = "high severity + negative sentiment"
    elif renewal_days <= 45 and int(account["current_health_score"]) < 70:
        force = f"renewal in {renewal_days}d with health {account['current_health_score']}"
    elif int(account["contract_value"]) >= 200_000 and ticket["severity"] == "High":
        force = "high-severity issue on strategic (>=$200k) account"

    if force and route_obj["route"] != "escalate":
        return "escalate", force
    return route_obj["route"], None


def quality_cross_check(output_row, review_obj):
    """Heuristic red flags vs judge verdict; disagreement -> human spot-review."""
    draft = output_row["draft_text"].lower()
    red_flags = []
    if len(draft) < 140:
        red_flags.append("draft under ~35 words — unlikely to be actionable")
    if any(w in draft for w in ("soon", "sometime", "let us know")) and "by " not in draft:
        red_flags.append("vague timing language with no concrete commitment")
    if "probably going to churn" in draft or "definitely" in draft:
        red_flags.append("absolute/overstated risk language")
    disagreement = bool(red_flags) and review_obj["verdict"] == "approve"
    return red_flags, disagreement
