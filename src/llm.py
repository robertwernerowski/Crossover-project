"""LLM runner with per-stage token/cost accounting.

Two execution modes:
  * real  -- ANTHROPIC_API_KEY present: calls the Claude API (prompt caching
             on the system block, JSON-only responses, one retry on bad JSON).
  * mock  -- no key: a deterministic mock generates schema-valid responses
             from the structured input, so the full pipeline (routing,
             guardrails, evals, escalation paths) runs end-to-end and token
             usage is estimated from the actual prompt/response text.

Every call is recorded in UsageLog with model, stage, token counts, and cost
(list price and effective price after batch/cache adjustments).
"""

import csv
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

from config import BATCH_DISCOUNT, CACHE_READ_MULT, CACHE_WRITE_MULT, PRICING


def estimate_tokens(text: str) -> int:
    """Rough token estimate for mock mode (~4 chars/token for English prose)."""
    return max(1, len(text) // 4)


@dataclass
class CallRecord:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int      # portion of input served from prompt cache
    cache_write: bool       # this call wrote the cache (first call in stage)
    batchable: bool
    retries: int

    def cost_list(self) -> float:
        p_in, p_out = PRICING[self.model]
        total_in = self.input_tokens + self.cached_tokens
        return (total_in * p_in + self.output_tokens * p_out) / 1e6

    def cost_effective(self) -> float:
        """Cost after prompt caching and batch discount."""
        p_in, p_out = PRICING[self.model]
        cache_mult = CACHE_WRITE_MULT if self.cache_write else CACHE_READ_MULT
        cost = (self.input_tokens * p_in
                + self.cached_tokens * p_in * cache_mult
                + self.output_tokens * p_out) / 1e6
        if self.batchable:
            cost *= BATCH_DISCOUNT
        return cost


@dataclass
class UsageLog:
    records: list = field(default_factory=list)

    def add(self, rec: CallRecord):
        self.records.append(rec)

    def by_stage(self) -> dict:
        agg = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "cached_tokens": 0,
                                   "output_tokens": 0, "cost_list": 0.0,
                                   "cost_effective": 0.0, "retries": 0, "models": set()})
        for r in self.records:
            s = agg[r.stage]
            s["calls"] += 1
            s["input_tokens"] += r.input_tokens
            s["cached_tokens"] += r.cached_tokens
            s["output_tokens"] += r.output_tokens
            s["cost_list"] += r.cost_list()
            s["cost_effective"] += r.cost_effective()
            s["retries"] += r.retries
            s["models"].add(r.model)
        return agg

    def write_csv(self, path: str):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["stage", "model", "input_tokens", "cached_tokens",
                        "output_tokens", "batchable", "retries",
                        "cost_list_usd", "cost_effective_usd"])
            for r in self.records:
                w.writerow([r.stage, r.model, r.input_tokens, r.cached_tokens,
                            r.output_tokens, r.batchable, r.retries,
                            f"{r.cost_list():.6f}", f"{r.cost_effective():.6f}"])


def _extract_json(text: str):
    """Parse a JSON object from a model response, tolerating code fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in response")
    return json.loads(text[start:end + 1])


class LLMRunner:
    def __init__(self):
        self.log = UsageLog()
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.mode = "real" if self.api_key else "mock"
        self._client = None
        self._cache_seen = set()  # (stage, hash(system)) pairs already cached

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def call(self, *, stage: str, model: str, system: str, user: str,
             mock_fn, validator=None, batchable: bool = False,
             max_tokens: int = 1500) -> dict:
        """Run one LLM call and return the parsed+validated JSON dict.

        mock_fn(attempt) -> str: deterministic response generator for mock mode.
        validator(obj) -> list[str]: returns problems; non-empty triggers a retry.
        Raises RuntimeError after 2 failed attempts (caller routes to escalation).
        """
        last_err = None
        for attempt in range(2):
            text = (self._call_real(model, system, user, max_tokens)
                    if self.mode == "real" else mock_fn(attempt))
            self._record(stage, model, system, user, text, batchable, attempt)
            try:
                obj = _extract_json(text)
                problems = validator(obj) if validator else []
                if problems:
                    raise ValueError("; ".join(problems))
                return obj
            except (ValueError, json.JSONDecodeError) as e:
                last_err = e
        raise RuntimeError(f"[{stage}] response failed validation after retry: {last_err}")

    def _call_real(self, model, system, user, max_tokens) -> str:
        resp = self._get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        self._last_usage = resp.usage
        return next(b.text for b in resp.content if b.type == "text")

    def _record(self, stage, model, system, user, response_text, batchable, retries):
        if self.mode == "real":
            u = self._last_usage
            rec = CallRecord(stage=stage, model=model,
                             input_tokens=u.input_tokens,
                             cached_tokens=(u.cache_read_input_tokens or 0)
                             + (u.cache_creation_input_tokens or 0),
                             cache_write=bool(u.cache_creation_input_tokens),
                             output_tokens=u.output_tokens,
                             batchable=batchable, retries=retries)
        else:
            key = (stage, hash(system))
            cache_write = key not in self._cache_seen
            self._cache_seen.add(key)
            rec = CallRecord(stage=stage, model=model,
                             input_tokens=estimate_tokens(user),
                             cached_tokens=estimate_tokens(system),
                             cache_write=cache_write,
                             output_tokens=estimate_tokens(response_text),
                             batchable=batchable, retries=retries)
        self.log.add(rec)
