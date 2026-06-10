# Customer Success AI Workflow

A runnable end-to-end AI system that handles the core operational work of a
senior customer success function for a ~750-account B2B SaaS portfolio:
daily account review, prioritization, inbound issue handling, customer
check-in support, output quality review, and targeted interventions — within
a $50,000/year token budget.

## Quick start

Requires only Python 3.8+ — **no packages, no API key, no setup**:

```
python3 src/pipeline.py        # Windows: py src\pipeline.py
```

Then **open `outputs/dashboard.html` in your browser** to see the full result:
attention list, risk briefs, issue routing with escalations, check-in briefs,
quality verdicts, the intervention plan, and token/cost telemetry.
(A pre-generated copy is already committed, so you can open it without
running anything.)

With `ANTHROPIC_API_KEY` set (and `pip install anthropic`), the same pipeline
calls the Claude API with prompt caching enabled; without a key it runs a
deterministic mock LLM so the full workflow — routing, guardrails, evals,
escalation, token/cost accounting — executes end-to-end offline.

Other artifacts in `outputs/`:
`01_account_review.json` … `06_intervention.json`, `usage_log.csv` (per-call
tokens + cost), `run_report.md` (per-stage rollup + annual budget projection).

## Model selection — the core design decision

The system's economics and quality both follow from **matching each task to
the cheapest model that is reliably good at it**, with deterministic code
(free) underneath everything. Pricing reference: [COSTS.md](COSTS.md).

| Tier | Model | $/MTok in/out | Used for | Why this model |
|---|---|---|---|---|
| 0 | none (Python rules) | $0 | Screening all 750 accounts/day, escalation floors, segment detection | Health deltas, ticket counts, and renewal windows are arithmetic, not judgment. Running an LLM over the whole portfolio daily would multiply cost ~5x for zero added signal. |
| 1 | **Haiku 4.5** | $1 / $5 | Per-account risk briefs (~150/day), inbound issue triage (30/wk), 24/7 alert triage (~40/day) | These are bounded classification/summarization tasks with a strict JSON schema — exactly what a small fast model does well. Volume lives here: ~93% of all calls. Sent via **Batch API (−50%)** where latency doesn't matter (overnight account review). |
| 2 | **Sonnet 4.6** | $3 / $15 | Portfolio prioritization (1/day), check-in briefs + follow-ups (24/wk), quality review judging (20/wk), escalation packets (~10/wk) | These need cross-document synthesis, judgment against standards, and customer-ready prose. Haiku measurably under-performs on multi-source reasoning; Opus adds cost without changing the verdicts on tasks this bounded. |
| 3 | **Opus 4.8** | $5 / $25 | Biweekly intervention design (26/yr), shadow-judging 10% of tier-1/2 verdicts | Highest-stakes, lowest-volume work: a wrong intervention burns two weeks across a whole segment. At 26 designs/year, the Opus premium costs ~$4/yr total — quality per dollar is unbeatable here. |

Three cost levers applied on top of routing (all measured in `run_report.md`):

1. **Prompt caching** — per-stage system prompts are frozen (see
   `src/prompts.py`); all volatile account context goes in the user turn, so
   the prefix is a 0.1x cache read after the first call.
2. **Batch API** — the daily account review is overnight work → 50% off.
3. **Escalation asymmetry** — cheap models are allowed to say "not sure":
   schema failures and guardrail triggers route up (to Sonnet packets or
   humans), never silently down.

**Result** (from [COSTS.md](COSTS.md), the budget-of-record): baseline ≈
**$392/yr (<1% of the $50k budget)**; a premium configuration (4x context,
Opus on all synthesis) ≈ $1.6k/yr; 10x portfolio growth ≈ $3.3k/yr. The
binding constraint is human review capacity, not tokens — so the budget is
allocated to an eval program and surge reserve rather than burned on
unnecessarily large models.

## Architecture

```
                        ┌────────────────────────────────────────────────┐
 24/7 intake            │  TIER 0 — deterministic, $0                    │
 ┌───────────────┐      │  • health/usage/ticket screening (all 750/day) │
 │ health deltas │─────▶│  • triage guardrails (renewal/severity floors) │
 │ usage events  │      │  • segment detection (declining cohorts)       │
 │ support queue │      └──────────────┬─────────────────────────────────┘
 └───────────────┘                     │ only flagged / inbound work
                                       ▼
        ┌──────────────────────────────────────────────────────┐
        │  TIER 1 — Haiku 4.5 (high volume, cheap)             │
        │  S1 account risk briefs (Batch API)  S3 issue triage │
        │  24/7 monitoring alert triage                        │
        └──────────────┬───────────────────────────────────────┘
                       │ briefs, routes, uncertainty flags
                       ▼
        ┌──────────────────────────────────────────────────────┐
        │  TIER 2 — Sonnet 4.6 (synthesis & judgment)          │
        │  S2 portfolio prioritization   S4 check-in briefs    │
        │  S5 quality review (judge)     escalation packets    │
        └──────────────┬───────────────────────────────────────┘
                       │ biweekly declining segment
                       ▼
        ┌──────────────────────────────────────────────────────┐
        │  TIER 3 — Opus 4.8 (low volume, high stakes)         │
        │  S6 intervention design + measurement plan           │
        └──────────────────────────────────────────────────────┘

 Every LLM response → schema validator → (retry once) → guardrail check
 → on failure or forced condition → HUMAN ESCALATION QUEUE (never silent)
```

### Stage walkthrough (maps 1:1 to the operating scope)

| Stage | Scope item | What happens |
|---|---|---|
| S1 `account_review` | Daily account review | Free rule-based screen scores all accounts (sim: 18 standing in for 750); only flagged (~20%) get a Haiku risk brief via the Batch API. Expansion candidates detected deterministically. |
| S2 `prioritization` | Prioritization, portfolio patterns | Sonnet ranks the attention list by risk x ARR x renewal proximity and names portfolio-level patterns, each citing ≥2 accounts (validated). |
| S3 `issue_triage` | Inbound issues, routing | Haiku routes each issue to immediate_resolution / scheduled_follow_up / escalate with a draft reply where applicable. Deterministic guardrails *floor* the decision (e.g. renewal ≤45d + weak health can never be quietly parked) — overrides are logged, never silent. Everything escalated gets a Sonnet escalation packet (owner, revenue at risk, deadline, fallback plan). |
| S4 `checkin_prep` | Customer check-ins | Sonnet builds the meeting brief from account record, usage trend, open tickets and the prior call note. A continuity eval verifies every unresolved follow-up item from the last call appears in the brief (12/12 pass in the committed run). |
| S5 `quality_review` | Output quality review | Sonnet judges each junior draft standard-by-standard against `quality_standards.csv` → approve / revise (with instructions) / block_and_escalate. A heuristic cross-check flags judge-vs-rules disagreements for human spot review. |
| S6 `interventions` | Targeted interventions | Tier-0 detection finds the declining segment (health drop ≥15: A004, A008, A014, A017); Opus designs the corrective play with per-account first steps and a measurement plan (baselines, targets, checkpoint date, iterate/stop/expand decision rule). |
| 24/7 | Monitoring & intake | Architecture: event stream (health deltas, usage anomalies, new tickets) → tier-0 filter → Haiku alert triage → same three routing paths. Priced in COSTS.md (W8); the triage stage demonstrates the identical call shape. |

### Reliability & evaluation design

1. **Schema validation** (`src/evals.py`) — every response must parse to JSON
   with required fields/enums and conditional invariants (e.g.
   `immediate_resolution` requires a `draft_reply`). Failures retry once, then
   route to the human queue. The committed run includes one injected malformed
   response (ticket T005) recovered via retry.
2. **Deterministic guardrails** — business floors the model cannot undercut:
   high severity + negative sentiment, renewal ≤45d with health <70, or
   high-severity on ≥$200k accounts force escalation. The run shows T003
   overridden (`renewal in 19d with health 52`).
3. **Cross-checks** — quality-review verdicts compared against independent
   heuristics; disagreements surface in `run_report.md` for spot review.
4. **Continuity eval** — check-in briefs must carry forward prior commitments.
5. **Cost telemetry** — every call logged with stage, model, tokens, list vs
   effective cost; the report projects annual spend against the $50k budget.

## Repo map

```
data/         provided synthetic dataset (8 CSVs/MD)
src/config.py    model tiers, pricing, annual volume assumptions
src/prompts.py   per-stage system prompts (stable → cacheable)
src/llm.py       LLM runner: real API or mock, retry, usage/cost log
src/evals.py     schema validators, guardrails, cross-checks
src/mocks.py     deterministic offline response generators
src/pipeline.py  orchestrator (entry point)
outputs/      committed artifacts from the run described above
COSTS.md      cost reference: cell-by-cell token math behind the model choices
```
