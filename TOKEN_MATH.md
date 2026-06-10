# Submission A — Token Math Sheet

AI system for senior customer success operations: 750-account B2B SaaS portfolio,
$50,000/year token budget.

## 1. Pricing values used (USD per 1M tokens)

| Model | Input | Output | Role in system |
|---|---|---|---|
| claude-haiku-4-5 | $1.00 | $5.00 | Tier 1: high-volume classification, per-account briefs, alert triage |
| claude-sonnet-4-6 | $3.00 | $15.00 | Tier 2: synthesis, judgment, customer-facing drafting |
| claude-opus-4-8 | $5.00 | $25.00 | Tier 3: biweekly intervention design, shadow-judging |

Adjustments applied where marked:
- **Batch API**: 50% off all tokens (used for non-latency-sensitive daily review).
- **Prompt caching**: cached prefix reads at 0.1x input price; first write at 1.25x.
  Stage system prompts are stable, so after the first call per 5-min window the
  prefix is a cache read. The sheet conservatively prices **all** cached tokens
  at the 0.1x read rate plus a 5% overhead line that absorbs write premiums.
- Tier 0 (deterministic screening, guardrails, segment detection) is code, not
  tokens: **$0**.

## 2. Volume assumptions (from the operating scope)

| Driver | Value | Basis |
|---|---|---|
| Portfolio | 750 accounts | given |
| Business days / weeks | 260 / 52 | standard year |
| Daily review flag rate | 20% -> 150 LLM briefs/day | deterministic screen passes ~80% of healthy accounts; sim flagged 8/18 on a deliberately risk-heavy dataset, production tuned to ~20% |
| Inbound issues | 30/week | given |
| Escalation rate | ~33% of issues -> 10/week | sim: 5/12 escalated incl. guardrail overrides |
| Check-ins | 12/week, each = prep + follow-up | given (full cycle) |
| Quality reviews | 20/week | given ("outputs each week"; sim reviewed 8) |
| Interventions | 26/year + 26 measurement readouts | biweekly, design + checkpoint |
| 24/7 monitoring alerts | 40/day x 365 | health-delta/usage-anomaly events past the free deterministic filter |

## 3. Baseline workflow costs (cell-by-cell)

Per-unit token budgets are production-sized (full CRM record, 12-week usage
history, open tickets, last call note — larger than the small synthetic rows in
the simulation, whose measured values are the lower bound).

| # | Workflow | Model | Units/yr | Fresh in/unit | Cached in/unit | Out/unit | $/unit calc | $/unit | Annual |
|---|---|---|---|---|---|---|---|---|---|
| W1 | Daily account review (flagged briefs) | Haiku + Batch | 39,000 | 2,500 | 800 | 350 | (2500x$1 + 800x$0.10 + 350x$5)/1M x 0.5 | $0.00217 | **$84.45** |
| W2 | Daily portfolio prioritization | Sonnet | 260 | 42,000 | 600 | 2,500 | (42000x$3 + 600x$0.30 + 2500x$15)/1M | $0.16368 | **$42.56** |
| W3 | Inbound issue triage | Haiku | 1,560 | 1,800 | 700 | 400 | (1800x$1 + 700x$0.10 + 400x$5)/1M | $0.00387 | **$6.04** |
| W4 | Escalation packets | Sonnet | 520 | 2,500 | 500 | 600 | (2500x$3 + 500x$0.30 + 600x$15)/1M | $0.01665 | **$8.66** |
| W5a | Check-in prep briefs | Sonnet | 624 | 3,500 | 700 | 800 | (3500x$3 + 700x$0.30 + 800x$15)/1M | $0.02271 | **$14.17** |
| W5b | Check-in follow-up (intake + recap) | Sonnet | 624 | 5,000 | 700 | 700 | (5000x$3 + 700x$0.30 + 700x$15)/1M | $0.02571 | **$16.04** |
| W6 | Output quality review (judge) | Sonnet | 1,040 | 2,200 | 900 | 700 | (2200x$3 + 900x$0.30 + 700x$15)/1M | $0.01737 | **$18.07** |
| W7a | Intervention design | Opus | 26 | 18,000 | 1,000 | 2,500 | (18000x$5 + 1000x$0.50 + 2500x$25)/1M | $0.15300 | **$3.98** |
| W7b | Intervention measurement readout | Sonnet | 26 | 6,000 | 600 | 1,200 | (6000x$3 + 600x$0.30 + 1200x$15)/1M | $0.03618 | **$0.94** |
| W8 | 24/7 monitoring alert triage | Haiku | 14,600 | 1,200 | 700 | 250 | (1200x$1 + 700x$0.10 + 250x$5)/1M | $0.00252 | **$36.79** |
| | **Baseline subtotal** | | | | | | | | **$231.70** |

## 4. Reliability & evaluation overhead (deliberate spend)

| # | Item | Model | Units/yr | $/unit | Annual | Rationale |
|---|---|---|---|---|---|---|
| R1 | Retry/validation overhead (5% of baseline) | mixed | — | — | $11.59 | schema-failed responses retried once (sim measured 1 retry / 47 calls ≈ 2%; 5% is conservative) |
| R2 | Weekly regression suite (200 golden cases) | Sonnet | 10,400 | $0.01350 | $140.40 | 2,000 in / 500 out per case; catches prompt/model drift before production |
| R3 | Opus shadow-judging 10% of triage + quality verdicts | Opus | 260 | $0.03000 | $7.80 | 2,500 in / 700 out; measures Haiku/Sonnet agreement, recalibrates thresholds |
| | **Operations total (baseline + overhead)** | | | | **$391.49** | |

## 5. Scenario analysis

| Scenario | Change vs baseline | Annual cost | % of $50k |
|---|---|---|---|
| Baseline | as above | $391 | 0.8% |
| Premium quality | 4x context everywhere; Sonnet->Opus on W2/W5/W6 | ~$1,580 | 3.2% |
| 10x portfolio (7,500 accounts) | volumes x10 on W1/W2/W8, x4 on human-cadence stages | ~$3,300 | 6.6% |
| Stress: premium + 10x + 2x retry rate | all of the above | ~$14,000 | 28% |

**Budget envelope check**: at a blended 85/15 input/output mix on Sonnet
($4.80/M effective), $50,000 buys ~10.4B tokens/year ≈ 28.5M tokens/day ≈
**38,000 tokens per account per day**. The proposed design uses well under 2%
of that at baseline.

## 6. Conclusion and allocation

The $50k constraint is **not binding at 750 accounts** when work is tiered
correctly — the deterministic tier handles the population scan for free and
LLM tokens are spent only where judgment is needed. Discipline here means not
inflating spend to meet the budget. Proposed allocation:

| Allocation | Amount |
|---|---|
| Run the **premium-quality** configuration (4x context, Opus on synthesis/judgment) | ~$1,600 |
| Evaluation program (regression suite, shadow-judging, A/B prompt tests) | ~$3,400 cap |
| Surge & growth reserve (volume spikes, renewal season, portfolio growth to ~10x) | ~$15,000 cap |
| Unallocated headroom (hard cap enforced by per-stage spend alerts at 80%) | ~$30,000 |

Governance: per-stage daily token caps in config, spend alerting at 80% of the
monthly run-rate, and the usage logger (outputs/usage_log.csv) as the audit
trail tying every dollar to a stage and model.
