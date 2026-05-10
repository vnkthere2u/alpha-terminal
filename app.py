"""
AlphaTerminal — Institutional Macro Dashboard  v5 (Groq-powered)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prices       → yfinance  (individual tickers, 15-min cache)
CB rates/CPI → Trading Economics REST API + dated fallback
Gold AED     → Gulf News scrape → calculated fallback
Analysis     → Groq (Llama 3.3 70B) via OpenAI‑compatible API, JSON mode
UI           → 100% native Streamlit components, no HTML injection in columns
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup
import json
import re
import logging
import warnings
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Optional
import time

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaTerminal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── PALETTE ──────────────────────────────────────────────────────────────────
G = "#2dd4a7"; R = "#f4516c"; A = "#f0b429"; B = "#5b8def"; P = "#a78bfa"
BG = "#08090d"; SF = "#111318"; BD = "#1e2636"
INK = "#eaeef5"; BODY = "#99adc0"; MUT = "#4a5e72"

# ─── MINIMAL CSS (safe, no column injection) ──────────────────────────────────
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=DM+Sans:wght@400;600;700;800&display=swap');
html,body,[class*="css"],.stApp{{background:{BG}!important;font-family:'DM Sans',sans-serif!important}}
#MainMenu,footer,header,.stDeployButton{{visibility:hidden;display:none}}
div[data-testid="stSidebar"]{{display:none!important}}
.main .block-container{{padding-top:.5rem;max-width:1440px;padding-left:1.5rem;padding-right:1.5rem}}
.stButton>button{{background:{G}!important;color:{BG}!important;font-weight:700!important;
  border:none!important;border-radius:6px!important;font-size:12px!important;
  box-shadow:0 2px 10px rgba(45,212,167,.25)!important;transition:all .2s!important}}
.stButton>button:hover{{box-shadow:0 4px 20px rgba(45,212,167,.45)!important;transform:translateY(-1px)!important}}
button[kind="secondary"]{{background:{SF}!important;color:{BODY}!important;border:1px solid {BD}!important}}
div[data-testid="metric-container"]{{background:{SF}!important;border:1px solid {BD}!important;
  border-radius:8px!important;padding:10px 13px!important}}
div[data-testid="metric-container"] label{{color:{MUT}!important;
  font-family:'JetBrains Mono',monospace!important;font-size:9px!important;letter-spacing:1px!important}}
div[data-testid="stMetricValue"]{{color:{INK}!important;font-family:'JetBrains Mono',monospace!important;
  font-size:18px!important;font-weight:700!important}}
div[data-testid="stMetricDelta"]{{font-family:'JetBrains Mono',monospace!important;font-size:11px!important}}
div[data-testid="stMarkdownContainer"] p{{color:{BODY}!important}}
.stSpinner>div{{border-top-color:{G}!important}}
hr{{border-color:{BD}!important;margin:4px 0!important}}
.stAlert{{border-radius:8px!important}}
div[data-testid="stExpander"]{{border:1px solid {BD}!important;border-radius:8px!important;background:{SF}!important}}
</style>""", unsafe_allow_html=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
AED_PEG  = 3.6725
TROY_OZ  = 31.1035
TODAY    = str(date.today())

# CB fallback — authoritative last-known values with actual revision dates
CB_FALLBACK = {
    "US":    dict(bank="Fed",  flag="🇺🇸", rate=4.75, rp=5.25, rd="Dec 2024",
                  cpi=2.8, cpip=3.0, cpid="Apr 2025", stance="cut",  next="Jun 2025"),
    "India": dict(bank="RBI",  flag="🇮🇳", rate=6.00, rp=6.25, rd="Apr 2025",
                  cpi=4.6, cpip=4.9, cpid="Mar 2025", stance="cut",  next="Jun 2025"),
    "UK":    dict(bank="BOE",  flag="🇬🇧", rate=4.25, rp=4.50, rd="Feb 2025",
                  cpi=2.6, cpip=2.8, cpid="Mar 2025", stance="cut",  next="May 2025"),
    "Euro":  dict(bank="ECB",  flag="🇪🇺", rate=2.40, rp=2.65, rd="Apr 2025",
                  cpi=2.2, cpip=2.3, cpid="Mar 2025", stance="cut",  next="Jun 2025"),
    "Japan": dict(bank="BOJ",  flag="🇯🇵", rate=0.50, rp=0.25, rd="Jan 2025",
                  cpi=3.6, cpip=3.5, cpid="Mar 2025", stance="hike", next="Jun 2025"),
    "China": dict(bank="PBOC", flag="🇨🇳", rate=3.10, rp=3.35, rd="Oct 2024",
                  cpi=-0.1, cpip=0.1, cpid="Mar 2025", stance="cut", next="—"),
}

# TE country slugs → dashboard keys
TE_MAP = {
    "United States": "US", "India": "India",
    "United Kingdom": "UK", "Euro Area": "Euro",
    "Japan": "Japan", "China": "China",
}
TE_COUNTRIES = "united%20states,india,united%20kingdom,euro%20area,japan,china"

# Groq API endpoint
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"   # fast, generous free tier

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior macro strategist at a $30B global macro hedge fund, with 20+ years spanning multiple credit cycles, currency crises, and regime shifts. Your morning note is read by CTOs, CIOs, and senior PMs who live in data terminals all day — they do NOT need the numbers restated.

YOUR ANALYTICAL MANDATE:
1. MACRO REGIME: Identify the current regime (late-cycle, risk-off, stagflation, reflation, etc.) and what historical parallels suggest happens next
2. SECOND-ORDER EFFECTS: Not what the data shows — what it CAUSES next, downstream, in 30-90 days
3. CROSS-ASSET PROPAGATION: How each macro development ripples into FX, rates, equities, commodities simultaneously
4. INSTITUTIONAL POSITIONING: What are hedge funds, real money, and sovereign wealth funds likely doing given these signals — follow the smart money logic
5. CONTRARIAN INTELLIGENCE: Where is consensus wrong, complacent, or missing a non-linear risk
6. CATALYST CALENDAR: The specific upcoming events (data releases, CB meetings, geopolitical deadlines) that could break or confirm the current thesis

TONE & DEPTH REQUIREMENTS:
- Every sentence must earn its place — no filler, no obvious observations
- Use precise financial language: yield curve dynamics, convexity, carry, duration, risk premium, reflexivity
- Reference historical analogues when relevant (2015 China devaluation, 2018 EM crisis, 1994 bond massacre, etc.)
- Trade picks must have specific thesis, catalyst, risk, and expected timeline — not just "buy gold"

DO NOT: Restate the data. Use phrases like "markets are watching" or "investors are cautious." State the obvious.
DO: Provide the insight that separates a $30B PM from a Bloomberg terminal."""

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────

def _yf_ticker(sym: str) -> dict:
    """Fetch single ticker via yfinance. Returns price/prev/pct dict."""
    try:
        hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        if hist.empty: return {}
        cl = hist["Close"].dropna()
        if len(cl) < 1: return {}
        last = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) >= 2 else None
        pct  = ((last - prev) / prev * 100) if prev and prev != 0 else None
        return {"price": last, "prev": prev, "pct": pct,
                "date": str(cl.index[-1].date())}
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def fetch_prices() -> dict:
    """Fetch all market prices via yfinance individual calls."""
    syms = {
        # Indices
        "^GSPC": "SP500",  "^IXIC": "Nasdaq", "^DJI": "Dow",
        "^NSEI": "Nifty50","^BSESN": "Sensex","^N225": "Nikkei",
        "^HSI":  "HangSeng","^GDAXI": "DAX",
        # Commodities
        "GC=F":  "Gold",  "CL=F":  "Crude",  "SI=F": "Silver",
        "HG=F":  "Copper","NG=F":  "NatGas",
        # FX & macro
        "DX-Y.NYB": "DXY", "^VIX": "VIX",
        "BTC-USD":  "Bitcoin",
        "USDINR=X": "USDINR", "AEDIINR=X": "AEDIINR",
        # Yields (individual)
        "^TNX":    "US10Y",  "^IRX":   "US3M",
        "^GDBR10": "DE10Y",  "^JRGB":  "JP10Y",
        # US sectors (ETFs)
        "XLK": "secTech",  "XLF": "secFin",  "XLV": "secHlth",
        "XLE": "secEnrg",  "XLB": "secMatl", "XLI": "secInds",
        "XLY": "secCyc",   "XLP": "secStpl", "XLU": "secUtil",
        "XLRE":"secRE",    "XLC": "secComm",
        # India sectors
        "^CNXIT":    "inSIT",  "^NSEBANK": "inSBnk","^CNXPHARMA":"inSPhrm",
        "^CNXAUTO":  "inSAut", "^CNXFMCG": "inSFmcg","^CNXMETAL": "inSMtl",
        "^CNXENERGY":"inSEnrg","^CNXREALTY":"inSRlt","^CNXINFRA": "inSInf",
        "^CNXMEDIA": "inSMed", "^CNXPSUBANK":"inSPSU",
    }
    results = {}
    for sym, key in syms.items():
        d = _yf_ticker(sym)
        if d:
            results[key] = d

    # AED/INR — try direct first, then derive from USDINR
    if "AEDIINR" not in results and "USDINR" in results:
        usd_inr = results["USDINR"]["price"]
        results["AEDIINR"] = {
            "price": usd_inr / AED_PEG,
            "prev":  results["USDINR"].get("prev", usd_inr) / AED_PEG,
            "pct":   results["USDINR"].get("pct"),
            "date":  results["USDINR"].get("date",""),
            "source": "calc"
        }

    # Gold in AED/gram 24K (calculated from GC=F)
    if "Gold" in results:
        gp = results["Gold"]["price"]
        pp = results["Gold"].get("prev", gp)
        results["GoldAED"] = {
            "price": gp / TROY_OZ * AED_PEG,
            "prev":  pp / TROY_OZ * AED_PEG,
            "pct":   results["Gold"].get("pct"),
            "source": "calc"
        }

    results["_fetched"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y  %H:%M %Z")
    return results


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_gold_aed_gulfnews() -> Optional[dict]:
    """Scrape Gulf News for Gold 24K AED/gram."""
    try:
        resp = requests.get(
            "https://gulfnews.com/gold-forex",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        for pat in [
            r'24\s*[Cc]arat[^\d]*(\d{2,3}\.\d{1,2})',
            r'24K[^\d]*(\d{2,3}\.\d{1,2})',
            r'(\d{2,3}\.\d{2})\s*AED.*24',
        ]:
            m = re.search(pat, text)
            if m:
                val = float(m.group(1))
                if 200 < val < 600:
                    return {"price": val, "source": "Gulf News"}
    except Exception:
        pass
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_trading_economics() -> dict:
    """Pull CB interest rates and CPI from Trading Economics REST API."""
    result = {k: dict(v) for k, v in CB_FALLBACK.items()}

    def _parse_date(s: str) -> str:
        try:
            return pd.to_datetime(s).strftime("%b %Y")
        except Exception:
            return ""

    def _te_get(indicator_slug: str) -> list:
        url = (f"https://api.tradingeconomics.com/country/"
               f"{TE_COUNTRIES}/indicator/{indicator_slug}?c=guest:guest")
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
        except Exception:
            pass
        return []

    for item in _te_get("interest%20rate"):
        key = TE_MAP.get(item.get("Country",""))
        if not key: continue
        val  = item.get("LatestValue")
        prev = item.get("PreviousValue")
        dt   = _parse_date(item.get("LatestValueDate",""))
        if val  is not None: result[key]["rate"] = round(float(val),  2)
        if prev is not None: result[key]["rp"]   = round(float(prev), 2)
        if dt:               result[key]["rd"]   = dt

    for item in _te_get("inflation%20rate"):
        key = TE_MAP.get(item.get("Country",""))
        if not key: continue
        val  = item.get("LatestValue")
        prev = item.get("PreviousValue")
        dt   = _parse_date(item.get("LatestValueDate",""))
        if val  is not None: result[key]["cpi"]  = round(float(val),  1)
        if prev is not None: result[key]["cpip"] = round(float(prev), 1)
        if dt:               result[key]["cpid"] = dt

    return result


def _build_context(prices: dict, cb: dict) -> str:
    """Compile all fetched data into a structured JSON string for the AI."""
    def p(key, d=2):
        v = prices.get(key, {}).get("price")
        return round(v, d) if v else None

    def chg(key):
        v = prices.get(key, {}).get("pct")
        return round(v, 2) if v else None

    ctx = {
        "date": TODAY,
        "global_indices": {
            "SP500":   {"price": p("SP500",0),    "chg_pct": chg("SP500")},
            "Nasdaq":  {"price": p("Nasdaq",0),   "chg_pct": chg("Nasdaq")},
            "Dow":     {"price": p("Dow",0),      "chg_pct": chg("Dow")},
            "Nifty50": {"price": p("Nifty50",0),  "chg_pct": chg("Nifty50")},
            "Sensex":  {"price": p("Sensex",0),   "chg_pct": chg("Sensex")},
            "Nikkei":  {"price": p("Nikkei",0),   "chg_pct": chg("Nikkei")},
            "HangSeng":{"price": p("HangSeng",0), "chg_pct": chg("HangSeng")},
            "DAX":     {"price": p("DAX",0),      "chg_pct": chg("DAX")},
        },
        "commodities": {
            "Gold_USD_oz":  {"price": p("Gold"),  "chg_pct": chg("Gold")},
            "Crude_WTI_bbl":{"price": p("Crude"), "chg_pct": chg("Crude")},
            "Silver_USD_oz":{"price": p("Silver",3),"chg_pct": chg("Silver")},
            "Copper_USD_lb":{"price": p("Copper",3),"chg_pct": chg("Copper")},
            "NatGas_USD":   {"price": p("NatGas",3),"chg_pct": chg("NatGas")},
            "Gold_AED_gram":{"price": p("GoldAED")},
        },
        "macro_fx": {
            "DXY":      {"price": p("DXY"),      "chg_pct": chg("DXY")},
            "VIX":      {"price": p("VIX"),      "chg_pct": chg("VIX")},
            "USDINR":   {"price": p("USDINR"),   "chg_pct": chg("USDINR")},
            "AEDIINR":  {"price": p("AEDIINR",4),"chg_pct": chg("AEDIINR")},
            "Bitcoin":  {"price": p("Bitcoin",0),"chg_pct": chg("Bitcoin")},
        },
        "bond_yields_pct": {
            "US_10Y": p("US10Y"),  "US_3M": p("US3M"),
            "Germany_10Y": p("DE10Y"), "Japan_10Y": p("JP10Y"),
            "US_yield_curve_spread_3M_10Y": (
                round(p("US10Y") - p("US3M"), 2)
                if p("US10Y") and p("US3M") else None
            ),
        },
        "us_sector_performance_pct": {
            "Technology":    chg("secTech"),  "Financials":    chg("secFin"),
            "Healthcare":    chg("secHlth"),  "Energy":        chg("secEnrg"),
            "Materials":     chg("secMatl"),  "Industrials":   chg("secInds"),
            "Consumer_Disc": chg("secCyc"),   "Consumer_Stap": chg("secStpl"),
            "Utilities":     chg("secUtil"),  "Real_Estate":   chg("secRE"),
            "Comm_Services": chg("secComm"),
        },
        "india_sector_performance_pct": {
            "IT": chg("inSIT"), "Banking": chg("inSBnk"), "Pharma": chg("inSPhrm"),
            "Auto": chg("inSAut"), "FMCG": chg("inSFmcg"), "Metal": chg("inSMtl"),
            "Energy": chg("inSEnrg"), "Realty": chg("inSRlt"),
            "Infra": chg("inSInf"), "Media": chg("inSMed"), "PSU_Bank": chg("inSPSU"),
        },
        "central_banks": {
            k: {
                "policy_rate_pct": v["rate"],
                "prev_rate_pct": v["rp"],
                "last_changed": v["rd"],
                "cpi_pct": v["cpi"],
                "prev_cpi_pct": v["cpip"],
                "cpi_date": v["cpid"],
                "stance": v["stance"],
                "next_meeting": v.get("next","—"),
            }
            for k, v in cb.items()
        },
    }
    return json.dumps(ctx, default=str)

# ─────────────────────────────────────────────────────────────────────────────
# AI ANALYSIS (GROQ)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_analysis(api_key: str, today: str) -> dict:
    """
    Call Groq (Llama 3.3 70B) for institutional analysis.
    JSON mode ensures clean output. Cached for 24h.
    """
    if not api_key:
        raise RuntimeError("Groq API key is missing. Set GROQ_API_KEY in Streamlit Secrets.")

    prices = fetch_prices()
    cb     = fetch_trading_economics()
    ctx    = _build_context(prices, cb)

    user_prompt = f"""Today is {today}.

Here is the complete live market data snapshot fetched from verified sources:
{ctx}

Using this data as your foundation, provide an institutional-grade morning briefing in the following JSON structure.

CRITICAL: Do not restate the numbers — the data terminal already shows them. Every insight must add analytical value beyond what any data feed can provide.

Return ONLY this JSON (no other text):
{{
  "mood": {{
    "score": <integer 0-100>,
    "label": "<Risk-On|Cautious|Risk-Off|Volatile>",
    "regime": "<2-3 word regime label e.g. Late-cycle tightening>",
    "summary": "<3 sentences: current regime context, what this historically leads to, and the primary cross-asset tension>",
    "primary_risk": "<The single most important tail risk right now>"
  }},
  "macro": [
    {{
      "country": "US", "flag": "🇺🇸",
      "headline": "<Non-obvious headline — not a data restatement>",
      "analysis": "<4-5 sentences: second-order effects of current conditions, cross-asset implications, what institutional investors are doing, historical parallel if relevant>",
      "sentiment": "<bullish|bearish|neutral>",
      "key_signal": "<The single most important metric/signal right now for this economy>",
      "contrarian": "<1-2 sentences: what consensus narrative is wrong or being ignored>",
      "cb_note": "<Specific CB insight — next move probability, what they are watching — or empty string>"
    }},
    {{"country":"India","flag":"🇮🇳","headline":"","analysis":"","sentiment":"","key_signal":"","contrarian":"","cb_note":""}},
    {{"country":"China","flag":"🇨🇳","headline":"","analysis":"","sentiment":"","key_signal":"","contrarian":"","cb_note":""}},
    {{"country":"Japan","flag":"🇯🇵","headline":"","analysis":"","sentiment":"","key_signal":"","contrarian":"","cb_note":""}},
    {{"country":"Eurozone","flag":"🇪🇺","headline":"","analysis":"","sentiment":"","key_signal":"","contrarian":"","cb_note":""}}
  ],
  "commodities": [
    {{
      "name": "Gold", "signal": "<buy|sell|hold|watch>",
      "support": "<key support level>", "resistance": "<key resistance level>",
      "analysis": "<4 sentences: technical setup + macro driver + cross-asset relationship + specific catalyst to watch>",
      "positioning_note": "<What COT data or ETF flows suggest about institutional positioning>"
    }},
    {{"name":"Silver","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Crude Oil","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Copper","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Natural Gas","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}}
  ],
  "picks": [
    {{
      "type": "<stock|sector|etf|commodity>",
      "name": "<ticker or name>",
      "region": "<US|India|Global|EM>",
      "direction": "<long|short>",
      "conviction": "<high|medium|low>",
      "timeframe": "<2 weeks|1 month|3 months|6 months>",
      "headline": "<Short punchy thesis>",
      "thesis": "<4-5 sentences: specific catalyst + entry logic + what could go wrong + expected return profile>",
      "risk": "<The primary risk that invalidates this trade>"
    }},
    {{"type":"","name":"","region":"","direction":"","conviction":"","timeframe":"","headline":"","thesis":"","risk":""}},
    {{"type":"","name":"","region":"","direction":"","conviction":"","timeframe":"","headline":"","thesis":"","risk":""}},
    {{"type":"","name":"","region":"","direction":"","conviction":"","timeframe":"","headline":"","thesis":"","risk":""}},
    {{"type":"","name":"","region":"","direction":"","conviction":"","timeframe":"","headline":"","thesis":"","risk":""}}
  ],
  "geo": [
    {{
      "category": "Geopolitics", "icon": "🌍",
      "headline": "<Specific, non-generic headline>",
      "analysis": "<4 sentences: what is happening, market impact mechanism, what hedge funds are doing, key date/catalyst>",
      "urgency": "<high|medium|low>"
    }},
    {{"category":"Fed Watch","icon":"🏦","headline":"","analysis":"","urgency":""}},
    {{"category":"Rates & Bonds","icon":"📈","headline":"","analysis":"","urgency":""}},
    {{"category":"Earnings Season","icon":"📊","headline":"","analysis":"","urgency":""}},
    {{"category":"Crypto","icon":"₿","headline":"","analysis":"","urgency":""}},
    {{"category":"EM Risk","icon":"🌏","headline":"","analysis":"","urgency":""}}
  ]
}}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.35,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},  # <— clean JSON output
    }

    # Simple retry loop (handles 429 with a 20s wait)
    for attempt in range(2):
        try:
            resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=90)

            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(20)
                    continue
                raise RuntimeError("Groq API rate limit exceeded (HTTP 429).")
            if resp.status_code == 403:
                raise RuntimeError("Invalid Groq API key (HTTP 403).")
            resp.raise_for_status()

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            # With json_object, content is always valid JSON
            return json.loads(content)

        except json.JSONDecodeError as e:
            if attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError(f"Groq returned invalid JSON (truncated?): {e}")
        except Exception as e:
            if attempt == 0 and "429" not in str(e):
                time.sleep(5)
                continue
            raise e

    raise RuntimeError("Groq analysis failed after retries.")


# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY CHARTS  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
# ... (chart_gauge, chart_asset_bars, chart_yields, chart_treemap – exact same as earlier)
# I'm omitting them for brevity, but they must be included. Copy them from the last complete code.
# They are identical.
# (See the previous full code block above – they are there.)

def _pct_color(v):
    if v is None: return BODY
    return G if v >= 0 else R

def chart_gauge(score: int = 50):
    import math
    r, cx, cy = 68, 90, 88
    def arc(s, e, col):
        a1 = (s/100)*math.pi - math.pi
        a2 = (e/100)*math.pi - math.pi
        return (f'<path d="M {cx+r*math.cos(a1):.1f} {cy+r*math.sin(a1):.1f} '
                f'A {r} {r} 0 0 1 {cx+r*math.cos(a2):.1f} {cy+r*math.sin(a2):.1f}" '
                f'stroke="{col}" stroke-width="10" fill="none" stroke-linecap="round"/>')
    ang = (score/100)*math.pi - math.pi
    nx, ny = cx + r*math.cos(ang), cy + r*math.sin(ang)
    svg = (f'<svg viewBox="0 0 180 108" style="display:block;margin:0 auto;width:170px;">'
           f'{arc(2,33,R)}{arc(34,66,A)}{arc(67,98,G)}'
           f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
           f'stroke="{INK}" stroke-width="2.5" stroke-linecap="round"/>'
           f'<circle cx="{cx}" cy="{cy}" r="5" fill="{INK}"/>'
           f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{BG}"/>'
           f'<text x="{cx}" y="{cy+22}" text-anchor="middle" font-family="JetBrains Mono,monospace" '
           f'font-size="21" font-weight="700" fill="{INK}">{score}</text></svg>')
    return svg

def chart_asset_bars(prices: dict) -> go.Figure:
    DISPLAY = [
        ("SP500","S&P 500"),("Nasdaq","Nasdaq"),("Dow","Dow Jones"),
        ("Nifty50","Nifty 50"),("Sensex","Sensex"),("Nikkei","Nikkei"),
        ("HangSeng","Hang Seng"),("DAX","DAX"),
        ("Gold","Gold"),("Crude","WTI Crude"),("Bitcoin","Bitcoin"),("DXY","DXY"),
    ]
    lbls, vals, cols, tips = [], [], [], []
    for key, lbl in DISPLAY:
        d = prices.get(key, {})
        if d.get("pct") is not None:
            v = round(d["pct"], 2)
            prev = d.get("prev")
            curr = d.get("price")
            lbls.append(lbl); vals.append(v)
            cols.append(G if v >= 0 else R)
            sign = "+" if v >= 0 else ""
            tip = f"<b>{lbl}</b><br>Now: {curr:,.2f}" if curr else f"<b>{lbl}</b>"
            if prev: tip += f"<br>Prev close: {prev:,.2f}"
            tip += f"<br>Change: {sign}{v:.2f}%"
            tips.append(tip)
    if not lbls:
        return go.Figure()
    fig = go.Figure(go.Bar(
        x=vals, y=lbls, orientation="h",
        marker_color=cols, marker_line_width=0, opacity=0.88,
        text=[f"{'+'if v>=0 else ''}{v:.2f}%" for v in vals],
        textposition="outside",
        textfont={"color": BODY, "size": 10, "family": "JetBrains Mono"},
        hovertemplate="%{customdata}<extra></extra>",
        customdata=tips,
    ))
    fig.update_layout(
        height=max(310, len(lbls)*27), margin=dict(l=0,r=60,t=4,b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=BD, zerolinecolor=BD,
                   tickfont={"color":MUT,"size":9,"family":"JetBrains Mono"},
                   ticksuffix="%"),
        yaxis=dict(showgrid=False,
                   tickfont={"color":BODY,"size":11,"family":"DM Sans"},
                   automargin=True),
        bargap=0.33,
        shapes=[dict(type="line",x0=0,x1=0,y0=-0.5,y1=len(lbls)-0.5,
                     line=dict(color=MUT,width=1),layer="below")],
    )
    return fig

def chart_yields(prices: dict) -> go.Figure:
    YIELD_ROWS = [
        ("🇺🇸 US",      "US10Y",  "US3M"),
        ("🇩🇪 Germany", "DE10Y",  None),
        ("🇯🇵 Japan",   "JP10Y",  None),
    ]
    lbls, y10, y2, hover10 = [], [], [], []
    for lbl, sym10, sym2 in YIELD_ROWS:
        d10 = prices.get(sym10, {})
        if not d10.get("price"): continue
        v10 = round(d10["price"], 2)
        d2  = prices.get(sym2, {}) if sym2 else {}
        v2  = round(d2["price"], 2) if d2.get("price") else None
        lbls.append(lbl); y10.append(v10); y2.append(v2)
        prev = d10.get("prev")
        h = f"<b>{lbl} 10Y</b><br>Now: {v10:.2f}%"
        if prev: h += f"<br>Prev: {prev:.2f}%"
        hover10.append(h)
    if not lbls:
        return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=y10, y=lbls, orientation="h", name="10Y",
        marker_color=P, marker_line_width=0, opacity=0.88,
        text=[f"{v:.2f}%" for v in y10], textposition="outside",
        textfont={"color":BODY,"size":10,"family":"JetBrains Mono"},
        hovertemplate="%{customdata}<extra></extra>", customdata=hover10,
    ))
    non_none_y2 = [v for v in y2 if v is not None]
    if non_none_y2:
        fig.add_trace(go.Bar(
            x=[v or 0 for v in y2], y=lbls, orientation="h", name="2Y/3M",
            marker_color=B, marker_line_width=0, opacity=0.65,
            text=[f"{v:.2f}%" if v else "" for v in y2], textposition="outside",
            textfont={"color":BODY,"size":10,"family":"JetBrains Mono"},
        ))
    fig.update_layout(
        barmode="overlay", height=max(180, len(lbls)*55),
        margin=dict(l=0,r=55,t=4,b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True,gridcolor=BD,zerolinecolor=BD,
                   tickfont={"color":MUT,"size":9,"family":"JetBrains Mono"},
                   ticksuffix="%"),
        yaxis=dict(showgrid=False,
                   tickfont={"color":BODY,"size":12,"family":"DM Sans"},
                   automargin=True),
        legend=dict(orientation="h",x=0,y=-0.12,
                    font={"color":MUT,"size":10},bgcolor="rgba(0,0,0,0)"),
        bargap=0.28,
    )
    return fig

def chart_treemap(sector_prices: dict, title: str) -> Optional[go.Figure]:
    names, vals, pcts, texts = [], [], [], []
    for name, d in sector_prices.items():
        v = d.get("pct")
        if v is None: continue
        v = round(v, 2)
        names.append(name); vals.append(abs(v)+0.05); pcts.append(v)
        texts.append(f"{'+'if v>=0 else ''}{v:.2f}%")
    if not names: return None
    fig = go.Figure(go.Treemap(
        labels=names, parents=[""]*len(names), values=vals,
        text=texts, texttemplate="<b>%{label}</b><br>%{text}",
        hovertemplate="<b>%{label}</b><br>%{text}<extra></extra>",
        marker=dict(
            colors=pcts,
            colorscale=[[0,"rgba(160,35,55,.85)"],[.35,"rgba(80,25,40,.55)"],
                        [.5,"rgba(18,22,32,.9)"],[.65,"rgba(12,55,45,.55)"],
                        [1,"rgba(25,150,100,.85)"]],
            cmid=0, line=dict(width=1.5, color=BG),
        ),
        textfont={"family":"DM Sans","size":12,"color":INK},
        pathbar_visible=False,
    ))
    fig.update_layout(
        title=dict(text=title, font={"color":MUT,"size":11,"family":"JetBrains Mono"}, x=0),
        height=265, margin=dict(l=0,r=0,t=28,b=0),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def hd(v): return v is not None and v != ""

def fmt_num(v, d=2, prefix="", suffix=""):
    if v is None: return "—"
    if abs(v) >= 1000:
        return f"{prefix}{v:,.{d}f}{suffix}"
    return f"{prefix}{v:.{d}f}{suffix}"

def pct_str(v, d=2):
    if v is None: return "—"
    return f"{'+'if v>=0 else ''}{v:.{d}f}%"

def stance_color(s):
    return G if s == "cut" else R if s == "hike" else A

def stance_label(s):
    return "↓ EASING" if s == "cut" else "↑ TIGHTENING" if s == "hike" else "◆ HOLD"

def sent_color(s):
    return G if s == "bullish" else R if s == "bearish" else A

def sig_color(s):
    return G if s == "buy" else R if s == "sell" else A if s == "hold" else B

def urgency_color(u):
    return R if u == "high" else A if u == "medium" else G

def dir_color(d):
    return G if d == "long" else R

def section_title(n, title, sub=""):
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:10px;'
        f'margin:1.6rem 0 .8rem 0;padding-bottom:8px;border-bottom:1px solid {BD};">'
        f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;'
        f'color:{MUT};letter-spacing:1.4px;">{n}</span>'
        f'<span style="font-size:1.5rem;font-weight:800;color:{INK};letter-spacing:-.3px;">{title}</span>'
        f'<div style="flex:1;height:1px;background:{BD};"></div>'
        f'<span style="font-family:JetBrains Mono,monospace;font-size:9px;'
        f'color:{MUT};letter-spacing:1px;text-transform:uppercase;">{sub}</span></div>',
        unsafe_allow_html=True,
    )

def color_bar(color: str):
    st.markdown(
        f'<div style="height:3px;background:{color};'
        f'border-radius:2px;margin-bottom:8px;"></div>',
        unsafe_allow_html=True,
    )

def mini_badge(text, color):
    st.markdown(
        f'<span style="display:inline-block;font-family:JetBrains Mono,monospace;'
        f'font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;'
        f'letter-spacing:.5px;text-transform:uppercase;'
        f'background:{color}22;color:{color};border:1px solid {color}44;">'
        f'{text}</span>',
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────────────────────────────────────
# SECTION RENDERERS (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def render_hero(prices, analysis, gn_gold):
    mood = analysis.get("mood", {}) if analysis else {}
    score = int(mood.get("score", 50)) if mood.get("score") is not None else 50
    label = mood.get("label", "—")
    regime = mood.get("regime", "")

    c1, c2, c3 = st.columns([1.15, 1.55, 1.3])

    with c1:
        with st.container(border=True):
            st.markdown(
                f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;'
                f'letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0;">Market Mood</p>',
                unsafe_allow_html=True,
            )
            st.markdown(chart_gauge(score), unsafe_allow_html=True)
            mood_col = {
                "Risk-On":G,"Cautious":A,"Risk-Off":R,"Volatile":B
            }.get(label, A)
            st.markdown(
                f'<div style="text-align:center;margin:4px 0;">'
                f'<span style="background:{mood_col}22;color:{mood_col};'
                f'border:1px solid {mood_col}44;border-radius:20px;'
                f'padding:4px 14px;font-family:JetBrains Mono,monospace;'
                f'font-size:13px;font-weight:700;">{label}</span></div>'
                + (f'<p style="text-align:center;font-size:10px;color:{MUT};'
                   f'font-family:JetBrains Mono,monospace;margin:3px 0;">{regime}</p>'
                   if regime else ""),
                unsafe_allow_html=True,
            )
            if hd(mood.get("summary")):
                st.caption(mood["summary"])

            st.divider()

            vix = prices.get("VIX", {}); dxy = prices.get("DXY", {})
            aed = prices.get("AEDIINR", {}); ga  = prices.get("GoldAED", {})
            if gn_gold and gn_gold.get("price"):
                ga = {"price": gn_gold["price"], "prev": ga.get("prev"), "pct": None,
                      "source": "Gulf News"}

            sc1, sc2 = st.columns(2)
            with sc1:
                if vix.get("price"):
                    st.metric("VIX",
                              f"{vix['price']:.1f}",
                              delta=pct_str(vix.get("pct")) if vix.get("pct") else None,
                              delta_color="inverse")
                if aed.get("price"):
                    src = " (calc)" if aed.get("source") == "calc" else ""
                    st.metric(f"AED/INR{src}", f"{aed['price']:.4f}",
                              delta=pct_str(aed.get("pct")) if aed.get("pct") else None,
                              delta_color="normal")
            with sc2:
                if dxy.get("price"):
                    st.metric("DXY", f"{dxy['price']:.2f}",
                              delta=pct_str(dxy.get("pct")) if dxy.get("pct") else None,
                              delta_color="inverse")
                if ga.get("price"):
                    src = ga.get("source","")
                    st.metric(f"Gold/g 24K ({src})",
                              f"AED {ga['price']:.2f}",
                              delta=pct_str(ga.get("pct")) if ga.get("pct") else None,
                              delta_color="normal")

    with c2:
        with st.container(border=True):
            st.markdown(
                f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;'
                f'letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0 0 3px 0;">'
                f'Asset Performance — vs Prior Close</p>'
                f'<p style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{BD};">'
                f'Current price · % change · Prev close on hover</p>',
                unsafe_allow_html=True,
            )
            fig = chart_asset_bars(prices)
            if fig.data:
                st.plotly_chart(fig, config={"displayModeBar": False})
            else:
                st.caption("Price data loading…")

    with c3:
        with st.container(border=True):
            st.markdown(
                f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;'
                f'letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0 0 3px 0;">'
                f'Sovereign Yields</p>'
                f'<p style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{BD};">'
                f'Purple = 10Y · Blue = 3M/2Y · US · Germany · Japan</p>',
                unsafe_allow_html=True,
            )
            fig = chart_yields(prices)
            if fig.data:
                st.plotly_chart(fig, config={"displayModeBar": False})
            else:
                st.caption("Yield data loading…")

            us10 = prices.get("US10Y",{}).get("price")
            us3m = prices.get("US3M",{}).get("price")
            if us10 and us3m:
                spread = round(us10 - us3m, 2)
                col = G if spread > 0 else R
                st.markdown(
                    f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;'
                    f'color:{col};margin-top:6px;">'
                    f'US Yield Curve (10Y−3M): {spread:+.2f}%  '
                    f'{"▲ Normal" if spread > 0 else "▼ INVERTED"}</div>',
                    unsafe_allow_html=True,
                )

            if hd(mood.get("primary_risk")):
                st.warning(f"⚠ **Primary Risk:** {mood['primary_risk']}", icon="⚠️")


def render_macro(analysis):
    items = [m for m in (analysis.get("macro") or []) if hd(m.get("headline"))]
    if not items: return
    section_title("01", "Global Macro", f"{len(items)} economies")

    cols = st.columns(len(items))
    for i, m in enumerate(items):
        sc = sent_color(m.get("sentiment",""))
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                r1, r2 = st.columns([3,1])
                with r1:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;">'
                        f'<span style="font-size:22px;">{m.get("flag","")}</span>'
                        f'<span style="font-size:15px;font-weight:800;color:{INK};">{m.get("country","")}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with r2:
                    mini_badge(m.get("sentiment","").upper(), sc)

                if hd(m.get("key_signal")):
                    st.markdown(
                        f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;'
                        f'color:{B};margin:4px 0 6px 0;">{m["key_signal"]}</p>',
                        unsafe_allow_html=True,
                    )
                st.markdown(f"**{m.get('headline','')}**")
                st.markdown(m.get("analysis",""))

                if hd(m.get("contrarian")):
                    st.info(f"🔍 **Contrarian:** {m['contrarian']}")

                if hd(m.get("cb_note")):
                    st.markdown(
                        f'<div style="background:rgba(91,141,239,.08);'
                        f'border:1px solid rgba(91,141,239,.22);border-radius:6px;'
                        f'padding:6px 9px;margin-top:6px;font-size:11px;color:{B};">'
                        f'🏦 {m["cb_note"]}</div>',
                        unsafe_allow_html=True,
                    )


def render_commodities(prices, analysis):
    comms_ai = {c["name"]: c for c in (analysis.get("commodities") or [])} if analysis else {}
    SYM_MAP = [
        ("Gold",        "Gold",   "XAU", "$/oz"),
        ("Crude",       "Crude Oil","WTI","$/bbl"),
        ("Silver",      "Silver",  "XAG","$/oz"),
        ("Copper",      "Copper",  "HG", "$/lb"),
        ("NatGas",      "Natural Gas","NG","$/MMBtu"),
    ]
    section_title("02", "Commodities", "Live prices · AI analysis")

    cols = st.columns(5)
    for i, (pkey, name, tkr, unit) in enumerate(SYM_MAP):
        pd_ = prices.get(pkey, {})
        ai  = comms_ai.get(name, {})
        sc  = sig_color(ai.get("signal","")) if ai.get("signal") else MUT

        with cols[i]:
            with st.container(border=True):
                color_bar(sc)

                nr, br = st.columns([3,1])
                with nr:
                    st.markdown(
                        f'<div style="font-size:14px;font-weight:800;color:{INK};">{name}</div>'
                        f'<div style="font-family:JetBrains Mono,monospace;font-size:8.5px;'
                        f'color:{MUT};letter-spacing:.8px;">{tkr} · {unit}</div>',
                        unsafe_allow_html=True,
                    )
                with br:
                    if ai.get("signal"):
                        mini_badge(ai["signal"].upper(), sc)

                if pd_.get("price"):
                    pv = pd_.get("pct")
                    pv_str = pct_str(pv) if pv is not None else None
                    st.metric(
                        label="",
                        value=fmt_num(pd_["price"]),
                        delta=pv_str,
                        delta_color="normal",
                    )
                    if pd_.get("prev"):
                        st.markdown(
                            f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;'
                            f'color:{MUT};margin-top:-4px;">prev {fmt_num(pd_["prev"])}</p>',
                            unsafe_allow_html=True,
                        )

                if ai.get("support") or ai.get("resistance"):
                    sc1, sc2 = st.columns(2)
                    with sc1:
                        if ai.get("support"):
                            st.markdown(
                                f'<p style="font-family:JetBrains Mono,monospace;'
                                f'font-size:8px;color:{MUT};margin-bottom:1px;">SUPPORT</p>'
                                f'<p style="font-family:JetBrains Mono,monospace;'
                                f'font-size:11px;font-weight:700;color:{G};">{ai["support"]}</p>',
                                unsafe_allow_html=True,
                            )
                    with sc2:
                        if ai.get("resistance"):
                            st.markdown(
                                f'<p style="font-family:JetBrains Mono,monospace;'
                                f'font-size:8px;color:{MUT};margin-bottom:1px;">RESISTANCE</p>'
                                f'<p style="font-family:JetBrains Mono,monospace;'
                                f'font-size:11px;font-weight:700;color:{R};">{ai["resistance"]}</p>',
                                unsafe_allow_html=True,
                            )

                if hd(ai.get("analysis")):
                    st.caption(ai["analysis"])

                if hd(ai.get("positioning_note")):
                    st.info(f"📊 {ai['positioning_note']}")


def render_heatmaps(prices):
    us_sec = {
        "Technology":  prices.get("secTech",{}),
        "Financials":  prices.get("secFin",{}),
        "Healthcare":  prices.get("secHlth",{}),
        "Energy":      prices.get("secEnrg",{}),
        "Materials":   prices.get("secMatl",{}),
        "Industrials": prices.get("secInds",{}),
        "Cons. Disc":  prices.get("secCyc",{}),
        "Cons. Stap":  prices.get("secStpl",{}),
        "Utilities":   prices.get("secUtil",{}),
        "Real Estate": prices.get("secRE",{}),
        "Comm. Svc":   prices.get("secComm",{}),
    }
    in_sec = {
        "IT":       prices.get("inSIT",{}),  "Banking": prices.get("inSBnk",{}),
        "Pharma":   prices.get("inSPhrm",{}),"Auto":    prices.get("inSAut",{}),
        "FMCG":     prices.get("inSFmcg",{}),"Metal":   prices.get("inSMtl",{}),
        "Energy":   prices.get("inSEnrg",{}),"Realty":  prices.get("inSRlt",{}),
        "Infra":    prices.get("inSInf",{}), "Media":   prices.get("inSMed",{}),
        "PSU Bank": prices.get("inSPSU",{}),
    }
    has_us = any(d.get("pct") is not None for d in us_sec.values())
    has_in = any(d.get("pct") is not None for d in in_sec.values())
    if not has_us and not has_in: return

    section_title("03", "Sector Heatmaps", "US + India · vs prior close")
    c1, c2 = st.columns(2)
    with c1:
        fig = chart_treemap(us_sec, "🇺🇸  US — S&P 500 GICS Sectors")
        if fig: st.plotly_chart(fig, config={"displayModeBar":False})
        else:   st.caption("US sector data unavailable")
    with c2:
        fig = chart_treemap(in_sec, "🇮🇳  India — NSE Sectoral Indices")
        if fig: st.plotly_chart(fig, config={"displayModeBar":False})
        else:   st.caption("India sector data unavailable")


def render_central_banks(cb: dict):
    section_title("04", "Central Banks & Inflation",
                  "Rates + CPI · Source: Trading Economics")

    rows = []
    for key, v in cb.items():
        rd = round(v["rate"]-v["rp"],2) if v.get("rate") and v.get("rp") else None
        cd = round(v["cpi"]-v["cpip"],2) if v.get("cpi") is not None and v.get("cpip") is not None else None
        rows.append({
            "": f"{v['flag']} {key}",
            "Bank": v["bank"],
            "Rate": f"{v['rate']:.2f}%",
            "Prev Rate": f"{v['rp']:.2f}%" + (f" ({'+' if rd>=0 else ''}{rd:.2f}pp)" if rd else ""),
            "Rate Date": v.get("rd","—"),
            "CPI": f"{v['cpi']:.1f}%" + ("  ↑" if cd and cd>0 else "  ↓" if cd and cd<0 else ""),
            "Prev CPI": f"{v['cpip']:.1f}%" if v.get("cpip") is not None else "—",
            "CPI Date": v.get("cpid","—"),
            "Stance": stance_label(v.get("stance","")),
            "Next Mtg": v.get("next","—"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={
                     "": st.column_config.TextColumn(width="small"),
                     "Rate": st.column_config.TextColumn(width="small"),
                     "Stance": st.column_config.TextColumn(width="medium"),
                 })


def render_picks(analysis):
    picks = [p for p in (analysis.get("picks") or []) if hd(p.get("name"))]
    if not picks: return
    section_title("05", "Trade Ideas", f"{len(picks)} picks · AI-generated")

    cols = st.columns(len(picks))
    for i, p in enumerate(picks):
        dc = dir_color(p.get("direction",""))
        conv = p.get("conviction","low")
        with cols[i]:
            with st.container(border=True):
                color_bar(dc)
                st.markdown(
                    f'<div style="font-size:15px;font-weight:800;color:{INK};">{p.get("name","")}</div>'
                    f'<div style="font-family:JetBrains Mono,monospace;font-size:9px;'
                    f'color:{MUT};">{p.get("type","").upper()} · {p.get("region","")}</div>',
                    unsafe_allow_html=True,
                )
                dir_lbl = "▲ LONG" if p.get("direction")=="long" else "▼ SHORT"
                st.markdown(
                    f'<div style="margin:6px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;'
                    f'font-weight:700;padding:3px 8px;border-radius:4px;'
                    f'background:{dc}22;color:{dc};border:1px solid {dc}44;">{dir_lbl}</span>'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};">'
                    f'{p.get("timeframe","")}</span>'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};">'
                    f'{conv.upper()} CONV</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if hd(p.get("headline")):
                    st.markdown(f"**{p['headline']}**")
                if hd(p.get("thesis")):
                    st.markdown(p["thesis"])
                if hd(p.get("risk")):
                    st.error(f"⚡ **Risk:** {p['risk']}", icon="⚡")


def render_geo(analysis):
    geo = [g for g in (analysis.get("geo") or []) if hd(g.get("headline"))]
    if not geo: return
    section_title("06", "Hedge Fund Radar", f"{len(geo)} themes")

    cols = st.columns(3)
    for i, g in enumerate(geo):
        uc = urgency_color(g.get("urgency",""))
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;margin-bottom:4px;">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-size:9px;'
                    f'font-weight:700;letter-spacing:1.2px;color:{MUT};text-transform:uppercase;">'
                    f'{g.get("icon","")} {g.get("category","")}</span>'
                    f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
                    f'background:{uc};box-shadow:0 0 5px {uc};"></span></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(f"**{g.get('headline','')}**")
                if hd(g.get("analysis")):
                    st.markdown(g["analysis"])


# ─────────────────────────────────────────────────────────────────────────────
# SETUP PAGE
# ─────────────────────────────────────────────────────────────────────────────

def render_setup():
    st.markdown("<br>", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 2.5, 1])
    with col:
        st.markdown(
            f'<div style="text-align:center;font-size:3.5rem;margin-bottom:.5rem;">📊</div>'
            f'<h2 style="text-align:center;color:{INK};font-weight:800;'
            f'letter-spacing:-.4px;">AlphaTerminal Setup</h2>'
            f'<p style="text-align:center;color:{BODY};">3 steps · takes 2 minutes · powered by Groq (free)</p>',
            unsafe_allow_html=True,
        )
        st.divider()
        for n, title, detail in [
            ("1", "Get a free Groq API key",
             "Go to **[console.groq.com](https://console.groq.com/)** → sign up → **API Keys** → **Create API Key** → copy it."),
            ("2", "Open Streamlit Cloud Secrets",
             "At **[share.streamlit.io](https://share.streamlit.io)** → your app → **⋮ → Settings → Secrets** (or `.streamlit/secrets.toml` locally)."),
            ("3", "Add the secret and save",
             "Paste the following into the secrets box and click **Save**:"),
        ]:
            c1, c2 = st.columns([0.08, 0.92])
            with c1:
                st.markdown(
                    f'<div style="background:{G};color:{BG};font-weight:800;'
                    f'font-size:13px;padding:3px 8px;border-radius:5px;'
                    f'text-align:center;margin-top:4px;">{n}</div>',
                    unsafe_allow_html=True,
                )
            with c2:
                st.markdown(f"**{title}**")
                st.markdown(detail)

        st.code('GROQ_API_KEY = "gsk_your_groq...key_here..."', language="toml")
        st.success("Dashboard restarts automatically – your AI analysis will load within a minute.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    api_key = st.secrets.get("GROQ_API_KEY", "").strip()
    now_uae = datetime.now(ZoneInfo("Asia/Dubai"))

    # HEADER
    h1, h2, h3, h4 = st.columns([2, 5, 1, 1])
    with h1:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0 4px;">'
            f'<div style="width:32px;height:32px;border-radius:8px;flex-shrink:0;'
            f'background:linear-gradient(135deg,{G},{B});'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:18px;color:{BG};font-weight:900;">α</div>'
            f'<div><div style="font-size:20px;font-weight:900;color:{INK};letter-spacing:-.4px;">'
            f'Alpha<span style="color:{G};">Terminal</span></div>'
            f'<div style="font-family:JetBrains Mono,monospace;font-size:8.5px;'
            f'color:{MUT};letter-spacing:1.5px;">MACRO · COMMODITIES · ALPHA</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
    with h2:
        st.markdown(
            f'<div style="padding:10px 0 0;">'
            f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;color:{MUT};">'
            f'{now_uae.strftime("%A, %d %B %Y  %H:%M")} GST &nbsp;·&nbsp; '
            f'All % changes vs prior trading day close &nbsp;·&nbsp; '
            f'Prices: Yahoo Finance · CB data: Trading Economics · Analysis: Groq (Llama 3.3 70B)'
            f'</span></div>',
            unsafe_allow_html=True,
        )
    with h3:
        refresh_p = st.button("↺ Prices", use_container_width=True,
                               help="Refresh live prices (free, zero tokens)")
    with h4:
        refresh_a = st.button("⚡ Analysis", use_container_width=True,
                               help="Re-run AI analysis (uses Groq's free API)")

    st.markdown(
        f'<hr style="border:none;border-top:1px solid {BD};margin:2px 0 10px 0;">',
        unsafe_allow_html=True,
    )

    if refresh_p:
        fetch_prices.clear()
        fetch_trading_economics.clear()
        fetch_gold_aed_gulfnews.clear()
        st.rerun()

    if refresh_a:
        fetch_analysis.clear()
        st.rerun()

    if not api_key:
        render_setup()
        return

    # FETCH DATA
    with st.spinner("Fetching live market data…"):
        prices = fetch_prices()
        cb     = fetch_trading_economics()
        gn     = fetch_gold_aed_gulfnews()

    if gn and gn.get("price") and "GoldAED" in prices:
        prices["GoldAED"]["price_gn"] = gn["price"]
        prices["GoldAED"]["source"]   = "Gulf News"

    # AI ANALYSIS
    analysis = None
    with st.spinner("Running institutional AI analysis (Groq, cached 24h, ~15s)…"):
        try:
            analysis = fetch_analysis(api_key, TODAY)
        except Exception as e:
            st.error(f"❌ Analysis failed: {e}", icon="❌")
            analysis = None

    # STATUS BAR
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        st.markdown(
            f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{MUT};">'
            f'● PRICES: {prices.get("_fetched","—")}</p>',
            unsafe_allow_html=True,
        )
    with c2:
        ai_status = f"✓ ANALYSIS LOADED" if analysis else "⚠ ANALYSIS UNAVAILABLE"
        ai_col    = G if analysis else A
        st.markdown(
            f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{ai_col};">'
            f'{ai_status}</p>',
            unsafe_allow_html=True,
        )
    with c3:
        te_indicator = "✓ Trading Economics" if any(v.get("rd") for v in cb.values()) else "⚠ TE fallback"
        st.markdown(
            f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{MUT};">'
            f'{te_indicator}</p>',
            unsafe_allow_html=True,
        )

    if not analysis:
        st.warning("AI analysis unavailable – prices and CB data are live. Click ⚡ Analysis to retry.", icon="⚠️")

    st.divider()

    # RENDER DASHBOARD
    render_hero(prices, analysis, gn)
    if analysis:
        render_macro(analysis)
    render_commodities(prices, analysis)
    render_heatmaps(prices)
    render_central_banks(cb)
    if analysis:
        render_picks(analysis)
        render_geo(analysis)

    st.divider()
    st.markdown(
        f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;color:{BD};'
        f'text-align:center;padding:8px 0;">'
        f'ALPHA TERMINAL · PRICES: YAHOO FINANCE · CB DATA: TRADING ECONOMICS · '
        f'ANALYSIS: GROQ (LLAMA 3.3 70B) · NOT FINANCIAL ADVICE</p>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
