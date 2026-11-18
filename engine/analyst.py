"""Claude analysis layer.

Two calls per run, sized for token economy:
  1. News scan   - web search (capped), returns a terse fact list with numbers + sources.
  2. Synthesis   - no tools; reads the quant digest + news facts + yesterday's view and
                   returns schema-validated JSON (structured outputs).

The synthesis is instructed to cite only figures present in its inputs; a post-check
flags any figure it cannot trace back to the inputs (see `audit_numbers`).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from . import config

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Opus 5.5 list price, USD per 1M tokens (input, output); web search per 1k searches.
PRICE_IN, PRICE_OUT, PRICE_SEARCH = 4.0, 20.0, 10.0


# ─── Prompts ─────────────────────────────────────────────────────────────────
NEWS_SYSTEM = """You are the overnight desk analyst for a global macro fund. You search the web and \
report only verifiable facts. You never speculate, never pad, never repeat background."""

NEWS_TASK = """Today is {today} (Dubai). Window: events since {since}.

Search for what moved or will move macro markets in that window. Priorities:
1. Central banks: Fed, ECB, BOJ, PBOC, RBI, BoE, SNB - decisions, minutes, notable speeches, balance-sheet/FX operations.
2. Data prints with ACTUAL vs CONSENSUS vs PRIOR (US, China, Euro area, Japan, India).
3. Energy & metals: OPEC+, inventories, central-bank gold buying, ETF flows, supply shocks.
4. Geopolitics/trade: tariffs, sanctions, conflicts - only items with market transmission.
5. Credit/funding: spread events, auction tails, repo/funding stress, rating actions, fiscal news.
6. FX: interventions, CNH fixings, INR (RBI activity, FX reserves), JPY.
7. India flows: FPI/FII and DII net flows (NSDL/NSE provisional), notable India macro.

The quant engine flagged these moves - find the cause if one exists:
{signals}

Output format (plain text, no preamble, max ~600 words):
- One line per fact: `[date] [region/asset] fact with exact numbers (source)`.
- Up to 14 lines, most market-relevant first. Skip any category with nothing material.
- A final line `CAUSES:` mapping flagged moves to a cause, or `unexplained` if none found.
Never invent a number. If unsure of a figure, omit it."""

SYNTH_SYSTEM = """You are the chief macro strategist writing the morning note for a senior \
finance professional (Dubai-based, India/Gulf exposure). The reader wants understanding, not \
trades: never recommend positions, entries, targets or stops.

Standards:
- Signal over noise. Lead with what CHANGED and why it matters; skip what is merely ongoing \
unless its significance changed.
- Every claim must be anchored to a figure or fact from the inputs (quant digest, news facts, \
prior view). Do not introduce numbers that are not in the inputs. If you need a figure that \
is missing, say it is unavailable.
- Think in layers: 1st order (what happened) → 2nd order (transmission to other assets, \
currencies, flows, India/Gulf) → 3rd order (regime implication, reflexive loops, what \
consensus is under-pricing).
- Treat broken historical relationships, positioning extremes, changes in how corrections \
resolve, liquidity and term-premium shifts as the highest-value evidence. Explain what each \
implies about who is buying/selling and why.
- Separate horizons: short (1-4 weeks), medium (1-6 months), long (6+ months).
- Be crisp: short sentences, no adjectives without numbers, no generic macro boilerplate.
- Where the prior view is provided, state explicitly what has changed versus it."""

SYNTH_TASK = """MODE: {mode}. Note date: {today} (Dubai). Market data as of {asof} (US close).
{mode_hint}

=== PRIOR VIEW ({prior_date}) ===
{prior}

=== QUANT DIGEST (computed from Yahoo Finance, FRED, CFTC) ===
{digest}

=== NEWS FACTS (web search, last window) ===
{news}

Write the note as JSON matching the schema. Limits: bottom_line 3-5 items; developments \
max 5; trends 4-8 (cover all three horizons where evidence exists); watch 3-5 conditional \
triggers expressed as observable levels/conditions from the digest (no calendar guesses)."""

MODE_HINTS = {
    "daily": "Daily note: emphasise what changed in the last session/day and new signals; keep ongoing themes brief.",
    "weekly": ("Weekly review: emphasise medium and long horizon trends, week-on-week regime "
               "changes, positioning (latest CFTC), and relationships that have broken or healed."),
}

_STR = {"type": "string"}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["regime", "bottom_line", "developments", "trends", "hidden_signal",
                 "india_gulf", "watch", "data_caveats"],
    "properties": {
        "regime": {
            "type": "object", "additionalProperties": False,
            "required": ["label", "one_liner", "shift_vs_prior"],
            "properties": {"label": _STR, "one_liner": _STR, "shift_vs_prior": _STR},
        },
        "bottom_line": {"type": "array", "items": _STR},
        "developments": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["headline", "what_happened", "so_what", "second_order", "assets"],
                "properties": {"headline": _STR, "what_happened": _STR, "so_what": _STR,
                               "second_order": _STR, "assets": _STR},
            },
        },
        "trends": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["horizon", "status", "title", "evidence", "read"],
                "properties": {
                    "horizon": {"type": "string", "enum": ["short", "medium", "long"]},
                    "status": {"type": "string",
                               "enum": ["emerging", "strengthening", "fading", "broken", "ongoing"]},
                    "title": _STR, "evidence": _STR, "read": _STR,
                },
            },
        },
        "hidden_signal": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "evidence", "why_consensus_misses"],
            "properties": {"title": _STR, "evidence": _STR, "why_consensus_misses": _STR},
        },
        "india_gulf": _STR,
        "watch": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["trigger", "implication"],
                "properties": {"trigger": _STR, "implication": _STR},
            },
        },
        "data_caveats": {"type": "array", "items": _STR},
    },
}


# ─── Digest builder ──────────────────────────────────────────────────────────
def _pct(v, rate=False, level=False):
    if v is None:
        return "n/a"
    if rate:
        return f"{v * 100:+.0f}bp"
    if level:
        return f"{v:+,.2f}"
    return f"{v * 100:+.1f}%"


def build_digest(q: dict, gaps: list[str], max_signals: int = config.MAX_SIGNALS_TO_LLM) -> str:
    L: list[str] = []
    L.append("SNAPSHOT: name | last | 1w | 1m | 3m | 12m | 5y pctile")
    for r in q["snapshot"]:
        lv = r["is_level"] and not r["is_rate"]
        L.append(" | ".join([r["name"], r["last_fmt"]] +
                            [_pct(r[k], r["is_rate"], lv) for k in ("w1", "m1", "m3", "y1")] +
                            [str(r["pct5y"])]))
    g = q["regime"]
    L.append("\nREGIME")
    if "risk_appetite" in g:
        ra = g["risk_appetite"]
        L.append(f"Risk appetite composite {ra['value']}/100 (1w ago {ra['w1_ago']}, 1m ago {ra['m1_ago']})")
    if "quadrant" in g:
        qd = g["quadrant"]
        L.append(f"Market-implied quadrant: {qd['now']} (1m ago {qd['m1_ago']}); growth z {qd['growth_z']}, inflation z {qd['inflation_z']}")
    if "curve" in g:
        c = g["curve"]
        L.append(f"UST curve 1m: {c['shape_1m']} (2Y {c['d2y_bp']:+}bp, 10Y {c['d10y_bp']:+}bp); 2s10s {c['s2s10_bp']}bp, 3m10y {c['s3m10_bp']}bp")
    if "rates_decomp_3m" in g:
        d = g["rates_decomp_3m"]
        L.append(f"10Y move 3m decomposition: real {d['real_bp']:+}bp, breakeven {d['breakeven_bp']:+}bp (driver: {d['driver']})")
    if "term_premium" in g:
        t = g["term_premium"]
        L.append(f"10Y term premium {t['value_bp']}bp (3m {t['chg_3m_bp']:+}bp, 5y pctile {t['pct5y']})")
    if "credit" in g:
        c = g["credit"]
        L.append(f"HY OAS {c['hy_oas_bp']}bp (1m {c['hy_1m_bp']:+}bp, 5y pctile {c['hy_pct5y']})")
    if "liquidity" in g:
        lq = g["liquidity"]
        L.append(f"Fed net liquidity ${lq['netliq_bn']:,}bn (4w {lq['chg_4w_bn']:+,}bn, 13w {lq['chg_13w_bn']:+,}bn)")
    if "vix_term" in g:
        L.append(f"VIX3M/VIX {g['vix_term']} (<1 = inverted/stress)")
    ind = q.get("india", {})
    if ind:
        L.append("\nINDIA/GULF")
        if "inr_vol_ratio" in ind:
            v = ind["inr_vol_ratio"]
            L.append(f"USD/INR 1m vol {v['inr_vol_1m']}% vs DXY {v['dxy_vol_1m']}% (ratio {v['value']}, 3y pctile {v['pct3y']}; low = RBI smoothing)")
        if "nifty_usd_vs_em" in ind:
            v = ind["nifty_usd_vs_em"]
            L.append(f"Nifty in USD vs EEM: 3m {v['rel_3m']:+}%, 12m {v['rel_12m']:+}%")
    L.append("\nSIGNALS (severity 0-10 | horizon | NEW or ONGOING n days)")
    for s in q["signals"][:max_signals]:
        tag = "NEW" if s["status"] == "new" else f"ONGOING {s['days_active']}d"
        L.append(f"[{s['severity']} | {s['horizon']} | {tag}] {s['title']} — {s['detail']}")
    L.append("\nRELATIONSHIPS (26w corr | 13w | 5y median | state)")
    for r in q["relationships"]:
        L.append(f"{r['name']}: {r['corr_26w']:+} | {r['corr_13w']:+} | {r['baseline_median']:+} | {r['state']}")
    if q["positioning"]:
        L.append("\nPOSITIONING (CFTC, net % of OI | 3y pctile now / 4w ago)")
        for r in q["positioning"]:
            L.append(f"{r['name']} ({r['who']}): {r['net_oi_pct']:+}% | {r['pct3y']} / {r['pct3y_4w_ago']} (as of {r['asof']})")
    open_eps = [b for b in q["behaviour"] if b.get("open_days")]
    if open_eps:
        L.append("\nOPEN CORRECTIONS (depth in σ vs 5y avg | recovery base rate)")
        for b in open_eps:
            L.append(f"{b['name']} {b['type']}: open {b['open_days']}d, {b['open_depth']}σ vs avg {b['base_depth']}σ | base {b['base_recovery']}% recover <40d")
    if gaps:
        L.append(f"\nDATA GAPS: {', '.join(gaps)}")
    return "\n".join(L)


def prior_summary(prev: dict | None) -> tuple[str, str]:
    if not prev or "analysis" not in prev or not prev["analysis"]:
        return "none", "n/a"
    a = prev["analysis"]
    lines = [f"Regime: {a['regime']['label']} - {a['regime']['one_liner']}"]
    lines += [f"- {b}" for b in a.get("bottom_line", [])]
    lines += [f"Trend [{t['horizon']}/{t['status']}]: {t['title']}" for t in a.get("trends", [])]
    return "\n".join(lines), prev.get("note_date", "n/a")


# ─── Number audit ────────────────────────────────────────────────────────────
_NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*\.?\d*")


def _nums(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def audit_numbers(analysis: dict, corpus: str) -> list[str]:
    """Return figures in the analysis that cannot be traced to the inputs."""
    pool = {abs(x) for x in _nums(corpus)}
    text = json.dumps(analysis, ensure_ascii=False)
    bad = []
    for x in {abs(v) for v in _nums(text)}:
        if x < 10 and x == int(x):            # small counts ("3 months", "2 of 4")
            continue
        if 1900 <= x <= 2100 and x == int(x):  # years
            continue
        if any(abs(x - y) <= max(0.011, 0.005 * y) for y in pool):
            continue
        bad.append(f"{x:g}")
    return sorted(bad)


# ─── Claude calls ────────────────────────────────────────────────────────────
class Analyst:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model or os.getenv("CLAUDE_MODEL", config.DEFAULT_MODEL)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "searches": 0}

    def _account(self, resp):
        u = resp.usage
        self.usage["input_tokens"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) \
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        self.usage["output_tokens"] += u.output_tokens or 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            self.usage["searches"] += getattr(stu, "web_search_requests", 0) or 0

    def _create(self, **kw):
        resp = self.client.beta.messages.create(
            model=self.model, betas=[FALLBACK_BETA], fallbacks="default", **kw)
        self._account(resp)
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"Model refused: {getattr(resp, 'stop_details', None)}")
        return resp

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    def news(self, today: str, since: str, signal_titles: list[str]) -> str:
        user = NEWS_TASK.format(today=today, since=since,
                                signals="\n".join(f"- {t}" for t in signal_titles) or "- none")
        messages = [{"role": "user", "content": user}]
        tools = [{"type": "web_search_20260209", "name": "web_search",
                  "max_uses": config.NEWS_MAX_SEARCHES}]
        resp = self._create(system=NEWS_SYSTEM, max_tokens=8000, messages=messages, tools=tools,
                            output_config={"effort": "medium"})
        for _ in range(3):  # server-side tool loop can pause on long searches
            if resp.stop_reason != "pause_turn":
                break
            messages = [{"role": "user", "content": user}, {"role": "assistant", "content": resp.content}]
            resp = self._create(system=NEWS_SYSTEM, max_tokens=8000, messages=messages, tools=tools,
                                output_config={"effort": "medium"})
        # Keep only text written after the last search (earlier text is search narration).
        parts: list[str] = []
        for b in resp.content:
            t = getattr(b, "type", "")
            if t in ("server_tool_use", "web_search_tool_result"):
                parts = []
            elif t == "text":
                parts.append(b.text)
        return "".join(parts).strip() or self._text(resp)

    def synthesise(self, mode: str, today: str, asof: str, digest: str, news: str,
                   prior: str, prior_date: str) -> dict:
        user = SYNTH_TASK.format(mode=mode, today=today, asof=asof, mode_hint=MODE_HINTS[mode],
                                 prior=prior, prior_date=prior_date, digest=digest, news=news)
        resp = self._create(
            system=SYNTH_SYSTEM, max_tokens=16000,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": os.getenv("CLAUDE_EFFORT", "high"),
                           "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Synthesis truncated at max_tokens")
        return json.loads(self._text(resp))

    def cost_usd(self) -> float:
        u = self.usage
        return round(u["input_tokens"] / 1e6 * PRICE_IN + u["output_tokens"] / 1e6 * PRICE_OUT
                     + u["searches"] / 1e3 * PRICE_SEARCH, 3)


def dubai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Dubai"))
