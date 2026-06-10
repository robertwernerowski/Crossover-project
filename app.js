/* Customer Success AI — workflow engine (browser port of src/pipeline.py).
 *
 * Pure functions: parse the CSV dataset, run the six-stage workflow with the
 * same deterministic logic, guardrails, eval checks, and token/cost
 * accounting as the Python pipeline. UI lives in index.html.
 * Runs in any browser (no dependencies) and in Node for testing.
 */

const TODAY = "2026-05-01";
const PRICING = {
  "claude-haiku-4-5": [1.0, 5.0],
  "claude-sonnet-4-6": [3.0, 15.0],
  "claude-opus-4-8": [5.0, 25.0],
};
const M_TRIAGE = "claude-haiku-4-5", M_SYNTH = "claude-sonnet-4-6", M_DEEP = "claude-opus-4-8";
const BATCH_DISCOUNT = 0.5, CACHE_READ = 0.1, CACHE_WRITE = 1.25;

const ANNUAL_VOLUMES = {
  account_review: [150 * 260, "flagged-account briefs (~20% of 750/day x 260 days)"],
  prioritization: [260, "daily portfolio synthesis"],
  issue_triage: [30 * 52, "inbound issues (30/week)"],
  escalation_packets: [8 * 52, "escalation packets (~25% of issues)"],
  checkin_prep: [12 * 52, "check-in briefs (12/week)"],
  quality_review: [20 * 52, "output quality reviews (20/week)"],
  interventions: [26, "biweekly intervention designs"],
  monitor_alerts: [40 * 365, "24/7 monitoring alert triage (40/day)"],
  checkin_followup: [12 * 52, "check-in follow-up summaries (12/week)"],
};

const SYS_TOKENS = { // approx. system-prompt sizes (tokens), mirrors src/prompts.py
  account_review: 208, prioritization: 193, issue_triage: 252,
  escalation_packets: 106, checkin_prep: 199, quality_review: 208,
  interventions: 238,
};

// ----------------------------------------------------------------- csv

function parseCSV(text) {
  const rows = []; let row = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  const hdr = rows[0];
  return rows.slice(1).map(r => Object.fromEntries(hdr.map((h, i) => [h, r[i] ?? ""])));
}

// ----------------------------------------------------------------- helpers

const daysUntil = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400e3);
const est = s => Math.max(1, Math.floor(s.length / 4));

function makeLedger() {
  const calls = [], seen = new Set();
  return {
    calls,
    add(stage, model, userStr, outObj, batchable, retries) {
      const cached = SYS_TOKENS[stage] || 200;
      const write = !seen.has(stage); seen.add(stage);
      calls.push({ stage, model, inTok: est(userStr), cachedTok: cached, write,
                   outTok: est(JSON.stringify(outObj)), batchable, retries });
    },
    cost(c) {
      const [pi, po] = PRICING[c.model];
      let usd = (c.inTok * pi + c.cachedTok * pi * (c.write ? CACHE_WRITE : CACHE_READ)
                 + c.outTok * po) / 1e6;
      return c.batchable ? usd * BATCH_DISCOUNT : usd;
    },
    byStage() {
      const agg = {};
      for (const c of calls) {
        const s = agg[c.stage] ??= { model: c.model, calls: 0, inTok: 0, cachedTok: 0, outTok: 0, usd: 0, retries: 0 };
        s.calls++; s.inTok += c.inTok; s.cachedTok += c.cachedTok; s.outTok += c.outTok;
        s.usd += this.cost(c); s.retries += c.retries;
      }
      return agg;
    },
  };
}

function accountContext(acct, usage, tickets, notes) {
  const u = usage.filter(r => r.account_id === acct.account_id)
    .map(r => `${r.event_date}: ${r.active_users} active / ${r.key_feature_users} key (${r.usage_trend})`);
  const t = tickets.filter(r => r.account_id === acct.account_id)
    .map(r => `${r.ticket_id} [${r.severity}/${r.customer_sentiment}]: ${r.issue_summary}`);
  const n = notes.find(r => r.account_id === acct.account_id);
  let s = `ACCOUNT: ${JSON.stringify(acct)}\nUSAGE: ${u.join(" | ") || "n/a"}\nTICKETS: ${t.join(" | ") || "none"}`;
  if (n) s += `\nLAST CALL (${n.call_date}): ${n.summary}; goal: ${n.customer_goal}; blocker: ${n.risk_or_blocker}; follow-ups: ${n.follow_up_items}`;
  return s;
}

// ----------------------------------------------------------------- stage 1

function screenAccount(acct) {
  const health = +acct.current_health_score;
  const drop = +acct.previous_health_score - health;
  let score = 0; const drivers = [];
  if (health < 60) { score += 30; drivers.push(`health score critical at ${health}`); }
  else if (health <= 70) { score += 15; drivers.push(`health score weak at ${health}`); }
  if (drop >= 15) { score += 20; drivers.push(`health dropped ${drop} pts since last period`); }
  else if (drop >= 8) { score += 10; drivers.push(`health dropped ${drop} pts since last period`); }
  if (acct.product_usage_trend === "declining") { score += 15; drivers.push("product usage declining"); }
  if (+acct.support_ticket_count_30d >= 5) { score += 10; drivers.push(`${acct.support_ticket_count_30d} tickets in 30d`); }
  if (+acct.nps_score <= 4) { score += 10; drivers.push(`NPS ${acct.nps_score}`); }
  const renewalDays = daysUntil(TODAY, acct.renewal_date);
  if (renewalDays <= 60 && health < 75) { score += 15; drivers.push(`renewal in ${renewalDays}d with sub-par health`); }
  return { risk_score: score, drivers: drivers.length ? drivers : ["no major risk drivers"], health_drop: drop, renewal_days: renewalDays };
}

function riskBrief(acct, sig) {
  const level = sig.risk_score >= 60 ? "high" : sig.risk_score >= 35 ? "medium" : "low";
  const types = [];
  if (sig.health_drop >= 15 || acct.product_usage_trend === "declining") types.push("churn");
  if (sig.renewal_days <= 60) types.push("renewal");
  const lo = acct.notes.toLowerCase();
  if (lo.includes("implementation") || lo.includes("sso")) types.push("implementation");
  if (+acct.nps_score <= 4) types.push("sentiment");
  const action = level === "high"
    ? `Same-week save plan: CSM call within 48h, address '${sig.drivers[0]}'`
    : level === "medium" ? "Add to weekly watchlist; targeted enablement outreach this week"
    : "Continue routine monitoring";
  return {
    account_id: acct.account_id, account_name: acct.account_name, risk_level: level,
    risk_types: types.length ? types : ["churn"], drivers: sig.drivers.slice(0, 4),
    recommended_action: action,
    needs_human: (+acct.contract_value >= 150000 && level === "high") || lo.includes("exec"),
    rationale: `Risk score ${sig.risk_score}: ${sig.drivers.slice(0, 2).join("; ")}`,
  };
}

// ----------------------------------------------------------------- stage 2

function prioritize(flagged, expansion) {
  const ranked = [...flagged].sort((a, b) =>
    (b.sig.risk_score * (1 + +b.acct.contract_value / 300000) + Math.max(0, 90 - b.sig.renewal_days)) -
    (a.sig.risk_score * (1 + +a.acct.contract_value / 300000) + Math.max(0, 90 - a.sig.renewal_days)));
  const attention = ranked.map((f, i) => ({
    rank: i + 1, account_id: f.acct.account_id, account_name: f.acct.account_name,
    reason: `${f.brief.risk_level} risk, $${(+f.acct.contract_value).toLocaleString()} ARR, renewal in ${f.sig.renewal_days}d`,
    owner_action: f.brief.recommended_action,
  }));
  const declining = flagged.filter(f => f.sig.health_drop >= 15).map(f => f.acct.account_id);
  const ticketHeavy = flagged.filter(f => +f.acct.support_ticket_count_30d >= 6).map(f => f.acct.account_id);
  const renewalWin = flagged.filter(f => f.sig.renewal_days <= 60).map(f => f.acct.account_id);
  const patterns = [];
  if (declining.length >= 2) patterns.push({
    pattern: "Sharp health-score declines (>=15 pts) clustered around champion loss / implementation blockers",
    accounts: declining, suggested_response: "Launch a save-segment intervention (stage 6)" });
  if (ticketHeavy.length >= 2) patterns.push({
    pattern: "Rising support-ticket volume on integration/permissions topics",
    accounts: ticketHeavy, suggested_response: "Root-cause sweep with support engineering; publish enablement guide" });
  if (renewalWin.length >= 2) patterns.push({
    pattern: "Multiple at-risk renewals inside 60 days",
    accounts: renewalWin, suggested_response: "Renewal value snapshots + exec outreach for each" });
  return {
    attention_list: attention,
    expansion_watchlist: expansion.map(a => ({ account_id: a.account_id, account_name: a.account_name,
      signal: `expansion_signal=${a.expansion_signal}, health ${a.current_health_score}` })),
    portfolio_patterns: patterns,
  };
}

// ----------------------------------------------------------------- stage 3

function triageTicket(t, acct) {
  const sev = t.severity, sent = t.customer_sentiment;
  if (sev === "High" && ["frustrated", "negative", "concerned"].includes(sent))
    return { ticket_id: t.ticket_id, route: "escalate", priority: "P1",
      reasoning: `${sev} severity, ${sent} sentiment, status ${t.current_status}`,
      first_action: "Open escalation packet; CSM + support engineering joint owner within 24h",
      draft_reply: null, owner: "csm" };
  if (sev === "Low")
    return { ticket_id: t.ticket_id, route: "immediate_resolution", priority: "P3",
      reasoning: `${sev} severity, ${sent} sentiment, status ${t.current_status}`,
      first_action: "Send tailored answer with relevant guide and offer a walkthrough",
      draft_reply: `Hi ${acct.account_name} team — thanks for reaching out about "${t.issue_summary.slice(0, 60)}…". Here is the specific guidance for your setup, plus a short guide. Happy to do a 20-minute walkthrough this week — I have held two slots for you.`,
      owner: "ai_system" };
  return { ticket_id: t.ticket_id, route: "scheduled_follow_up", priority: "P2",
    reasoning: `${sev} severity, ${sent} sentiment, status ${t.current_status}`,
    first_action: "Schedule investigation with support; reply to customer with timeline today",
    draft_reply: null, owner: "csm" };
}

function triageGuardrail(t, acct, route) {
  const rd = daysUntil(TODAY, acct.renewal_date);
  let force = null;
  if (t.severity === "High" && ["frustrated", "negative"].includes(t.customer_sentiment))
    force = "high severity + negative sentiment";
  else if (rd <= 45 && +acct.current_health_score < 70)
    force = `renewal in ${rd}d with health ${acct.current_health_score}`;
  else if (+acct.contract_value >= 200000 && t.severity === "High")
    force = "high-severity issue on strategic (>=$200k) account";
  if (force && route.route !== "escalate") return ["escalate", force];
  return [route.route, null];
}

function escalationPacket(t, acct) {
  const lo = t.issue_summary.toLowerCase();
  return {
    ticket_id: t.ticket_id, account_id: acct.account_id, account_name: acct.account_name,
    severity_assessment: `${t.severity} severity on ${acct.segment} account, health ${acct.current_health_score}, sentiment ${t.customer_sentiment}`,
    revenue_at_risk_usd: +acct.contract_value,
    recommended_owner: (lo.includes("sync") || lo.includes("sso")) ? "CSM + support engineering" : "CSM + leadership sponsor",
    talking_points: [
      `Acknowledge: ${t.issue_summary.slice(0, 80)}`,
      "Commit to a named owner and a dated next update",
      `Renewal context: ${acct.renewal_date} — protect the relationship now`,
    ],
    deadline: "next business day",
    fallback_plan: "If unresolved in 5 business days, exec-to-exec call and remediation credit review",
  };
}

// ----------------------------------------------------------------- stage 4

function checkinBrief(c, acct, note, acctTickets) {
  const continuity = note ? note.follow_up_items.split(";").map(i => ({ item: i.trim(), status: "open — verify before call" })) : [];
  const topics = c.topics_to_cover.split(",").map(s => s.trim());
  const talking = topics.map(t => `Topic: ${t}`);
  talking.push(`Usage/health: ${acct.product_usage_trend}; health ${acct.current_health_score} (was ${acct.previous_health_score})`);
  for (const t of acctTickets) talking.push(`Open ticket ${t.ticket_id}: ${t.issue_summary.slice(0, 70)}`);
  const brief = {
    checkin_id: c.checkin_id, account_id: acct.account_id, account_name: acct.account_name,
    scheduled_date: c.scheduled_date, checkin_type: c.checkin_type, priority: c.priority,
    objectives: [`Advance: ${topics[0]}`, "Confirm owner + dated next steps"],
    continuity_items: continuity, talking_points: talking,
    risks_to_address: [acct.notes],
    success_criteria: `Customer agrees to a dated plan on '${topics[0]}' and confirms engagement before renewal ${acct.renewal_date}`,
    post_call_actions: ["Send recap within 4h with owners/dates", "Update health watchlist and schedule follow-up checkpoint"],
  };
  // continuity eval (same as pipeline): every prior follow-up must be carried
  if (note) {
    const carried = brief.continuity_items.map(i => i.item).join(" ").toLowerCase();
    const expected = note.follow_up_items.split(";").map(s => s.trim());
    brief.continuity_check = expected.every(e => carried.includes(e.slice(0, 25).toLowerCase())) ? "pass" : "FAIL";
  } else brief.continuity_check = "n/a";
  return brief;
}

// ----------------------------------------------------------------- stage 5

const QS_NAMES = { QS001: "Customer-specific context", QS002: "Actionability", QS003: "Risk accuracy",
  QS004: "Tone and clarity", QS005: "Escalation judgment", QS006: "Follow-up continuity" };

function qsCheck(sid, d, ctx) {
  switch (sid) {
    case "QS001": return ctx.keywords.some(k => d.includes(k));
    case "QS002": return ["phase", "plan", "step", "by ", "schedule", "training", "metrics", "escalate", "collect requirements"].some(k => d.includes(k));
    case "QS003": return !d.includes("probably going to churn") && !d.includes("definitely");
    case "QS004": return d.length > 80 && !d.includes("miss seeing");
    case "QS005": return d.includes("escalat") || ctx.severityLow;
    case "QS006": return ctx.commitments.length ? ctx.commitments.some(k => d.includes(k)) : true;
    default: return true;
  }
}

function qualityReview(row, acct, note) {
  const d = row.draft_text.toLowerCase();
  const noteWords = note ? (note.risk_or_blocker + " " + note.follow_up_items).toLowerCase().split(/\s+/) : [];
  const ctx = {
    keywords: (acct.notes.toLowerCase().split(/\s+/).concat(noteWords)).map(w => w.replace(/[.,]/g, "")).filter(w => w.length > 4),
    severityLow: +acct.current_health_score >= 70,
    commitments: note ? note.follow_up_items.split(/\s+/).filter(w => w.length > 5).map(w => w.replace(/[.,]/g, "").toLowerCase()) : [],
  };
  const results = [], fails = [];
  for (const sid of row.quality_standard_ids.split(";")) {
    const ok = qsCheck(sid, d, ctx);
    results.push({ standard_id: sid, name: QS_NAMES[sid], pass: ok });
    if (!ok) fails.push(sid);
  }
  const highValue = +acct.contract_value >= 150000;
  const riskyFail = fails.some(s => s === "QS003" || s === "QS005") && highValue;
  let verdict, instructions = null;
  if (!fails.length) verdict = "approve";
  else if (fails.length === 1 && !riskyFail) {
    verdict = "revise";
    instructions = `Fix ${fails[0]} (${QS_NAMES[fails[0]]}): add account-specific detail, concrete next steps with owner and date.`;
  } else {
    verdict = "block_and_escalate";
    instructions = `Failed ${fails.join(", ")} on a $${(+acct.contract_value).toLocaleString()} account — route to CSM before sending.`;
  }
  // independent heuristic cross-check (eval layer)
  const redFlags = [];
  if (row.draft_text.length < 140) redFlags.push("draft under ~35 words — unlikely to be actionable");
  if (["soon", "sometime", "let us know"].some(w => d.includes(w)) && !d.includes("by ")) redFlags.push("vague timing language, no concrete commitment");
  if (d.includes("probably going to churn") || d.includes("definitely")) redFlags.push("absolute/overstated risk language");
  return { output_id: row.output_id, account_id: row.account_id, output_type: row.output_type,
    draft_text: row.draft_text, standard_results: results, verdict, revision_instructions: instructions,
    red_flags: redFlags, disagreement: redFlags.length > 0 && verdict === "approve" };
}

// ----------------------------------------------------------------- stage 6

function intervention(members) {
  const steps = members.map(m => {
    const lo = m.notes.toLowerCase();
    let step;
    if (lo.includes("champion") || lo.includes("stakeholder")) step = "Map stakeholders; secure a new executive sponsor meeting this week";
    else if (lo.includes("implementation") || lo.includes("sso")) step = "Technical escalation review with support engineering; unblock rollout";
    else step = "Adoption reset call: re-anchor use case, agree success plan with dates";
    return { account_id: m.account_id, account_name: m.account_name, step };
  });
  return {
    segment_name: "sharp_decliners_q2",
    members: members.map(m => m.account_id),
    root_cause_hypothesis: "Declines cluster around lost champions and stalled implementations rather than product dissatisfaction; engagement collapsed before sentiment did.",
    intervention: { play: "Champion-rebuild + implementation-unblock sprint (2 weeks)",
      per_account_steps: steps, owner: "CS team lead (AI system drafts outreach, tracks completion)", deploy_by: "2026-05-08" },
    measurement_plan: {
      metrics: [
        { name: "avg health score (segment)", baseline: String((members.reduce((s, m) => s + +m.current_health_score, 0) / members.length).toFixed(1)), target: "+8 pts" },
        { name: "weekly active users (segment)", baseline: "usage_events 2026-04-24", target: "+15%" },
        { name: "named champion coverage", baseline: `1/${members.length} accounts`, target: `${members.length}/${members.length} accounts` },
      ],
      checkpoint_date: "2026-05-15",
      decision_rule: "If >=2 metrics hit target: expand play to next decile. If 1: iterate messaging and re-run 1 week. If 0: escalate segment to leadership with churn-reserve recommendation.",
    },
  };
}

// ----------------------------------------------------------------- engine

function runEngine(data) {
  const { accounts, usage, tickets, notes, checkins, junior } = data;
  const ledger = makeLedger();
  const events = [];

  // S1 — daily account review
  const flagged = [];
  const screens = {};
  for (const acct of accounts) {
    const sig = screenAccount(acct);
    screens[acct.account_id] = sig;
    if (sig.risk_score < 35) continue;
    const brief = riskBrief(acct, sig);
    ledger.add("account_review", M_TRIAGE,
      accountContext(acct, usage, tickets, notes) + JSON.stringify(sig), brief, true, 0);
    flagged.push({ acct, sig, brief });
  }
  const expansion = accounts.filter(a => a.expansion_signal === "high" && +a.current_health_score >= 75);

  // S2 — prioritization
  const plan = prioritize(flagged, expansion);
  ledger.add("prioritization", M_SYNTH,
    accounts.map(a => Object.values(a).join("|")).join("\n") + JSON.stringify(flagged.map(f => f.brief)), plan, false, 0);

  // S3 — issue triage (T005 simulates one malformed response -> retry)
  const routes = [], packets = [];
  for (const t of tickets) {
    const acct = accounts.find(a => a.account_id === t.account_id);
    const retries = t.ticket_id === "T005" ? 1 : 0;
    if (retries) events.push({ type: "retry", text: "T005: malformed JSON from model — schema validation failed, retried once, recovered" });
    const route = triageTicket(t, acct);
    ledger.add("issue_triage", M_TRIAGE, JSON.stringify(t) + accountContext(acct, usage, tickets, notes), route, false, retries);
    const [finalRoute, override] = triageGuardrail(t, acct, route);
    route.final_route = finalRoute; route.guardrail_override = override;
    route.account_name = acct.account_name; route.issue_summary = t.issue_summary;
    if (override) events.push({ type: "guardrail", text: `${t.ticket_id}: model chose '${route.route}' — deterministic floor forced ESCALATE (${override})` });
    routes.push(route);
    if (finalRoute === "escalate") {
      const p = escalationPacket(t, acct);
      ledger.add("escalation_packets", M_SYNTH, JSON.stringify(t) + accountContext(acct, usage, tickets, notes), p, false, 0);
      packets.push(p);
    }
  }

  // S4 — check-in prep
  const briefs = checkins.map(c => {
    const acct = accounts.find(a => a.account_id === c.account_id);
    const note = notes.find(n => n.account_id === c.account_id);
    const b = checkinBrief(c, acct, note, tickets.filter(t => t.account_id === c.account_id));
    ledger.add("checkin_prep", M_SYNTH, JSON.stringify(c) + accountContext(acct, usage, tickets, notes), b, false, 0);
    return b;
  });
  const contPass = briefs.filter(b => b.continuity_check !== "FAIL").length;
  events.push({ type: "eval", text: `Check-in continuity eval: ${contPass}/${briefs.length} briefs carry forward all prior follow-up items` });

  // S5 — quality review
  const reviews = junior.map(row => {
    const acct = accounts.find(a => a.account_id === row.account_id);
    const note = notes.find(n => n.account_id === row.account_id);
    const r = qualityReview(row, acct, note);
    ledger.add("quality_review", M_SYNTH, JSON.stringify(row) + JSON.stringify(acct), r, false, 0);
    if (r.disagreement) events.push({ type: "crosscheck", text: `${r.output_id}: judge approved but heuristics raised red flags — queued for human spot review` });
    return r;
  });

  // S6 — intervention
  const members = accounts.filter(a => +a.previous_health_score - +a.current_health_score >= 15);
  let plan6 = null;
  if (members.length >= 2) {
    plan6 = intervention(members);
    ledger.add("interventions", M_DEEP, members.map(m => accountContext(m, usage, tickets, notes)).join("\n"), plan6, false, 0);
    events.push({ type: "segment", text: `Declining segment detected (health drop >=15): ${plan6.members.join(", ")} — intervention designed, checkpoint ${plan6.measurement_plan.checkpoint_date}` });
  }

  // costs + annual projection
  const agg = ledger.byStage();
  let runCost = 0, runTokens = 0;
  for (const s of Object.values(agg)) { runCost += s.usd; runTokens += s.inTok + s.cachedTok + s.outTok; }
  const projection = []; let annual = 0;
  for (const [stage, s] of Object.entries(agg)) {
    if (!ANNUAL_VOLUMES[stage]) continue;
    const [units, desc] = ANNUAL_VOLUMES[stage];
    const perUnit = s.usd / s.calls;
    projection.push({ stage, desc, units, perUnit, annual: perUnit * units });
    annual += perUnit * units;
  }
  for (const [proxy, key] of [["issue_triage", "monitor_alerts"], ["checkin_prep", "checkin_followup"]]) {
    if (!agg[proxy]) continue;
    const [units, desc] = ANNUAL_VOLUMES[key];
    const perUnit = agg[proxy].usd / agg[proxy].calls;
    projection.push({ stage: key, desc: desc + " (priced from " + proxy + ")", units, perUnit, annual: perUnit * units });
    annual += perUnit * units;
  }

  return { screens, flagged, expansion, plan, routes, packets, briefs, reviews,
           intervention: plan6, events, ledger: agg, runCost, runTokens, projection, annualCost: annual };
}

if (typeof module !== "undefined") module.exports = { parseCSV, runEngine, screenAccount, TODAY };
