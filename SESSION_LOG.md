# Submission C — Claude Code Session Log

How Claude Code was used as a thinking and building partner for this
assessment. Chronological; each step notes what was asked, what came back,
and what decision it drove.

## 1. Data exploration before any design

Read all eight dataset files first. Key observations that shaped the design:

- `accounts.csv` carries both current and previous health scores → a free
  deterministic delta signal; no LLM needed to *find* risk, only to *explain
  and act on* it. This became the Tier-0/Tier-1 split.
- `call_notes.csv` has explicit `follow_up_items` → continuity is testable,
  not aspirational. That became an automated eval (every prior follow-up must
  reappear in the check-in brief).
- `junior_outputs.csv` rows reference specific standard IDs → quality review
  can be judged standard-by-standard rather than holistically, which makes
  the verdict auditable.
- `support_tickets.csv` includes severity + sentiment + renewal context →
  enough signal for deterministic escalation floors under the model's routing.

## 2. Pricing grounded via the claude-api skill, not memory

Loaded the Claude API reference skill before writing any model/pricing code.
Pulled: Haiku 4.5 $1/$5, Sonnet 4.6 $3/$15, Opus 4.8 $5/$25 per MTok, Batch
API −50%, cache reads ~0.1x / writes 1.25x, and the prompt-caching design
rule (stable prefix first, volatile content in the user turn). That rule is
why `src/prompts.py` keeps system prompts frozen per stage and all
per-account context goes in the user message.

## 3. Architecture decision: tiered routing instead of one big agent

Considered a single agentic loop over the whole portfolio vs a staged
pipeline. Chose the pipeline: population-scale work (750/day) is mostly
mechanical screening that code does for free and deterministically, while the
LLM adds value on flagged accounts, judgment calls, and synthesis. This is
also what makes the $50k budget trivially sufficient — the token math
followed from the architecture, not the other way around.

Environment check: no `ANTHROPIC_API_KEY` in this sandbox → designed the
runner with a real-API path (prompt caching, usage from `response.usage`) and
a deterministic mock path sharing the same validation/guardrail/logging code,
so the simulation is runnable anywhere and the measured token accounting is
real in both modes.

## 4. Build

Wrote `config.py` → `llm.py` → `prompts.py` → `evals.py` → `mocks.py` →
`pipeline.py`. Deliberate choices:

- Every prompt demands JSON-only output with an explicit schema, and every
  call site passes a validator; a failed validation retries once then raises
  to the escalation path. One mock (T005) intentionally returns truncated
  JSON on its first attempt so the retry path is exercised in every run.
- Guardrails return `(final_route, override_reason)` so overrides are logged
  artifacts, not silent mutations.
- The usage logger computes both list cost and effective cost (cache + batch)
  per call, so the run report can show what the optimizations are worth.

## 5. Run, observe, debug

First end-to-end run worked but surfaced two real issues from the output:

1. **Quality judge calibration**: verdicts were `approve: 0` — even
   objectively decent drafts (O005 expansion plan) failed. Traced to the mock
   judge's QS004 length heuristic (>100 chars) sitting right at the length of
   two good drafts. Loosened to >80 → verdicts became approve 2 / revise 2 /
   block 4, matching my own reading of the eight drafts.
2. **Projection gaps**: the annual projection skipped check-in follow-ups
   (priced but never measured). Generalized the proxy-pricing so unmeasured
   stages are priced from the closest-shaped measured call, and added an
   explicit caveat that the small synthetic rows make measured context a
   lower bound vs the production-sized budgets in COSTS.md.

Verified the interesting events in the committed run: guardrail override on
T003 (renewal in 19 days, health 52 — model said `scheduled_follow_up`,
floor forced `escalate`), 1 retry recovery, 12/12 continuity passes, segment
detection picking exactly the four sharp decliners (A004, A008, A014, A017).

## 6. Token math

Did the cost math cell-by-cell with production-sized context budgets (the
synthetic rows are ~5–10x smaller than a real account 360). Baseline came out
at ~$232/yr core + ~$160 eval/retry overhead — under 1% of budget. Rather
than inflating the design to "use" the budget, the sheet shows the envelope
math ($50k ≈ 38k tokens/account/day), a premium configuration, a 10x-scale
scenario, and an explicit allocation with caps and spend alerting. The
honest conclusion: with tiering + caching + batching, the binding constraints
are review capacity and latency, not tokens.

## 7. What I'd do next with more time

- Run the pipeline in real mode and diff mock vs real token counts to
  calibrate the estimator.
- Golden-set regression harness (R2 in the token sheet) wired into CI.
- Replace the single-day simulation with a multi-day loop to demonstrate
  intervention measurement (deploy → checkpoint → decision rule) on shifting
  synthetic data.
