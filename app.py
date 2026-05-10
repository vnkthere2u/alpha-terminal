"""
AlphaTerminal — Institutional Macro Dashboard  v7 (Live Macro + News)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prices       → yfinance (15‑min cache)
CB rates/CPI → Trading Economics web scrape (latest)
Unemployment, GDP → Trading Economics web scrape
News         → NewsAPI (free tier)
Analysis     → Groq (Llama 3.3 70B) with full live context, JSON mode
UI           → 100% native Streamlit
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
from typing import Optional, List, Dict
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

# Minimal fallback – used only if scraping totally fails
CB_SCRAPE_FALLBACK = {
    "US":    dict(bank="Fed", flag="🇺🇸", rate=4.50, rp=4.25, rd="Mar 2026", cpi=3.0, cpip=3.2, cpid="Apr 2026", gdp=2.4, une=3.9, stance="hold", next="Jun 2026"),
    "India": dict(bank="RBI", flag="🇮🇳", rate=6.00, rp=6.25, rd="Apr 2026", cpi=4.6, cpip=4.9, cpid="Mar 2026", gdp=6.2, une=3.4, stance="cut", next="Jun 2026"),
    "UK":    dict(bank="BOE", flag="🇬🇧", rate=4.50, rp=4.75, rd="Feb 2026", cpi=2.8, cpip=3.0, cpid="Mar 2026", gdp=0.2, une=4.4, stance="cut", next="May 2026"),
    "Euro":  dict(bank="ECB", flag="🇪🇺", rate=2.65, rp=2.90, rd="Mar 2026", cpi=2.4, cpip=2.5, cpid="Mar 2026", gdp=0.3, une=6.1, stance="cut", next="Jun 2026"),
    "Japan": dict(bank="BOJ", flag="🇯🇵", rate=0.75, rp=0.50, rd="Jan 2026", cpi=3.3, cpip=3.5, cpid="Mar 2026", gdp=-1.0, une=2.5, stance="hike", next="Jun 2026"),
    "China": dict(bank="PBOC", flag="🇨🇳", rate=3.00, rp=3.10, rd="Dec 2025", cpi=0.2, cpip=0.3, cpid="Mar 2026", gdp=5.2, une=5.2, stance="cut", next="—"),
}

TE_MAP = {
    "United States": "US", "India": "India",
    "United Kingdom": "UK", "Euro Area": "Euro",
    "Japan": "Japan", "China": "China",
}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# ─── SYSTEM PROMPT (institutional mandate) ───────────────────────────────────
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

YOU RECEIVE THE FOLLOWING LIVE DATA IN YOUR CONTEXT:
- Central bank policy rates, last change dates, CPI, GDP, unemployment
- VIX, DXY, major FX pairs, Bitcoin
- 10Y/3M yield curve for US, Germany, Japan
- Equity index returns and sector heatmaps (US, India)
- Commodity latest price + 5-day OHLC (Gold, Silver, Crude, Copper, NatGas)
- Top 10 global business/world news headlines

CRITICAL RULES:
- DO NOT restate the numbers. Instead, INTERPRET them: what do they signal collectively?
- Every macro opinion must be backed by at least one specific data point or headline from the provided context.
- For support/resistance levels on commodities, derive them from the 5-day high/low/close — never invent numbers.
- Trade ideas must include a concrete catalyst from the news or an upcoming data release.
- Contrarian notes must identify a consensus that is contradicted by the data you see.

DO NOT: Use phrases like "markets are watching" or "investors are cautious." State the obvious.
DO: Provide the insight that separates a $30B PM from a Bloomberg terminal."""

# ─────────────────────────────────────────────────────────────────────────────
# YFINANCE PRICE FETCHING (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
def _yf_ticker(sym: str) -> dict:
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
    syms = {
        "^GSPC": "SP500", "^IXIC": "Nasdaq", "^DJI": "Dow",
        "^NSEI": "Nifty50","^BSESN": "Sensex","^N225": "Nikkei",
        "^HSI": "HangSeng","^GDAXI": "DAX",
        "GC=F": "Gold", "CL=F": "Crude", "SI=F": "Silver",
        "HG=F": "Copper","NG=F": "NatGas",
        "DX-Y.NYB": "DXY", "^VIX": "VIX",
        "BTC-USD": "Bitcoin",
        "USDINR=X": "USDINR", "AEDIINR=X": "AEDIINR",
        "^TNX": "US10Y", "^IRX": "US3M",
        "^GDBR10": "DE10Y", "^JRGB": "JP10Y",
        "XLK": "secTech", "XLF": "secFin", "XLV": "secHlth",
        "XLE": "secEnrg", "XLB": "secMatl", "XLI": "secInds",
        "XLY": "secCyc", "XLP": "secStpl", "XLU": "secUtil",
        "XLRE":"secRE", "XLC": "secComm",
        "^CNXIT": "inSIT", "^NSEBANK": "inSBnk","^CNXPHARMA":"inSPhrm",
        "^CNXAUTO": "inSAut", "^CNXFMCG": "inSFmcg","^CNXMETAL": "inSMtl",
        "^CNXENERGY":"inSEnrg","^CNXREALTY":"inSRlt","^CNXINFRA": "inSInf",
        "^CNXMEDIA": "inSMed", "^CNXPSUBANK":"inSPSU",
    }
    results = {}
    for sym, key in syms.items():
        d = _yf_ticker(sym)
        if d: results[key] = d
    if "AEDIINR" not in results and "USDINR" in results:
        usd_inr = results["USDINR"]["price"]
        results["AEDIINR"] = {
            "price": usd_inr / AED_PEG,
            "prev": results["USDINR"].get("prev", usd_inr) / AED_PEG,
            "pct": results["USDINR"].get("pct"),
            "date": results["USDINR"].get("date",""),
            "source": "calc"
        }
    if "Gold" in results:
        gp = results["Gold"]["price"]
        pp = results["Gold"].get("prev", gp)
        results["GoldAED"] = {
            "price": gp / TROY_OZ * AED_PEG,
            "prev": pp / TROY_OZ * AED_PEG,
            "pct": results["Gold"].get("pct"),
            "source": "calc"
        }
    results["_fetched"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y  %H:%M %Z")
    return results

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_gold_aed_gulfnews():
    try:
        resp = requests.get(
            "https://gulfnews.com/gold-forex",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=8)
        if resp.status_code != 200: return None
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        for pat in [r'24\s*[Cc]arat[^\d]*(\d{2,3}\.\d{1,2})', r'24K[^\d]*(\d{2,3}\.\d{1,2})', r'(\d{2,3}\.\d{2})\s*AED.*24']:
            m = re.search(pat, text)
            if m:
                val = float(m.group(1))
                if 200 < val < 600:
                    return {"price": val, "source": "Gulf News"}
    except: pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# LIVE TRADING ECONOMICS SCRAPING (rates, cpi, gdp, unemployment)
# ─────────────────────────────────────────────────────────────────────────────
def _scrape_te_table(indicator_slug: str) -> list:
    url = f"https://tradingeconomics.com/country-list/{indicator_slug}"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200: return []
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="table table-hover")
        if not table: return []
        rows = table.find_all("tr")[1:]
        data = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4: continue
            country = cols[0].get_text(strip=True)
            last = cols[1].get_text(strip=True)
            prev = cols[2].get_text(strip=True) if len(cols) > 2 else ""
            ref_date = cols[3].get_text(strip=True) if len(cols) > 3 else ""
            data.append({"country": country, "last": last, "previous": prev, "date": ref_date})
        return data
    except: return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_live_macro() -> dict:
    """Scrape interest rate, inflation, gdp, unemployment from TE and return enriched CB dict."""
    result = {k: dict(v) for k, v in CB_SCRAPE_FALLBACK.items()}  # start with fallback

    # interest rate
    for item in _scrape_te_table("interest-rate"):
        key = TE_MAP.get(item["country"])
        if not key: continue
        try: result[key]["rate"] = float(item["last"].replace('%',''))
        except: pass
        try: result[key]["rp"] = float(item["previous"].replace('%',''))
        except: pass
        if item["date"]: result[key]["rd"] = item["date"]

    # inflation
    for item in _scrape_te_table("inflation-rate"):
        key = TE_MAP.get(item["country"])
        if not key: continue
        try: result[key]["cpi"] = float(item["last"].replace('%',''))
        except: pass
        try: result[key]["cpip"] = float(item["previous"].replace('%',''))
        except: pass
        if item["date"]: result[key]["cpid"] = item["date"]

    # gdp growth
    for item in _scrape_te_table("gdp-growth"):
        key = TE_MAP.get(item["country"])
        if not key: continue
        try: result[key]["gdp"] = float(item["last"].replace('%',''))
        except: pass
        # date optional

    # unemployment
    for item in _scrape_te_table("unemployment-rate"):
        key = TE_MAP.get(item["country"])
        if not key: continue
        try: result[key]["une"] = float(item["last"].replace('%',''))
        except: pass

    return result

# ─────────────────────────────────────────────────────────────────────────────
# NEWS HEADLINES (NewsAPI)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news_headlines(api_key: str, num: int = 10) -> List[str]:
    if not api_key: return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"apiKey": api_key, "language": "en", "pageSize": num, "category": "business"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200: return []
        articles = resp.json().get("articles", [])
        return [a["title"] for a in articles if a.get("title")][:num]
    except: return []

# ─────────────────────────────────────────────────────────────────────────────
# COMMODITY OHLC (5-day)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def fetch_commodity_ohlc() -> dict:
    symbols = {
        "Gold": "GC=F", "Crude Oil": "CL=F", "Silver": "SI=F",
        "Copper": "HG=F", "Natural Gas": "NG=F",
    }
    ohlc = {}
    for name, sym in symbols.items():
        ticker = yf.Ticker(sym)
        hist = ticker.history(period="5d")
        if hist.empty: continue
        df = hist[["Open","High","Low","Close"]].tail(5)
        ohlc[name] = df.reset_index().to_dict(orient="records")
        for rec in ohlc[name]:
            rec["Date"] = str(rec["Date"].date())
    return ohlc

# ─────────────────────────────────────────────────────────────────────────────
# BUILD AI CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
def _build_context(prices: dict, cb: dict) -> str:
    def p(key, d=2): v = prices.get(key, {}).get("price"); return round(v, d) if v else None
    def chg(key): v = prices.get(key, {}).get("pct"); return round(v, 2) if v else None

    ctx = {
        "date": TODAY,
        "indices": {
            "SP500": {"price": p("SP500",0), "chg_pct": chg("SP500")},
            "Nasdaq": {"price": p("Nasdaq",0), "chg_pct": chg("Nasdaq")},
            "Dow": {"price": p("Dow",0), "chg_pct": chg("Dow")},
            "Nifty50": {"price": p("Nifty50",0), "chg_pct": chg("Nifty50")},
            "Nikkei": {"price": p("Nikkei",0), "chg_pct": chg("Nikkei")},
            "HangSeng": {"price": p("HangSeng",0), "chg_pct": chg("HangSeng")},
            "DAX": {"price": p("DAX",0), "chg_pct": chg("DAX")},
        },
        "commodities_latest": {
            "Gold": p("Gold"), "Crude": p("Crude"), "Silver": p("Silver",3),
            "Copper": p("Copper",3), "NatGas": p("NatGas",3),
            "Gold_AED_gram": p("GoldAED"),
        },
        "fx_vix": {
            "DXY": p("DXY"), "VIX": p("VIX"), "USDINR": p("USDINR"),
            "AEDIINR": p("AEDIINR",4), "Bitcoin": p("Bitcoin",0),
        },
        "yields": {
            "US_10Y": p("US10Y"), "US_3M": p("US3M"),
            "DE_10Y": p("DE10Y"), "JP_10Y": p("JP10Y"),
            "US_curve_3M10Y": round(p("US10Y") - p("US3M"), 2) if p("US10Y") and p("US3M") else None,
        },
        "us_sectors": {
            "Tech": chg("secTech"), "Financials": chg("secFin"), "Health": chg("secHlth"),
            "Energy": chg("secEnrg"), "Materials": chg("secMatl"), "Industrials": chg("secInds"),
            "ConsDisc": chg("secCyc"), "ConsStap": chg("secStpl"), "Utilities": chg("secUtil"),
            "RealEstate": chg("secRE"), "CommSvcs": chg("secComm"),
        },
        "india_sectors": {
            "IT": chg("inSIT"), "Bank": chg("inSBnk"), "Pharma": chg("inSPhrm"),
            "Auto": chg("inSAut"), "FMCG": chg("inSFmcg"), "Metal": chg("inSMtl"),
            "Energy": chg("inSEnrg"), "Realty": chg("inSRlt"), "Infra": chg("inSInf"),
            "Media": chg("inSMed"), "PSU": chg("inSPSU"),
        },
        "macro_fundamentals": {
            k: {
                "rate": v["rate"], "prev_rate": v["rp"], "rate_date": v["rd"],
                "cpi": v["cpi"], "prev_cpi": v["cpip"], "cpi_date": v["cpid"],
                "gdp_growth": v.get("gdp"), "unemployment": v.get("une"),
                "stance": v["stance"], "next_meeting": v.get("next","—"),
            } for k, v in cb.items()
        },
    }
    return json.dumps(ctx, default=str)

# ─────────────────────────────────────────────────────────────────────────────
# AI ANALYSIS (Groq + Live Context)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_analysis(groq_key: str, news_key: str, today: str) -> dict:
    if not groq_key:
        raise RuntimeError("Groq API key missing.")
    prices = fetch_prices()
    cb     = fetch_live_macro()
    ohlc   = fetch_commodity_ohlc()
    headlines = fetch_news_headlines(news_key)
    ctx    = _build_context(prices, cb)

    ohlc_str = json.dumps(ohlc, default=str)
    news_str = "\n".join(f"- {h}" for h in headlines) if headlines else "No headlines"

    user_prompt = f"""Today is {today}.

LIVE MACRO DATA:
{ctx}

COMMODITY 5-DAY OHLC:
{ohlc_str}

MAJOR HEADLINES:
{news_str}

Provide an institutional-grade morning briefing in the following JSON structure.
CRITICAL: Do NOT restate numbers. Interpret them. Every insight must be tied to a specific value, spread, or headline.
Return ONLY JSON (no other text).

{{
  "mood": {{
    "score": <0-100>,
    "label": "<Risk-On|Cautious|Risk-Off|Volatile>",
    "regime": "<2-3 word regime>",
    "summary": "<3 sentences: regime, historical parallel, primary cross-asset tension>",
    "primary_risk": "<top tail risk>"
  }},
  "macro": [
    {{
      "country": "US", "flag": "🇺🇸",
      "headline": "<sharp, non-obvious>",
      "analysis": "<4-5 sentences: second-order effects, institutional moves, historical parallel>",
      "sentiment": "<bullish|bearish|neutral>",
      "key_signal": "<single most important metric>",
      "contrarian": "<1-2 sentences: what consensus is wrong about>",
      "cb_note": "<CB insight, next move probability>"
    }},
    {{"country":"India","flag":"🇮🇳","headline":"",...}},
    {{"country":"China","flag":"🇨🇳","headline":"",...}},
    {{"country":"Japan","flag":"🇯🇵","headline":"",...}},
    {{"country":"Eurozone","flag":"🇪🇺","headline":"",...}}
  ],
  "commodities": [
    {{
      "name": "Gold", "signal": "<buy|sell|hold|watch>",
      "support": "<from OHLC>", "resistance": "<from OHLC>",
      "analysis": "<4 sentences: technical + macro driver + cross-asset + catalyst>",
      "positioning_note": "<COT/ETF inference>"
    }},
    {{"name":"Silver","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Crude Oil","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Copper","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}},
    {{"name":"Natural Gas","signal":"","support":"","resistance":"","analysis":"","positioning_note":""}}
  ],
  "picks": [
    {{
      "type": "<stock|sector|etf|commodity>",
      "name": "<ticker>",
      "region": "<US|India|Global|EM>",
      "direction": "<long|short>",
      "conviction": "<high|medium|low>",
      "timeframe": "<2 weeks|1 month|3 months|6 months>",
      "headline": "<punchy thesis>",
      "thesis": "<4-5 sentences: catalyst, entry, risk, return>",
      "risk": "<what invalidates>"
    }}
  ],
  "geo": [
    {{
      "category": "Geopolitics", "icon": "🌍",
      "headline": "<specific>",
      "analysis": "<4 sentences: market impact, hedge funds, key date>",
      "urgency": "<high|medium|low>"
    }},
    {{"category":"Fed Watch","icon":"🏦","headline":"",...}},
    {{"category":"Rates & Bonds","icon":"📈","headline":"",...}},
    {{"category":"Earnings Season","icon":"📊","headline":"",...}},
    {{"category":"Crypto","icon":"₿","headline":"",...}},
    {{"category":"EM Risk","icon":"🌏","headline":"",...}}
  ]
}}"""

    headers = {
        "Authorization": f"Bearer {groq_key}",
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
        "response_format": {"type": "json_object"},
    }

    for attempt in range(2):
        try:
            resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=90)
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(20)
                    continue
                raise RuntimeError("Rate limited.")
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError("Invalid JSON.")
        except Exception as e:
            if attempt == 0 and "429" not in str(e):
                time.sleep(5)
                continue
            raise e
    raise RuntimeError("Analysis failed.")

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY CHARTS, HELPERS, RENDERERS (all identical to previous versions)
# (For brevity, not repeated – you must include your existing chart_asset_bars,
#  chart_gauge, chart_yields, chart_treemap, helpers, and all render_ functions here.)
# ─────────────────────────────────────────────────────────────────────────────
# [ ... paste your chart functions and helpers exactly as before ... ]

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    groq_key = st.secrets.get("GROQ_API_KEY", "").strip()
    news_key = st.secrets.get("NEWSAPI_KEY", "").strip()
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
            f'</div></div>', unsafe_allow_html=True)
    with h2:
        st.markdown(
            f'<div style="padding:10px 0 0;">'
            f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;color:{MUT};">'
            f'{now_uae.strftime("%A, %d %B %Y  %H:%M")} GST · '
            f'Live data: Yahoo Finance, Trading Economics, NewsAPI · AI: Groq'
            f'</span></div>', unsafe_allow_html=True)
    with h3:
        if st.button("↺ Prices", use_container_width=True):
            fetch_prices.clear(); fetch_live_macro.clear(); fetch_gold_aed_gulfnews.clear(); st.rerun()
    with h4:
        if st.button("⚡ Analysis", use_container_width=True):
            fetch_analysis.clear(); st.rerun()

    st.markdown(f'<hr style="border:none;border-top:1px solid {BD};margin:2px 0 10px 0;">', unsafe_allow_html=True)

    if not groq_key:
        render_setup(); return

    with st.spinner("Fetching live data…"):
        prices = fetch_prices()
        cb = fetch_live_macro()
        gn = fetch_gold_aed_gulfnews()
    if gn and gn.get("price") and "GoldAED" in prices:
        prices["GoldAED"]["price_gn"] = gn["price"]
        prices["GoldAED"]["source"] = "Gulf News"

    analysis = None
    with st.spinner("Running AI analysis…"):
        try:
            analysis = fetch_analysis(groq_key, news_key, TODAY)
        except Exception as e:
            st.error(f"❌ Analysis failed: {e}")

    # STATUS BAR, RENDERERS – unchanged (use previous render_hero, etc.)
    # ...

if __name__ == "__main__":
    main()
