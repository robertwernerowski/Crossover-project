# Run Report — Customer Success AI Workflow

Mode: **mock** | Reference date: 2026-05-01 | Models: triage=claude-haiku-4-5, synth=claude-sonnet-4-6, deep=claude-opus-4-8

## Measured usage by stage

| Stage | Calls | Input tok | Cached tok | Output tok | Retries | List cost | Effective cost |
|---|---|---|---|---|---|---|---|
| account_review | 8 | 2,242 | 1,664 | 830 | 0 | $0.0081 | $0.0034 |
| prioritization | 1 | 1,548 | 193 | 643 | 0 | $0.0149 | $0.0150 |
| issue_triage | 13 | 3,845 | 3,276 | 996 | 1 | $0.0121 | $0.0094 |
| escalation_packets | 5 | 1,945 | 530 | 709 | 0 | $0.0181 | $0.0170 |
| checkin_prep | 12 | 3,309 | 2,388 | 2,292 | 0 | $0.0515 | $0.0457 |
| quality_review | 8 | 2,181 | 1,664 | 916 | 0 | $0.0253 | $0.0215 |
| interventions | 1 | 893 | 238 | 342 | 0 | $0.0142 | $0.0145 |
| **TOTAL** | | | | | | **$0.1440** | **$0.1266** |

Effective cost applies prompt caching (0.1x on cached prefix reads, 1.25x first write) and the 50% Batch API discount on batchable stages.

## Annual projection at portfolio scale (750 accounts)

| Stage | Units/yr | Tok in/unit | Tok out/unit | Eff. cost/unit | Annual cost |
|---|---|---|---|---|---|
| account_review (flagged-account briefs (~20% of 750/day x 260 business days)) | 39,000 | 488 | 104 | $0.00042 | $16.57 |
| prioritization (daily portfolio synthesis) | 260 | 1,741 | 643 | $0.01501 | $3.90 |
| issue_triage (inbound issues (30/week)) | 1,560 | 548 | 77 | $0.00073 | $1.13 |
| escalation_packets (escalation packets (~25% of issues)) | 416 | 495 | 142 | $0.00340 | $1.41 |
| checkin_prep (check-in briefs (12/week)) | 624 | 475 | 191 | $0.00381 | $2.38 |
| quality_review (output quality reviews (20/week)) | 1,040 | 481 | 114 | $0.00269 | $2.79 |
| interventions (biweekly intervention designs) | 26 | 1,131 | 342 | $0.01450 | $0.38 |
| monitor_alerts (24/7 monitoring alert triage (40/day)) | 14,600 | ~same as issue_triage | | $0.00073 | $10.60 |
| checkin_followup (check-in follow-up summaries (12/week)) | 624 | ~same as checkin_prep | | $0.00381 | $2.38 |
| **TOTAL projected** | | | | | **$41.55** |

Budget: $50,000/yr -> projected core spend $42 (0.1% of budget). Remaining headroom funds retries, context growth, eval sampling, and surge volume (see COSTS.md).

Caveat: this synthetic dataset's records are small, so measured per-unit context is ~5-10x below the production context budgets assumed in COSTS.md (which prices full CRM/usage/ticket history per account). Treat this projection as a lower bound; the Token Math Sheet is the budget-of-record.

## Reliability events this run

- issue_triage: 1 deterministic guardrail override(s): T003 (renewal in 19d with health 52)
- issue_triage: 1 malformed response(s) recovered via retry
- checkin_prep: continuity eval pass 12/12
- quality_review verdicts: {'approve': 2, 'revise': 2, 'block_and_escalate': 4}
- intervention deployed for segment ['A004', 'A008', 'A014', 'A017']
