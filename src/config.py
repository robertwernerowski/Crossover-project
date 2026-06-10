"""Configuration: model routing tiers, pricing, and annual volume assumptions.

Pricing values mirror the Token Math Sheet (TOKEN_MATH.md). All costs are
USD per million tokens. The batch discount and cache-read multiplier are
applied by the usage logger when a call is marked batchable / cache-hit.
"""

# Reference "today" for the simulation (data is dated late April 2026).
TODAY = "2026-05-01"

# Model tiers. Tier 0 (deterministic rules) is free and not listed.
MODEL_TRIAGE = "claude-haiku-4-5"   # high-volume classification / per-account briefs
MODEL_SYNTH = "claude-sonnet-4-6"   # synthesis, judgment, customer-facing drafting
MODEL_DEEP = "claude-opus-4-8"      # biweekly intervention design (low volume, high stakes)

# USD per 1M tokens: (input, output)
PRICING = {
    MODEL_TRIAGE: (1.00, 5.00),
    MODEL_SYNTH: (3.00, 15.00),
    MODEL_DEEP: (5.00, 25.00),
}

BATCH_DISCOUNT = 0.50        # Message Batches API: 50% off all tokens
CACHE_READ_MULT = 0.10       # cached prefix read: ~0.1x input price
CACHE_WRITE_MULT = 1.25      # cache write premium (5-minute TTL)

# Annual volume assumptions for the 750-account portfolio (used to project
# measured per-unit token usage to annual cost in the run report).
ANNUAL_VOLUMES = {
    # stage: (units per year, description)
    "account_review": (150 * 260, "flagged-account briefs (~20% of 750/day x 260 business days)"),
    "prioritization": (260, "daily portfolio synthesis"),
    "issue_triage": (30 * 52, "inbound issues (30/week)"),
    "escalation_packets": (8 * 52, "escalation packets (~25% of issues)"),
    "checkin_prep": (12 * 52, "check-in briefs (12/week)"),
    "checkin_followup": (12 * 52, "check-in follow-up summaries (12/week)"),
    "quality_review": (20 * 52, "output quality reviews (20/week)"),
    "interventions": (26, "biweekly intervention designs"),
    "monitor_alerts": (40 * 365, "24/7 monitoring alert triage (40/day)"),
}
