"""
AlphaTerminal — Institutional Macro Dashboard v11
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARCHITECTURE:
  ↺ Data Button   → yfinance (15-min cache) + FRED (daily cache)
  ⚡ AI Button    → 3-stage pipeline, runs ONCE per day, date-keyed cache
                    Stage 1: Gemini 2.0 Flash + Google Search → 24-48hr news brief
                    Stage 2: Gemini 2.5 Flash → Deep macro + yield curve (JSON)
                    Stage 3: Gemini 2.5 Flash → Commodities + Picks + Geo (JSON)
  Keep-Alive      → JS heartbeat pings /_stcore/health every 4 min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import json
import logging
import warnings
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Dict

from fredapi import Fred
from google import genai
from google.genai import types

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
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
O = "#fb923c"   # orange for medium urgency

# ─── CSS ──────────────────────────────────────────────────────────────────────
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
.stExpander{{border:1px solid {BD}!important;border-radius:8px!important}}
.stTabs [data-baseweb="tab-list"]{{background:{SF}!important;border-radius:6px;gap:2px;padding:3px}}
.stTabs [data-baseweb="tab"]{{background:transparent!important;color:{MUT}!important;font-size:11px!important;
  padding:4px 10px!important;border-radius:4px!important}}
.stTabs [aria-selected="true"]{{background:{BD}!important;color:{INK}!important}}
.order-tag{{display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;
  border-radius:3px;background:{BD};color:{MUT};margin-bottom:4px;}}
</style>""", unsafe_allow_html=True)

# ─── KEEP-ALIVE: JS heartbeat so app never sleeps ─────────────────────────────
components.html("""
<script>
(function() {
    // Ping Streamlit health endpoint every 4 minutes to prevent idle sleep
    function heartbeat() {
        fetch('/_stcore/health', {method: 'GET', mode: 'no-cors', cache: 'no-store'})
            .catch(function() {});   // silently ignore errors
    }
    setInterval(heartbeat, 4 * 60 * 1000);
    heartbeat(); // immediate first ping
})();
</script>
""", height=0)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
AED_PEG = 3.6725
TROY_OZ = 31.1035

FRED_SERIES = {
    "US":    {"rate": "FEDFUNDS",          "cpi": "CPIAUCSL",               "bank": "Fed",  "flag": "🇺🇸"},
    "Euro":  {"rate": "ECBDFR",            "cpi": "CP0000EZ19M086NES",      "bank": "ECB",  "flag": "🇪🇺"},
    "Japan": {"rate": "INTDSRJPM193N",     "cpi": "JPNCPIALLMINMEI",        "bank": "BOJ",  "flag": "🇯🇵"},
    "India": {"rate": "INTDSRINM193N",     "cpi": "INDCPIALLMINMEI",        "bank": "RBI",  "flag": "🇮🇳"},
    "China": {"rate": "INTDSRCNM193N",     "cpi": "CHNCPIALLMINMEI",        "bank": "PBOC", "flag": "🇨🇳"},
}

# ─── AI SYSTEM PROMPTS ────────────────────────────────────────────────────────

NEWS_SEARCH_PROMPT = """You are a macro intelligence aggregator for a $50B global macro hedge fund.
Search the web RIGHT NOW and compile only news from the last 24-48 hours that matters for macro markets.
Today is {today_date}.

Search for and report ONLY what actually happened — no summaries of old events, no fabricated data:

1. CENTRAL BANKS: Fed officials speeches/testimony, FOMC minutes, ECB/BOJ/RBI/PBOC rate decisions or forward guidance
2. ECONOMIC DATA PRINTS: CPI, PPI, PCE, NFP/payrolls, unemployment, GDP revisions, PMI flash, retail sales,
   housing starts, trade balance — include ACTUAL vs EXPECTED vs PRIOR for each print
3. COMMODITIES: OPEC+ production decisions, EIA/API crude inventory data, gold ETF flows (GLD/IAU), LME metals
4. GEOPOLITICS & TRADE: Tariff announcements/escalations, sanctions, ceasefire/conflict developments, bilateral trade deals
5. CREDIT & FUNDING MARKETS: IG/HY spread moves >5bp, repo market stress, sovereign rating changes, bond auction tails
6. FX & CURRENCY: FX interventions (BOJ/PBOC), major EM currency stress, carry trade unwind signals
7. MARKET STRUCTURE: Significant CFTC/COT positioning shifts, notable fund liquidations, large ETF flows, M&A that moves sectors

For EACH item:
— State the exact date and time if available
— What happened (specific numbers matter)
— Immediate market reaction if observable
— One-line: Why this matters for the next 1-4 weeks

If no significant event in a category in the last 48 hours, write: "Quiet — no major developments."

Be ruthlessly factual. Specificity is your credibility. Do not pad with background context."""

MACRO_ANALYSIS_SYSTEM = """You are a senior macro strategist whose analysis framework combines:
• Ray Dalio — debt supercycles, paradigm shifts, and the beautiful deleveraging template
• George Soros — reflexivity: how market prices feed back into fundamentals creating boom-bust dynamics
• Stanley Druckenmiller — top-down macro conviction, identify the dominant factor and size accordingly
• Paul Tudor Jones — macro momentum, technical levels as confirmation of fundamental theses

MANDATORY THREE-LAYER ANALYSIS for each country/region:
→ 1st Order (Obvious): What the data literally shows. What 90% of participants see.
→ 2nd Order (Edge): Knock-on effects on OTHER asset classes, currencies, and trading partners.
   Where capital flows because of this. What sector re-rates. What EM gets hit or bid.
→ 3rd Order (Alpha): Structural regime shifts. Reflexivity loops being established.
   What the consensus doesn't see yet but will in 3-6 months. Positioning extremes.
   Historical analogs. What Soros would call the "far from equilibrium" condition.

DISCIPLINE RULES:
— Reference SPECIFIC numbers from the provided live data and news brief
— If a figure is not in the provided data, state "data not available" — never fabricate numbers
— Central bank notes must address: current stance → most likely next action →
  what would SURPRISE the market → reflexivity implication for rates/FX/credit
— Contrarian views must be backed by positioning extreme, valuation anomaly, or historical analog
— Write for a PM managing $10B in real capital, not a student

Return ONLY a valid raw JSON object. No markdown fences, no backticks, no preamble:
{
  "mood": {
    "score": 50,
    "label": "Risk-On|Risk-Off|Cautious|Volatile|Euphoric|Panic",
    "regime": "Current regime in 8 words max",
    "summary": "3 sentences: (1) dominant macro driver right now (2) primary tension or stress fracture (3) what resolves/triggers the next move",
    "primary_risk": "Single most important near-term risk — specific, not generic",
    "tail_risk": "Low-probability but catastrophic left-tail scenario in one sentence",
    "consensus_trade": "What most institutional portfolios are currently positioned for",
    "contrarian_setup": "Where asymmetric opportunity lies — what consensus is missing"
  },
  "macro": [
    {
      "country": "US",
      "flag": "🇺🇸",
      "headline": "Sharp, data-specific headline — not generic",
      "sentiment": "bullish|bearish|neutral",
      "analysis": "Deep analysis min 120 words. Open with specific data from the news brief or live prices. Then 2nd order effects on global capital flows, EM, credit. Then 3rd order structural insight or reflexivity dynamic being set up.",
      "key_signal": "The single most important metric to monitor right now and exactly why",
      "second_order": "Specific knock-on effects for OTHER asset classes, currencies, sectors, countries",
      "third_order": "Structural shift, reflexivity loop, positioning extreme, or regime change signal",
      "contrarian": "Where consensus is wrong — back it with positioning data, sentiment extreme, or historical analog",
      "cb_note": "CB current stance → next likely action → probability → what would SURPRISE → reflexivity implication for FX/credit/equities",
      "data_dependency": "The specific upcoming data print that could flip this narrative — name it and expected timing"
    }
  ],
  "yield_curve": {
    "us_spread": "US 10Y minus 3M: current level, trend, inversion depth vs history, recession probability read",
    "global_divergence": "BOJ vs Fed vs ECB rate/yield differential — carry trade flows and FX implications",
    "credit_message": "IG/HY spread level and direction — what credit is pricing for growth and risk appetite"
  }
}
The 'macro' array must contain exactly 5 objects for: US, Euro, Japan, India, China (in that order)."""

MARKETS_ANALYSIS_SYSTEM = """You are the CIO and Chief Risk Officer of a $25B global macro fund.
Your markets analysis is anchored in the provided live prices, OHLC data, and news brief.
Every single statement must reference a specific price level, percentage move, or news event.
No vague qualitative assessments. No generic macro commentary. Data-backed conviction only.

COMMODITY ANALYSIS FRAMEWORK:
— Open with: Current price + 1-day + 5-day % move from the data
— Technical: Key support/resistance from OHLC, trend context
— Macro driver: What fundamental factor is the primary mover right now
— Positioning: Any COT/ETF flow insight from news brief
— 2nd Order: What does this commodity's price action signal for FX, inflation, equities?
— Catalyst + Risk: What upcoming event moves it, what invalidates the thesis

PICKS FRAMEWORK:
— Every pick needs a CATALYST that happened in the last 48 hours (from news brief)
— Specific entry approach, price target, and stop
— The risk/reward ratio must be stated explicitly
— Mix: 2 equity/ETF, 1 FX, 1 commodity or bond

GEO FRAMEWORK:
— Must reference a specific event from the news brief
— State WHICH assets are affected and in WHICH direction
— Provide a specific hedging instrument

Return ONLY a valid raw JSON object. No markdown fences, no backticks, no preamble:
{
  "commodities": [
    {
      "name": "Gold",
      "signal": "buy|sell|hold",
      "support": 0.0,
      "resistance": 0.0,
      "analysis": "Current price from live data + % move. Technical levels from OHLC. Macro driver. Positioning if available. Min 90 words.",
      "positioning_note": "COT positioning or ETF flow data from news brief if available, else current fund consensus",
      "second_order": "What this price action signals for USD strength, real rates, inflation expectations, equity risk premium",
      "catalyst": "Specific upcoming event/data print that could drive next significant move",
      "risk_scenario": "Specific trigger that would invalidate this signal and flip direction"
    }
  ],
  "picks": [
    {
      "type": "Stock|ETF|FX|Bond|Commodity",
      "name": "TICKER",
      "region": "US|India|EU|Japan|EM|Global",
      "direction": "long|short",
      "conviction": "high|medium",
      "timeframe": "1W|1M|3M|6M",
      "headline": "One sharp line — the core thesis",
      "thesis": "Min 90 words. Must open with the specific catalyst from the news brief. Then 2nd order reasoning. Then 3rd order structural edge. Why now, why this instrument.",
      "entry_note": "Dip-buy|Breakout|Scale-in|Fade rally — specific approach",
      "target": "Specific price target or expected % move",
      "risk": "Specific stop-loss trigger or invalidation scenario",
      "risk_reward": "e.g. 3:1"
    }
  ],
  "geo": [
    {
      "category": "Fed Policy|Trade War|Geopolitics|Credit Stress|FX Dynamics|Energy Security|Tech/AI War",
      "icon": "🌍",
      "headline": "Specific, current theme — reference actual event from news brief",
      "analysis": "Market impact with layered effects. Min 70 words. State which assets move, which direction, on what timeline. Include 2nd order effect.",
      "urgency": "high|medium|low",
      "assets_affected": "Specific tickers or asset classes most exposed to this theme",
      "time_horizon": "When this theme peaks or resolves",
      "hedge": "Specific instrument or strategy to hedge this risk"
    }
  ]
}
Exactly 5 commodities: Gold, Silver, Crude Oil, Copper, Natural Gas.
Exactly 4 picks (diverse: mix regions and asset classes).
Exactly 4 geo themes."""

# ─── YFINANCE DATA ────────────────────────────────────────────────────────────
def _yf_ticker(sym: str) -> dict:
    try:
        hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        if hist.empty:
            return {}
        cl = hist["Close"].dropna()
        if len(cl) < 1:
            return {}
        last = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) >= 2 else None
        pct  = ((last - prev) / prev * 100) if prev and prev != 0 else None
        return {"price": last, "prev": prev, "pct": pct, "date": str(cl.index[-1].date())}
    except Exception:
        return {}

@st.cache_data(ttl=900, show_spinner=False)
def fetch_prices() -> dict:
    syms = {
        "^GSPC": "SP500",   "^IXIC": "Nasdaq",   "^DJI": "Dow",
        "^NSEI": "Nifty50", "^BSESN": "Sensex",  "^N225": "Nikkei",
        "^HSI":  "HangSeng","^GDAXI": "DAX",
        "GC=F":  "Gold",    "CL=F":   "Crude",   "SI=F": "Silver",
        "HG=F":  "Copper",  "NG=F":   "NatGas",
        "DX-Y.NYB": "DXY",  "^VIX":   "VIX",
        "BTC-USD": "Bitcoin",
        "USDINR=X": "USDINR","^TNX": "US10Y", "^IRX": "US3M",
        "^GDBR10": "DE10Y", "^JRGB": "JP10Y",
        # US Sector ETFs
        "XLK": "secTech", "XLF": "secFin",  "XLV": "secHlth",
        "XLE": "secEnrg", "XLB": "secMatl", "XLI": "secInds",
        "XLY": "secCyc",  "XLP": "secStpl", "XLU": "secUtil",
        "XLRE":"secRE",   "XLC": "secComm",
        # India Sector Indices
        "^CNXIT":    "inSIT",   "^NSEBANK":  "inSBnk", "^CNXPHARMA": "inSPhrm",
        "^CNXAUTO":  "inSAut",  "^CNXFMCG":  "inSFmcg","^CNXMETAL":  "inSMtl",
        "^CNXENERGY":"inSEnrg", "^CNXREALTY":"inSRlt", "^CNXINFRA":  "inSInf",
        "^CNXMEDIA": "inSMed",  "^CNXPSUBANK":"inSPSU",
    }
    results = {}
    for sym, key in syms.items():
        d = _yf_ticker(sym)
        if d:
            results[key] = d

    if "USDINR" in results:
        usd_inr = results["USDINR"]["price"]
        results["AEDIINR"] = {
            "price": usd_inr / AED_PEG,
            "prev":  results["USDINR"].get("prev", usd_inr) / AED_PEG,
            "pct":   results["USDINR"].get("pct"),
        }
    if "Gold" in results:
        gp = results["Gold"]["price"]
        pp = results["Gold"].get("prev", gp)
        results["GoldAED"] = {
            "price": gp / TROY_OZ * AED_PEG,
            "prev":  pp / TROY_OZ * AED_PEG,
            "pct":   results["Gold"].get("pct"),
        }
    results["_fetched"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y  %H:%M %Z")
    return results

@st.cache_data(ttl=900, show_spinner=False)
def fetch_commodity_ohlc() -> dict:
    symbols = {
        "Gold": "GC=F", "Crude Oil": "CL=F", "Silver": "SI=F",
        "Copper": "HG=F", "Natural Gas": "NG=F",
    }
    ohlc = {}
    for name, sym in symbols.items():
        hist = yf.Ticker(sym).history(period="5d")
        if not hist.empty:
            df = hist[["Open", "High", "Low", "Close"]].tail(3)
            ohlc[name] = df.reset_index().to_dict(orient="records")
            for rec in ohlc[name]:
                rec["Date"] = str(rec["Date"].date())
    return ohlc

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fred_macro(api_key: str) -> dict:
    if not api_key:
        return {}
    fred = Fred(api_key=api_key)
    macro_data = {}
    for country, params in FRED_SERIES.items():
        try:
            rate_data = fred.get_series(params["rate"], observation_start=datetime.now() - timedelta(days=90))
            cpi_data  = fred.get_series(params["cpi"],  observation_start=datetime.now() - timedelta(days=400))
            curr_rate = rate_data.iloc[-1] if not rate_data.empty else 0.0
            prev_rate = rate_data.iloc[-2] if len(rate_data) > 1 else curr_rate
            if len(cpi_data) >= 13:
                cpi_yoy      = ((cpi_data.iloc[-1]  - cpi_data.iloc[-13]) / cpi_data.iloc[-13]) * 100
                prev_cpi_yoy = ((cpi_data.iloc[-2]  - cpi_data.iloc[-14]) / cpi_data.iloc[-14]) * 100
            else:
                cpi_yoy = prev_cpi_yoy = 0.0
            macro_data[country] = {
                "bank":   params["bank"],
                "flag":   params["flag"],
                "rate":   round(float(curr_rate), 2),
                "rp":     round(float(prev_rate), 2),
                "rd":     rate_data.index[-1].strftime("%b %Y") if not rate_data.empty else "—",
                "cpi":    round(float(cpi_yoy), 2),
                "cpip":   round(float(prev_cpi_yoy), 2),
                "cpid":   cpi_data.index[-1].strftime("%b %Y") if not cpi_data.empty else "—",
                "stance": "hike" if curr_rate > prev_rate else ("cut" if curr_rate < prev_rate else "hold"),
            }
        except Exception:
            macro_data[country] = {
                "bank": params["bank"], "flag": params["flag"],
                "rate": 0, "rp": 0, "cpi": 0, "cpip": 0, "stance": "hold",
            }
    return macro_data

# ─── SLIM PRICES FOR AI (saves ~60% tokens in prompt) ─────────────────────────
def slim_prices_for_ai(prices: dict) -> dict:
    KEEP = [
        "SP500","Nasdaq","Dow","Nifty50","Sensex","Nikkei","HangSeng","DAX",
        "Gold","Crude","Silver","Copper","NatGas",
        "DXY","VIX","Bitcoin","USDINR","US10Y","US3M","DE10Y","JP10Y",
    ]
    out = {}
    for k in KEEP:
        v = prices.get(k, {})
        if isinstance(v, dict) and v.get("price"):
            out[k] = {
                "price": round(float(v["price"]), 3),
                "pct_1d": round(float(v["pct"]), 2) if v.get("pct") is not None else None,
                "date": v.get("date", ""),
            }
    return out

# ─── THREE-STAGE AI ENGINE (all date-keyed → daily cache) ────────────────────

@st.cache_data(ttl=172800, show_spinner=False)   # 48h safety-net TTL; date key forces daily refresh
def fetch_ai_news_brief(gemini_key: str, today_date: str) -> str:
    """
    Stage 1 — Google Search grounded news aggregation.
    Uses gemini-2.0-flash with live search tool. Cached once per day (today_date key).
    """
    try:
        client = genai.Client(api_key=gemini_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=NEWS_SEARCH_PROMPT.format(today_date=today_date),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.05,
            ),
        )
        return response.text
    except Exception as e:
        return (
            f"## ⚠ News Brief Unavailable\n"
            f"Error: {e}\n\n"
            f"Analysis below will rely on live market data and FRED only."
        )

@st.cache_data(ttl=172800, show_spinner=False)
def run_macro_analysis(
    gemini_key: str,
    prices_json: str,
    macro_json: str,
    news_brief: str,
    today_date: str,
) -> dict:
    """
    Stage 2 — Deep macro + mood + yield curve analysis.
    Uses gemini-2.5-flash with JSON output. Cached once per day.
    """
    try:
        client = genai.Client(api_key=gemini_key)
        prompt = f"""TODAY: {today_date}

LIVE MARKET PRICES & YIELDS (yfinance, 15-min delayed):
{prices_json}

CENTRAL BANK POLICY RATES & YoY INFLATION (FRED — official data):
{macro_json}

LATEST 24-48 HOUR NEWS BRIEF (live Google Search — treat as ground truth):
{news_brief}

Analyze ALL of the above and return the deep macro JSON.
Every insight must directly reference a specific number or event from the data above.
Do not use information from your training data unless it contextualises something in the brief."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=MACRO_ANALYSIS_SYSTEM,
                response_mime_type="application/json",
                temperature=0.15,
                max_output_tokens=8192,
            ),
        )
        return json.loads(response.text)
    except json.JSONDecodeError:
        # Attempt to extract JSON even if there are stray characters
        try:
            text = response.text
            start = text.find("{")
            end   = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        st.error("⚠ Macro analysis JSON parse failed. Try AI Sync again.")
        return {}
    except Exception as e:
        st.error(f"⚠ Macro analysis failed: {e}")
        return {}

@st.cache_data(ttl=172800, show_spinner=False)
def run_markets_analysis(
    gemini_key: str,
    prices_json: str,
    ohlc_json: str,
    news_brief: str,
    today_date: str,
) -> dict:
    """
    Stage 3 — Commodities + trade picks + geo radar.
    Uses gemini-2.5-flash with JSON output. Cached once per day.
    """
    try:
        client = genai.Client(api_key=gemini_key)
        prompt = f"""TODAY: {today_date}

LIVE PRICES (yfinance, 15-min delayed):
{prices_json}

COMMODITY OHLC — last 3 sessions:
{ohlc_json}

LATEST 24-48 HOUR NEWS BRIEF (live Google Search — treat as ground truth):
{news_brief}

Generate the markets analysis JSON (commodities, picks, geo).
Every commodity analysis must open with the live price from the data above.
Every trade pick must cite a catalyst from the news brief.
Every geo theme must reference an actual event from the news brief."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=MARKETS_ANALYSIS_SYSTEM,
                response_mime_type="application/json",
                temperature=0.15,
                max_output_tokens=8192,
            ),
        )
        return json.loads(response.text)
    except json.JSONDecodeError:
        try:
            text = response.text
            start = text.find("{")
            end   = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        st.error("⚠ Markets analysis JSON parse failed. Try AI Sync again.")
        return {}
    except Exception as e:
        st.error(f"⚠ Markets analysis failed: {e}")
        return {}

# ─── CHARTS ───────────────────────────────────────────────────────────────────
def chart_gauge(score: int = 50):
    import math
    r, cx, cy = 68, 90, 88
    def arc(s, e, col):
        a1 = (s / 100) * math.pi - math.pi
        a2 = (e / 100) * math.pi - math.pi
        return (f'<path d="M {cx+r*math.cos(a1):.1f} {cy+r*math.sin(a1):.1f} '
                f'A {r} {r} 0 0 1 {cx+r*math.cos(a2):.1f} {cy+r*math.sin(a2):.1f}" '
                f'stroke="{col}" stroke-width="10" fill="none" stroke-linecap="round"/>')
    ang = (score / 100) * math.pi - math.pi
    nx, ny = cx + r * math.cos(ang), cy + r * math.sin(ang)
    return (f'<svg viewBox="0 0 180 108" style="display:block;margin:0 auto;width:170px;">'
            f'{arc(2,33,R)}{arc(34,66,A)}{arc(67,98,G)}'
            f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
            f'stroke="{INK}" stroke-width="2.5" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="{INK}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="{BG}"/>'
            f'<text x="{cx}" y="{cy+22}" text-anchor="middle" font-family="JetBrains Mono,monospace" '
            f'font-size="21" font-weight="700" fill="{INK}">{score}</text></svg>')

def chart_asset_bars(prices: dict) -> go.Figure:
    DISPLAY = [
        ("SP500","S&P 500"),   ("Nasdaq","Nasdaq"),    ("Dow","Dow Jones"),
        ("Nifty50","Nifty 50"),("Sensex","Sensex"),    ("Nikkei","Nikkei"),
        ("HangSeng","Hang Seng"),("DAX","DAX"),
        ("Gold","Gold"),       ("Crude","WTI Crude"),  ("Bitcoin","Bitcoin"),
        ("DXY","DXY"),
    ]
    lbls, vals, cols, tips = [], [], [], []
    for key, lbl in DISPLAY:
        d = prices.get(key, {})
        if d.get("pct") is not None:
            v = round(d["pct"], 2)
            lbls.append(lbl); vals.append(v)
            cols.append(G if v >= 0 else R)
            tips.append(f"<b>{lbl}</b><br>Price: {d.get('price',0):,.2f}<br>Change: {v:+.2f}%")

    fig = go.Figure(go.Bar(
        x=vals, y=lbls, orientation="h", marker_color=cols, opacity=0.88,
        text=[f"{'+' if v >= 0 else ''}{v:.2f}%" for v in vals],
        textposition="outside",
        textfont={"color": BODY, "size": 10},
        customdata=tips, hovertemplate="%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        height=max(310, len(lbls) * 27), margin=dict(l=0, r=60, t=4, b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=BD, zerolinecolor=BD),
        yaxis=dict(showgrid=False, automargin=True), bargap=0.33,
    )
    return fig

def chart_yields(prices: dict) -> go.Figure:
    ROWS = [("🇺🇸 US", "US10Y", "US3M"), ("🇩🇪 Germany", "DE10Y", None), ("🇯🇵 Japan", "JP10Y", None)]
    lbls, y10, y2 = [], [], []
    for lbl, sym10, sym2 in ROWS:
        d10 = prices.get(sym10, {})
        if not d10.get("price"):
            continue
        lbls.append(lbl)
        y10.append(round(d10["price"], 2))
        y2.append(round(prices.get(sym2, {}).get("price", 0), 2)
                  if sym2 and prices.get(sym2, {}).get("price") else None)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=y10, y=lbls, orientation="h", name="10Y", marker_color=P,
                         text=[f"{v:.2f}%" for v in y10], textposition="outside"))
    if any(y2):
        fig.add_trace(go.Bar(x=[v or 0 for v in y2], y=lbls, orientation="h", name="3M",
                             marker_color=B, opacity=0.65,
                             text=[f"{v:.2f}%" if v else "" for v in y2], textposition="outside"))
    fig.update_layout(
        barmode="overlay", height=180, margin=dict(l=0, r=55, t=4, b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(automargin=True), legend=dict(orientation="h", y=-0.12),
    )
    return fig

def chart_treemap(sector_prices: dict, title: str) -> Optional[go.Figure]:
    names, vals, pcts, texts = [], [], [], []
    for name, d in sector_prices.items():
        if d.get("pct") is not None:
            v = round(d["pct"], 2)
            names.append(name); vals.append(abs(v) + 0.05); pcts.append(v)
            texts.append(f"{'+'if v >= 0 else ''}{v:.2f}%")
    if not names:
        return None
    fig = go.Figure(go.Treemap(
        labels=names, parents=[""] * len(names), values=vals, text=texts,
        marker=dict(colors=pcts, colorscale=[[0, R], [.5, BG], [1, G]],
                    cmid=0, line=dict(width=1.5, color=BG)),
        textinfo="label+text",
    ))
    fig.update_layout(
        title=dict(text=title, font={"color": MUT, "size": 11}, x=0),
        height=265, margin=dict(l=0, r=0, t=28, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig

# ─── UI HELPERS ───────────────────────────────────────────────────────────────
def hd(v):         return v is not None and v != ""
def fmt_num(v):    return f"{v:,.2f}" if v else "—"
def pct_str(v):    return f"{'+'if v >= 0 else ''}{v:.2f}%" if v is not None else "—"
def sent_color(s): return G if str(s).lower() == "bullish" else R if str(s).lower() == "bearish" else A
def sig_color(s):  return G if str(s).lower() == "buy"     else R if str(s).lower() == "sell"    else A
def dir_color(d):  return G if str(d).lower() == "long"    else R
def urg_color(u):  return R if str(u).lower() == "high" else O if str(u).lower() == "medium" else MUT

def section_title(n, title, sub=""):
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:10px;margin:1.6rem 0 .8rem 0;'
        f'padding-bottom:8px;border-bottom:1px solid {BD};">'
        f'<span style="font-size:10px;color:{MUT};">{n}</span>'
        f'<span style="font-size:1.5rem;font-weight:800;color:{INK};">{title}</span>'
        f'<div style="flex:1;height:1px;background:{BD};"></div>'
        f'<span style="font-size:9px;color:{MUT};">{sub}</span></div>',
        unsafe_allow_html=True,
    )

def color_bar(col):
    st.markdown(f'<div style="height:3px;background:{col};border-radius:2px;margin-bottom:8px;"></div>',
                unsafe_allow_html=True)

def mini_badge(txt, col):
    st.markdown(
        f'<span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;'
        f'background:{col}22;color:{col};border:1px solid {col}44;">{txt}</span>',
        unsafe_allow_html=True,
    )

def order_label(txt):
    st.markdown(
        f'<span class="order-tag">{txt}</span>',
        unsafe_allow_html=True,
    )

# ─── RENDER: HERO ─────────────────────────────────────────────────────────────
def render_hero(prices, analysis):
    mood = analysis.get("mood", {}) if analysis else {}
    c1, c2, c3 = st.columns([1.15, 1.55, 1.3])

    with c1:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};text-transform:uppercase;margin:0;">Market Mood</p>',
                        unsafe_allow_html=True)
            st.markdown(chart_gauge(int(mood.get("score", 50))), unsafe_allow_html=True)
            st.markdown(f'<div style="text-align:center;margin:4px 0;font-weight:700;color:{INK};">'
                        f'{mood.get("label","—")}</div>', unsafe_allow_html=True)
            if hd(mood.get("regime")):
                st.markdown(f'<p style="text-align:center;font-size:10px;color:{MUT};">'
                            f'{mood["regime"]}</p>', unsafe_allow_html=True)
            if hd(mood.get("summary")):
                st.caption(mood["summary"])
            st.divider()
            sc1, sc2 = st.columns(2)
            with sc1:
                st.metric("VIX",       fmt_num(prices.get("VIX",{}).get("price")),
                          delta=pct_str(prices.get("VIX",{}).get("pct")),    delta_color="inverse")
                st.metric("AED/INR",   fmt_num(prices.get("AEDIINR",{}).get("price")),
                          delta=pct_str(prices.get("AEDIINR",{}).get("pct")))
            with sc2:
                st.metric("DXY",       fmt_num(prices.get("DXY",{}).get("price")),
                          delta=pct_str(prices.get("DXY",{}).get("pct")),    delta_color="inverse")
                st.metric("Gold/g AED",fmt_num(prices.get("GoldAED",{}).get("price")),
                          delta=pct_str(prices.get("GoldAED",{}).get("pct")))

    with c2:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};">Asset Performance</p>',
                        unsafe_allow_html=True)
            st.plotly_chart(chart_asset_bars(prices), config={"displayModeBar": False})

    with c3:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};">Sovereign Yields</p>',
                        unsafe_allow_html=True)
            st.plotly_chart(chart_yields(prices), config={"displayModeBar": False})
            if hd(mood.get("primary_risk")):
                st.warning(f"⚠ **Risk:** {mood['primary_risk']}")
            if hd(mood.get("tail_risk")):
                st.error(f"☣ **Tail:** {mood['tail_risk']}")
            if hd(mood.get("contrarian_setup")):
                st.info(f"↩ **Contrarian:** {mood['contrarian_setup']}")

# ─── RENDER: NEWS BRIEF ───────────────────────────────────────────────────────
def render_news_brief(news_text: str):
    section_title("00", "Daily Intelligence Brief", "Google Search · Last 24-48 hrs · Live Verified")
    with st.expander("📰 Full News Brief — expand to read", expanded=True):
        st.markdown(f'<div style="font-size:13px;line-height:1.75;color:{BODY};">', unsafe_allow_html=True)
        st.markdown(news_text)
        st.markdown("</div>", unsafe_allow_html=True)

# ─── RENDER: YIELD CURVE ANALYSIS ────────────────────────────────────────────
def render_yield_analysis(analysis):
    yc = analysis.get("yield_curve", {})
    if not yc:
        return
    c1, c2, c3 = st.columns(3)
    items = [
        ("🇺🇸 US Yield Curve", yc.get("us_spread",""), B),
        ("🌐 Global Divergence", yc.get("global_divergence",""), P),
        ("📊 Credit Signal",    yc.get("credit_message",""),  A),
    ]
    for col, (title, text, accent) in zip([c1, c2, c3], items):
        with col:
            with st.container(border=True):
                color_bar(accent)
                st.markdown(f'<b style="font-size:11px;color:{INK};">{title}</b>',
                            unsafe_allow_html=True)
                st.caption(text)

# ─── RENDER: MACRO ────────────────────────────────────────────────────────────
def render_macro(analysis):
    items = analysis.get("macro", [])
    if not items:
        return
    section_title("01", "Global Macro — Deep Dive", "FRED + News Brief + Gemini 2.5 Flash")
    render_yield_analysis(analysis)
    cols = st.columns(len(items))
    for i, m in enumerate(items):
        sc = sent_color(m.get("sentiment", ""))
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                st.markdown(f'<b style="font-size:14px;">{m.get("flag","")} {m.get("country","")}</b>',
                            unsafe_allow_html=True)
                mini_badge(m.get("sentiment","neutral").upper(), sc)
                st.markdown(f"**{m.get('headline','')}**")

                # Key signal (always visible)
                if hd(m.get("key_signal")):
                    st.markdown(
                        f'<p style="font-size:10px;color:{B};margin-top:4px;">'
                        f'📡 {m["key_signal"]}</p>', unsafe_allow_html=True)

                # Tabbed depth
                tab1, tab2, tab3 = st.tabs(["Analysis", "2nd · 3rd Order", "CB Note"])

                with tab1:
                    st.markdown(f'<p style="font-size:12px;color:{BODY};line-height:1.7;">'
                                f'{m.get("analysis","")}</p>', unsafe_allow_html=True)
                    if hd(m.get("data_dependency")):
                        st.markdown(
                            f'<p style="font-size:10px;color:{A};">📅 Watch: {m["data_dependency"]}</p>',
                            unsafe_allow_html=True)

                with tab2:
                    if hd(m.get("second_order")):
                        order_label("2ND ORDER")
                        st.markdown(f'<p style="font-size:12px;color:{BODY};line-height:1.65;">'
                                    f'{m["second_order"]}</p>', unsafe_allow_html=True)
                    if hd(m.get("third_order")):
                        order_label("3RD ORDER")
                        st.markdown(f'<p style="font-size:12px;color:{BODY};line-height:1.65;">'
                                    f'{m["third_order"]}</p>', unsafe_allow_html=True)
                    if hd(m.get("contrarian")):
                        order_label("CONTRARIAN")
                        st.markdown(f'<p style="font-size:12px;color:{A};line-height:1.65;">'
                                    f'{m["contrarian"]}</p>', unsafe_allow_html=True)

                with tab3:
                    if hd(m.get("cb_note")):
                        st.info(f"🏦 {m['cb_note']}")

# ─── RENDER: COMMODITIES ──────────────────────────────────────────────────────
def render_commodities(prices, analysis):
    comms_ai = {c["name"]: c for c in analysis.get("commodities", [])}
    SYM_MAP = [
        ("Gold",   "Gold",        "XAU"),
        ("Crude",  "Crude Oil",   "WTI"),
        ("Silver", "Silver",      "XAG"),
        ("Copper", "Copper",      "HG"),
        ("NatGas", "Natural Gas", "NG"),
    ]
    section_title("02", "Commodities", "Live Prices · Technical + Macro Analysis")
    cols = st.columns(5)
    for i, (pkey, name, _) in enumerate(SYM_MAP):
        pd_ = prices.get(pkey, {})
        ai  = comms_ai.get(name, {})
        sc  = sig_color(ai.get("signal", ""))
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                st.markdown(f'<b>{name}</b>', unsafe_allow_html=True)
                if ai.get("signal"):
                    mini_badge(ai.get("signal","").upper(), sc)
                if pd_.get("price"):
                    st.metric("", value=fmt_num(pd_["price"]),
                              delta=pct_str(pd_.get("pct")))

                tab1, tab2 = st.tabs(["Thesis", "2nd Order"])
                with tab1:
                    if hd(ai.get("analysis")):
                        st.markdown(f'<p style="font-size:11px;color:{BODY};line-height:1.65;">'
                                    f'{ai["analysis"]}</p>', unsafe_allow_html=True)
                    if hd(ai.get("support")) and hd(ai.get("resistance")):
                        s_val = ai.get("support", 0)
                        r_val = ai.get("resistance", 0)
                        if s_val and r_val:
                            st.markdown(
                                f'<p style="font-size:10px;">'
                                f'<span style="color:{G};">S: {fmt_num(s_val)}</span>&nbsp;&nbsp;'
                                f'<span style="color:{R};">R: {fmt_num(r_val)}</span></p>',
                                unsafe_allow_html=True)
                    if hd(ai.get("catalyst")):
                        st.markdown(f'<p style="font-size:10px;color:{A};">⚡ {ai["catalyst"]}</p>',
                                    unsafe_allow_html=True)
                with tab2:
                    if hd(ai.get("second_order")):
                        order_label("2ND ORDER")
                        st.caption(ai["second_order"])
                    if hd(ai.get("positioning_note")):
                        order_label("POSITIONING")
                        st.caption(ai["positioning_note"])
                    if hd(ai.get("risk_scenario")):
                        st.markdown(f'<p style="font-size:10px;color:{R};">⛔ {ai["risk_scenario"]}</p>',
                                    unsafe_allow_html=True)

# ─── RENDER: PICKS ────────────────────────────────────────────────────────────
def render_picks(analysis):
    picks = analysis.get("picks", [])
    if not picks:
        return
    section_title("03", "Trade Ideas", "Catalyst-Driven · Conviction Picks · AI-Generated")
    cols = st.columns(len(picks))
    for i, p in enumerate(picks):
        dc = dir_color(p.get("direction", ""))
        cv_col = G if p.get("conviction","") == "high" else A
        with cols[i]:
            with st.container(border=True):
                color_bar(dc)
                # Header row
                hc1, hc2 = st.columns([2, 1])
                with hc1:
                    st.markdown(f'<b style="font-size:16px;color:{INK};">{p.get("name","")}</b>',
                                unsafe_allow_html=True)
                    st.markdown(f'<span style="font-size:10px;color:{MUT};">'
                                f'{p.get("type","")} · {p.get("region","")}</span>',
                                unsafe_allow_html=True)
                with hc2:
                    mini_badge(p.get("direction","").upper(), dc)
                    mini_badge(p.get("conviction","").upper(), cv_col)

                st.markdown(f"**{p.get('headline','')}**")

                tab1, tab2 = st.tabs(["Thesis", "Levels"])
                with tab1:
                    st.markdown(f'<p style="font-size:12px;color:{BODY};line-height:1.7;">'
                                f'{p.get("thesis","")}</p>', unsafe_allow_html=True)
                    if hd(p.get("entry_note")):
                        st.markdown(f'<p style="font-size:10px;color:{B};">↗ Entry: {p["entry_note"]}</p>',
                                    unsafe_allow_html=True)
                with tab2:
                    mc1, mc2, mc3 = st.columns(3)
                    with mc1:
                        st.markdown(f'<p style="font-size:9px;color:{MUT};">TARGET</p>'
                                    f'<p style="font-size:12px;color:{G};font-weight:700;">'
                                    f'{p.get("target","—")}</p>', unsafe_allow_html=True)
                    with mc2:
                        st.markdown(f'<p style="font-size:9px;color:{MUT};">R:R</p>'
                                    f'<p style="font-size:12px;color:{A};font-weight:700;">'
                                    f'{p.get("risk_reward","—")}</p>', unsafe_allow_html=True)
                    with mc3:
                        st.markdown(f'<p style="font-size:9px;color:{MUT};">HORIZON</p>'
                                    f'<p style="font-size:12px;color:{B};font-weight:700;">'
                                    f'{p.get("timeframe","—")}</p>', unsafe_allow_html=True)
                    if hd(p.get("risk")):
                        st.markdown(f'<p style="font-size:10px;color:{R};">⛔ Stop: {p["risk"]}</p>',
                                    unsafe_allow_html=True)

# ─── RENDER: GEO / RADAR ──────────────────────────────────────────────────────
def render_geo(analysis):
    geo = analysis.get("geo", [])
    if not geo:
        return
    section_title("04", "Hedge Fund Radar", "Geopolitical & Structural Macro Themes")
    cols = st.columns(min(len(geo), 4))
    for i, g in enumerate(geo[:4]):
        uc = urg_color(g.get("urgency",""))
        with cols[i]:
            with st.container(border=True):
                color_bar(uc)
                # Category + urgency
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="font-size:11px;font-weight:700;color:{MUT};">'
                    f'{g.get("icon","")} {g.get("category","")}</span>'
                    f'<span style="font-size:9px;font-weight:700;padding:2px 6px;border-radius:3px;'
                    f'background:{uc}22;color:{uc};">{g.get("urgency","").upper()}</span></div>',
                    unsafe_allow_html=True)
                st.markdown(f"**{g.get('headline','')}**")

                tab1, tab2 = st.tabs(["Analysis", "Hedge"])
                with tab1:
                    st.markdown(f'<p style="font-size:12px;color:{BODY};line-height:1.7;">'
                                f'{g.get("analysis","")}</p>', unsafe_allow_html=True)
                    if hd(g.get("assets_affected")):
                        st.markdown(f'<p style="font-size:10px;color:{B};">🎯 {g["assets_affected"]}</p>',
                                    unsafe_allow_html=True)
                    if hd(g.get("time_horizon")):
                        st.markdown(f'<p style="font-size:10px;color:{MUT};">⏳ {g["time_horizon"]}</p>',
                                    unsafe_allow_html=True)
                with tab2:
                    if hd(g.get("hedge")):
                        st.info(f"🛡 {g['hedge']}")

# ─── RENDER: SECTOR TREEMAPS ──────────────────────────────────────────────────
def render_sector_maps(prices: dict):
    US_SECTORS = {
        "Technology": prices.get("secTech",{}), "Financials":  prices.get("secFin",{}),
        "Healthcare":  prices.get("secHlth",{}),"Energy":      prices.get("secEnrg",{}),
        "Materials":   prices.get("secMatl",{}),"Industrials": prices.get("secInds",{}),
        "Cons Discr":  prices.get("secCyc",{}), "Cons Staples":prices.get("secStpl",{}),
        "Utilities":   prices.get("secUtil",{}),"Real Estate": prices.get("secRE",{}),
        "Comm Svcs":   prices.get("secComm",{}),
    }
    IN_SECTORS = {
        "IT":       prices.get("inSIT",{}),  "Bank":   prices.get("inSBnk",{}),
        "Pharma":   prices.get("inSPhrm",{}),"Auto":   prices.get("inSAut",{}),
        "FMCG":     prices.get("inSFmcg",{}),"Metal":  prices.get("inSMtl",{}),
        "Energy":   prices.get("inSEnrg",{}),"Realty": prices.get("inSRlt",{}),
        "Infra":    prices.get("inSInf",{}), "Media":  prices.get("inSMed",{}),
        "PSU Bank": prices.get("inSPSU",{}),
    }
    section_title("05", "Sector Heat Maps", "Real-time via yfinance")
    c1, c2 = st.columns(2)
    with c1:
        fig = chart_treemap(US_SECTORS, "🇺🇸 US Sectors (SPDR ETFs)")
        if fig:
            st.plotly_chart(fig, config={"displayModeBar": False}, use_container_width=True)
    with c2:
        fig = chart_treemap(IN_SECTORS, "🇮🇳 India Sectors (NSE Indices)")
        if fig:
            st.plotly_chart(fig, config={"displayModeBar": False}, use_container_width=True)

# ─── SETUP SCREEN ─────────────────────────────────────────────────────────────
def render_setup():
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.info(
        "**AlphaTerminal Setup**\n\n"
        "Add the following to your Streamlit Secrets (Settings → Secrets):\n\n"
        "```toml\n"
        "GEMINI_API_KEY = \"your-gemini-api-key\"\n"
        "FRED_API_KEY   = \"your-fred-api-key\"\n"
        "```\n\n"
        "Get your FRED key free at: https://fredaccount.stlouisfed.org/login/secure/"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    gemini_key = st.secrets.get("GEMINI_API_KEY", "").strip()
    fred_key   = st.secrets.get("FRED_API_KEY",   "").strip()

    today_str = str(date.today())   # e.g. "2025-05-12" — forces new AI cache each day

    # ── Header ────────────────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns([5, 1.1, 1.1, 1.8])
    with h1:
        st.markdown(
            f'<div style="font-size:20px;font-weight:900;color:{INK};">AlphaTerminal'
            f'<span style="font-size:10px;color:{MUT};margin-left:10px;font-weight:400;">'
            f'v11 · Institutional Macro</span></div>',
            unsafe_allow_html=True)
    with h2:
        if st.button("↺ Data", use_container_width=True, help="Refresh live prices & FRED data"):
            fetch_prices.clear()
            fetch_fred_macro.clear()
            fetch_commodity_ohlc.clear()
            st.rerun()
    with h3:
        if st.button("⚡ AI Sync", use_container_width=True,
                     help="Force-regenerate today's AI analysis (clears daily cache)"):
            fetch_ai_news_brief.clear()
            run_macro_analysis.clear()
            run_markets_analysis.clear()
            st.rerun()
    with h4:
        # Show when data was last fetched
        st.markdown(
            f'<div style="text-align:right;font-size:9px;color:{MUT};padding-top:6px;">'
            f'Analysis date: <b style="color:{G};">{today_str}</b></div>',
            unsafe_allow_html=True)

    st.divider()

    if not gemini_key or not fred_key:
        render_setup()
        return

    # ── Stage 0: Fetch live data ───────────────────────────────────────────────
    with st.spinner("Fetching live prices & FRED macro data..."):
        prices = fetch_prices()
        macro  = fetch_fred_macro(fred_key)
        ohlc   = fetch_commodity_ohlc()

    fetched_at = prices.get("_fetched", "—")
    st.markdown(
        f'<p style="font-size:9px;color:{MUT};text-align:right;margin-top:-6px;">'
        f'Last data pull: {fetched_at}</p>',
        unsafe_allow_html=True)

    # ── Stage 1: News Brief (once per day, Google Search grounded) ────────────
    news_brief = ""
    with st.spinner("📡 Stage 1/3 — Aggregating live macro news via Google Search..."):
        news_brief = fetch_ai_news_brief(gemini_key, today_str)

    # ── Stage 2: Macro Analysis ───────────────────────────────────────────────
    macro_result = {}
    slim  = slim_prices_for_ai(prices)
    slim_json  = json.dumps(slim,  separators=(",", ":"))
    macro_json = json.dumps(macro, separators=(",", ":"))
    ohlc_json  = json.dumps(ohlc,  separators=(",", ":"))

    with st.spinner("🧠 Stage 2/3 — Running deep macro analysis (2nd + 3rd order)..."):
        macro_result = run_macro_analysis(
            gemini_key, slim_json, macro_json, news_brief, today_str)

    # ── Stage 3: Markets Analysis ─────────────────────────────────────────────
    markets_result = {}
    with st.spinner("📊 Stage 3/3 — Generating commodities, picks & geo radar..."):
        markets_result = run_markets_analysis(
            gemini_key, slim_json, ohlc_json, news_brief, today_str)

    # ── Merge full analysis ───────────────────────────────────────────────────
    analysis = {**macro_result, **markets_result}

    # ── Render sections ───────────────────────────────────────────────────────
    if not analysis:
        st.error("AI analysis failed for all stages. Click **⚡ AI Sync** to retry.")
        return

    render_hero(prices, analysis)
    render_news_brief(news_brief)
    render_macro(analysis)
    render_commodities(prices, analysis)
    render_picks(analysis)
    render_geo(analysis)
    render_sector_maps(prices)

    # ── Footer ────────────────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        f'<p style="text-align:center;font-size:9px;color:{MUT};">'
        f'AlphaTerminal v11 · Prices: yfinance · Rates/CPI: FRED · '
        f'News + Analysis: Gemini 2.0/2.5 Flash + Google Search · '
        f'For informational purposes only — not financial advice.</p>',
        unsafe_allow_html=True)


if __name__ == "__main__":
    main()
