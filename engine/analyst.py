"""Gemini analysis layer (free tier).

Two calls per run, sized for the free-tier daily request quota:
  1. News scan   - free RSS headlines + (if the free tier allows it) Google Search
                   grounding; returns a terse fact list with numbers + sources.
  2. Synthesis   - no tools; reads the quant digest + news facts + yesterday's view and
                   returns JSON constrained to a schema.

Model choice is resilient to Google's model churn: the first available model from a
preference list is used (override with GEMINI_MODEL), and a call that hits a missing
model or an exhausted daily quota moves on to the next model.

The synthesis is instructed to cite only figures present in its inputs; a post-check
flags any figure it cannot trace back to the inputs (see `audit_numbers`).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config

log = logging.getLogger(__name__)

# Preference order: rolling aliases first (survive deprecations), then pinned fallbacks.
MODEL_PREFS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]


# ─── Prompts ─────────────────────────────────────────────────────────────────
NEWS_SYSTEM = """You are the overnight desk analyst for a global macro fund. You search the web and \
report only verifiable facts. You never speculate, never pad, never repeat background."""

NEWS_TASK = """Today is {today} (Dubai). Window: events since {since}.

{search_line}

Headlines collected from news feeds in the window (titles only):
{headlines}

Report what moved or will move macro markets in that window. Priorities:
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
Never invent a number. If unsure of a figure, omit it. Use only facts from search results
or the headlines above; if a headline gives no figure, report it without one."""

SEARCH_ON = "Use Google Search to verify and add figures (actual vs consensus, flows, levels)."
SEARCH_OFF = "You have no search tool: work only from the headlines below."

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


# ─── Gemini calls ────────────────────────────────────────────────────────────
def _schema_without_additional(schema):
    """Older Gemini schema dialect (response_schema) rejects additionalProperties."""
    s = copy.deepcopy(schema)

    def walk(n):
        if isinstance(n, dict):
            n.pop("additionalProperties", None)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(s)
    return s


def _parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


def normalise(a: dict) -> dict:
    """Guarantee every schema key exists so renderers never crash on a sparse reply."""
    a = dict(a or {})
    rg = a.get("regime") or {}
    a["regime"] = {k: str(rg.get(k, "")) for k in ("label", "one_liner", "shift_vs_prior")}
    for k in ("bottom_line", "developments", "trends", "watch", "data_caveats"):
        a[k] = a.get(k) if isinstance(a.get(k), list) else []
    hs = a.get("hidden_signal") or {}
    a["hidden_signal"] = {k: str(hs.get(k, "")) for k in ("title", "evidence", "why_consensus_misses")}
    a["india_gulf"] = str(a.get("india_gulf", ""))
    a["trends"] = [dict(t, horizon=t.get("horizon") if t.get("horizon") in ("short", "medium", "long") else "medium",
                        status=t.get("status") or "ongoing")
                   for t in a["trends"] if isinstance(t, dict) and t.get("title")]
    for d in a["developments"]:
        for k in ("headline", "what_happened", "so_what", "second_order", "assets"):
            d.setdefault(k, "")
    for t in a["trends"]:
        for k in ("evidence", "read"):
            t.setdefault(k, "")
    for w in a["watch"]:
        w.setdefault("trigger", ""); w.setdefault("implication", "")
    if not a["regime"]["label"]:
        raise ValueError("synthesis returned no regime label")
    return a


class Analyst:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai
        from google.genai import errors, types
        self.types, self.errors = types, errors
        self.client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self.usage = {"input_tokens": 0, "output_tokens": 0, "searches": 0, "calls": 0}
        self.grounded = False
        self.models = self._candidates(model or os.getenv("GEMINI_MODEL"))
        self.model = self.models[0]

    def _candidates(self, override: str | None) -> list[str]:
        prefs = ([override] if override else []) + [m for m in MODEL_PREFS if m != override]
        try:
            available = {m.name.split("/")[-1] for m in self.client.models.list()}
            found = [m for m in prefs if m in available]
            if found:
                return found
        except Exception as e:
            log.warning("model listing failed: %s", e)
        return prefs

    def _account(self, resp):
        u = getattr(resp, "usage_metadata", None)
        if u:
            self.usage["input_tokens"] += (u.prompt_token_count or 0) + (getattr(u, "tool_use_prompt_token_count", 0) or 0)
            self.usage["output_tokens"] += (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        try:
            gm = resp.candidates[0].grounding_metadata
            q = getattr(gm, "web_search_queries", None) if gm else None
            if q:
                self.usage["searches"] += len(q)
                self.grounded = True
        except (AttributeError, IndexError, TypeError):
            pass
        self.usage["calls"] += 1

    def _generate(self, contents: str, config_kwargs: dict):
        """Try each candidate model; wait out short rate limits, skip missing/exhausted models."""
        last = None
        for model in list(self.models):
            for attempt in range(3):
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=contents,
                        config=self.types.GenerateContentConfig(**config_kwargs))
                    self._account(resp)
                    if not (resp.text or "").strip():
                        raise RuntimeError(f"empty response from {model} "
                                           f"(finish: {getattr(resp.candidates[0], 'finish_reason', '?') if resp.candidates else 'blocked'})")
                    self.model = model
                    return resp
                except self.errors.APIError as e:
                    last = e
                    msg = str(e).lower()
                    daily = any(k in msg for k in ("per day", "perday", "daily"))
                    if e.code == 429 and not daily and attempt < 2:
                        delay = re.search(r"retry in ([\d.]+)s|retrydelay[^\d]*(\d+)", msg)
                        wait = float(next(g for g in delay.groups() if g)) if delay else 20.0
                        time.sleep(min(wait + 1, 65))
                        continue
                    if e.code in (500, 503) and attempt < 2:
                        time.sleep(10 * (attempt + 1))
                        continue
                    log.warning("model %s failed (%s): %s", model, e.code, str(e)[:200])
                    break  # 404 / quota exhausted / bad request -> next model
                except RuntimeError as e:
                    last = e
                    log.warning("%s", e)
                    break
        raise RuntimeError(f"all Gemini models failed: {last}")

    def news(self, today: str, since: str, signal_titles: list[str], headlines: str = "") -> str:
        sig = "\n".join(f"- {t}" for t in signal_titles) or "- none"
        heads = headlines or "(none collected)"
        base = dict(system_instruction=NEWS_SYSTEM, temperature=0.2, max_output_tokens=4096)
        # 1st choice: Google Search grounding. Not free on every model/tier -> fall back to headlines.
        try:
            resp = self._generate(
                NEWS_TASK.format(today=today, since=since, signals=sig, headlines=heads, search_line=SEARCH_ON),
                dict(base, tools=[self.types.Tool(google_search=self.types.GoogleSearch())]))
            return resp.text.strip()
        except Exception as e:
            log.warning("grounded news scan unavailable, using headlines only: %s", str(e)[:200])
        resp = self._generate(
            NEWS_TASK.format(today=today, since=since, signals=sig, headlines=heads, search_line=SEARCH_OFF), base)
        return resp.text.strip()

    def synthesise(self, mode: str, today: str, asof: str, digest: str, news: str,
                   prior: str, prior_date: str) -> dict:
        user = SYNTH_TASK.format(mode=mode, today=today, asof=asof, mode_hint=MODE_HINTS[mode],
                                 prior=prior, prior_date=prior_date, digest=digest, news=news)
        base = dict(system_instruction=SYNTH_SYSTEM, temperature=0.3, max_output_tokens=16384,
                    response_mime_type="application/json")
        try:
            resp = self._generate(user, dict(base, response_json_schema=SCHEMA))
        except RuntimeError as e:
            if "schema" not in str(e).lower():
                raise
            resp = self._generate(user, dict(base, response_schema=_schema_without_additional(SCHEMA)))
        return normalise(_parse_json(resp.text))

    def cost_usd(self) -> float:
        return 0.0  # free tier


def dubai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Dubai"))
