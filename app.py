"""
AlphaTerminal — Institutional Macro Dashboard  v9 (Fixed Scraper + Section News)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prices       → yfinance (15‑min cache)
CB rates/CPI → Trading Economics web scrape (fixed 6‑col parser)
Unemployment, GDP → Trading Economics web scrape
News         → NewsAPI (section‑specific queries)
Analysis     → Groq (Llama 3.3 70B) in 4 clean JSON calls
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

# Minimal fallback – only used if scraping totally fails
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

# ─── SYSTEM PROMPTS ─────────────────────────────────────────────────────────
MACRO_SYSTEM = """You are a senior macro strategist. Return ONLY the JSON structure specified. No other text.
Analyze the provided live macro data, indices, yields, FX, VIX, DXY, yield curve, and central bank fundamentals. For every insight, reference a specific data point from the context. Do not restate numbers; interpret them."""

COMMODITIES_SYSTEM = """You are a commodity strategist. Return ONLY the JSON structure specified. No other text.
Using the provided latest prices, 5-day OHLC (High/Low/Close), and recent headlines, derive technical support/resistance from the OHLC range. Tie analysis to macro drivers and specific headlines."""

PICKS_SYSTEM = """You are a portfolio manager. Return ONLY the JSON structure specified. No other text.
Based on the provided sector performances, yield curve, DXY, VIX, central bank stances, and headlines, provide 3-5 actionable trade ideas with concrete catalyst, entry logic, risk, and timeframe. Use tickers where possible."""

GEO_SYSTEM = """You are a geopolitical risk analyst. Return ONLY the JSON structure specified. No other text.
Using the provided headlines and macro context, identify 4-6 specific themes (e.g., Fed Watch, Geopolitics, Earnings, Crypto, EM Risk). Each must include a specific headline, analysis of market impact, and urgency."""

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
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
# TRADING ECONOMICS SCRAPER (FIXED)
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_te_table(indicator_slug: str) -> list:
    """Correctly parse the 6‑column Trading Economics table."""
    url = f"https://tradingeconomics.com/country-list/{indicator_slug}"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="table table-hover") or soup.find("table", class_="table")
        if not table:
            return []
        rows = table.find_all("tr")[1:]
        data = []
        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 6:
                continue
            country = cols[0].get_text(strip=True)
            last    = cols[1].get_text(strip=True)
            prev    = cols[2].get_text(strip=True)
            ref     = cols[3].get_text(strip=True)   # 'Reference' column
            data.append({
                "country": country,
                "last": last,
                "previous": prev,
                "date": ref,
            })
        return data
    except Exception:
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_live_macro() -> dict:
    """Scrape interest rate, inflation, gdp, unemployment from Trading Economics."""
    result = {k: dict(v) for k, v in CB_SCRAPE_FALLBACK.items()}

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

    # unemployment
    for item in _scrape_te_table("unemployment-rate"):
        key = TE_MAP.get(item["country"])
        if not key: continue
        try: result[key]["une"] = float(item["last"].replace('%',''))
        except: pass

    return result

# ─────────────────────────────────────────────────────────────────────────────
# SECTION‑SPECIFIC NEWS FEEDS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_news(api_key: str, query: str, category: str, num: int) -> List[str]:
    """Generic NewsAPI call with a free‑form query."""
    if not api_key:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "apiKey": api_key,
        "q": query,
        "language": "en",
        "pageSize": num,
        "sortBy": "publishedAt",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        articles = resp.json().get("articles", [])
        headlines = [a["title"] for a in articles if a.get("title")]
        return headlines[:num]
    except:
        return []

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news_for_macro(api_key: str) -> List[str]:
    return _fetch_news(api_key, "central bank OR interest rate OR inflation OR GDP", "business", 6)

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news_for_commodities(api_key: str) -> List[str]:
    return _fetch_news(api_key, "oil OR gold OR copper OR commodity", "business", 6)

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news_for_geo(api_key: str) -> List[str]:
    return _fetch_news(api_key, "geopolitic OR conflict OR sanction OR tariff", "general", 6)

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news_for_trades(api_key: str) -> List[str]:
    return _fetch_news(api_key, "stock market OR earnings OR sector", "business", 6)

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
# AI CALLS (split into 4 sections)
# ─────────────────────────────────────────────────────────────────────────────

def _groq_call(system: str, user: str, api_key: str, max_tokens: int = 2048) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(2):
        try:
            resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=90)
            if resp.status_code == 429:
                if attempt == 0:
                    time.sleep(20)
                    continue
                raise RuntimeError("Rate limited (429).")
            resp.raise_for_status()
            data = resp.json()
            return json.loads(data["choices"][0]["message"]["content"])
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(5)
                continue
            raise RuntimeError("Invalid JSON returned.")
        except Exception as e:
            if attempt == 0 and "429" not in str(e):
                time.sleep(5)
                continue
            raise e
    raise RuntimeError("Groq call failed.")

# ─── 1. Mood + Global Macro ─────────────────────────────────────────────────
def _build_mood_macro_context(prices, cb):
    def p(key, d=2): v = prices.get(key, {}).get("price"); return round(v, d) if v else None
    def chg(key): v = prices.get(key, {}).get("pct"); return round(v, 2) if v else None
    ctx = {
        "today": TODAY,
        "indices": {
            "SP500": chg("SP500"), "Nasdaq": chg("Nasdaq"), "Dow": chg("Dow"),
            "Nifty": chg("Nifty50"), "Nikkei": chg("Nikkei"),
        },
        "fx_vol": {"DXY": p("DXY"), "VIX": p("VIX"), "USDINR": p("USDINR")},
        "yields": {
            "US_10Y": p("US10Y"), "US_3M": p("US3M"),
            "spread_3M10Y": round(p("US10Y") - p("US3M"), 2) if p("US10Y") and p("US3M") else None,
            "DE_10Y": p("DE10Y"), "JP_10Y": p("JP10Y"),
        },
        "central_banks": {
            k: {
                "rate": v["rate"], "prev_rate": v["rp"], "rate_date": v["rd"],
                "cpi": v["cpi"], "cpi_date": v["cpid"],
                "gdp": v.get("gdp"), "unemployment": v.get("une"),
                "stance": v["stance"], "next_mtg": v.get("next",""),
            } for k, v in cb.items()
        }
    }
    return json.dumps(ctx)

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_mood_and_macro(groq_key: str, news_key: str) -> dict:
    prices = fetch_prices()
    cb = fetch_live_macro()
    ctx = _build_mood_macro_context(prices, cb)
    headlines = fetch_news_for_macro(news_key)
    prompt = f"""Using the live macro data and central bank headlines, return JSON with:
- "mood": {{score (0-100), label (Risk-On/Cautious/Risk-Off/Volatile), regime (2-3 words), summary (2-3 sentences on regime, historical parallel, primary risk)}}
- "macro": array of 5 objects in exact order: US, India, China, Japan, Eurozone. Each: flag, headline (sharp non-obvious), analysis (4-5 sentences linking data to second-order effects, institutional positioning, historical analogue), sentiment (bullish/bearish/neutral), key_signal, contrarian (1-2 sentences), cb_note.
DATA:{ctx}
HEADLINES:{json.dumps(headlines)}"""
    return _groq_call(MACRO_SYSTEM, prompt, groq_key, max_tokens=3000)

# ─── 2. Commodities ─────────────────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_commodities_analysis(groq_key: str, news_key: str) -> dict:
    ohlc = fetch_commodity_ohlc()
    compact = {}
    for name, records in ohlc.items():
        compact[name] = [{"h": round(r["High"],2), "l": round(r["Low"],2), "c": round(r["Close"],2)} for r in records[-3:]]
    prices = fetch_prices()
    latest = {
        "Gold": round(prices.get("Gold",{}).get("price",0),2),
        "Crude": round(prices.get("Crude",{}).get("price",0),2),
        "Silver": round(prices.get("Silver",{}).get("price",0),3),
        "Copper": round(prices.get("Copper",{}).get("price",0),3),
        "NatGas": round(prices.get("NatGas",{}).get("price",0),3),
    }
    headlines = fetch_news_for_commodities(news_key)
    prompt = f"""COMMODITY OHLC & HEADLINES:
OHLC (3-day High/Low/Close):{json.dumps(compact)}
Prices:{json.dumps(latest)}
Headlines:{json.dumps(headlines)}

Return JSON "commodities" array of 5 objects (Gold, Silver, Crude Oil, Copper, Natural Gas). Each: name, signal (buy/sell/hold/watch), support (from OHLC low), resistance (from OHLC high), analysis (4 sentences: technical + macro driver + cross‑asset + specific catalyst), positioning_note."""
    return _groq_call(COMMODITIES_SYSTEM, prompt, groq_key, max_tokens=2500)

# ─── 3. Trade Ideas ─────────────────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_trade_ideas(groq_key: str, news_key: str) -> dict:
    prices = fetch_prices()
    cb = fetch_live_macro()
    headlines = fetch_news_for_trades(news_key)

    def chg(key): v = prices.get(key, {}).get("pct"); return round(v, 2) if v else None
    data = {
        "us_sectors": {
            "Tech": chg("secTech"), "Financials": chg("secFin"), "Energy": chg("secEnrg"),
            "Materials": chg("secMatl"), "Industrials": chg("secInds"),
        },
        "india_sectors": {
            "IT": chg("inSIT"), "Bank": chg("inSBnk"), "Pharma": chg("inSPhrm"),
        },
        "yield_curve_spread": round(prices.get("US10Y",{}).get("price",0) - prices.get("US3M",{}).get("price",0), 2) if prices.get("US10Y",{}).get("price") and prices.get("US3M",{}).get("price") else None,
        "DXY": round(prices.get("DXY",{}).get("price",0),2),
        "VIX": round(prices.get("VIX",{}).get("price",0),2),
        "cb_stance": {k: v["stance"] for k, v in cb.items()},
        "headlines": headlines[:5],
    }
    prompt = f"""Based on the data and headlines, generate 3-5 trade ideas in a JSON "picks" array. Each pick: type, name (ticker), region, direction, conviction, timeframe, headline, thesis, risk.
DATA:{json.dumps(data)}"""
    return _groq_call(PICKS_SYSTEM, prompt, groq_key, max_tokens=2000)

# ─── 4. Geopolitical & Fund Radar ───────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_geo_analysis(groq_key: str, news_key: str) -> dict:
    headlines = fetch_news_for_geo(news_key)
    prompt = f"""From these geopolitical headlines:{json.dumps(headlines)}
Return JSON "geo" array of 4-6 objects: category (Fed Watch, Geopolitics, Earnings, Crypto, EM, Options), icon, headline, analysis, urgency."""
    return _groq_call(GEO_SYSTEM, prompt, groq_key, max_tokens=2000)

# ─── MERGE ALL ANALYSIS SECTIONS ────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_full_analysis(groq_key: str, news_key: str, today: str) -> dict:
    mood_macro = fetch_mood_and_macro(groq_key, news_key)
    commodities = fetch_commodities_analysis(groq_key, news_key)
    picks = fetch_trade_ideas(groq_key, news_key)
    geo = fetch_geo_analysis(groq_key, news_key)

    analysis = {}
    analysis.update(mood_macro)
    analysis.update(commodities)
    analysis.update(picks)
    analysis.update(geo)
    return analysis

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY CHARTS, HELPERS, RENDERERS (same as before)
# ─────────────────────────────────────────────────────────────────────────────

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

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def hd(v): return v is not None and v != ""
def fmt_num(v, d=2, prefix="", suffix=""):
    if v is None: return "—"
    if abs(v) >= 1000: return f"{prefix}{v:,.{d}f}{suffix}"
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
    st.markdown(f'<div style="height:3px;background:{color};border-radius:2px;margin-bottom:8px;"></div>', unsafe_allow_html=True)
def mini_badge(text, color):
    st.markdown(f'<span style="display:inline-block;font-family:JetBrains Mono,monospace;font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.5px;text-transform:uppercase;background:{color}22;color:{color};border:1px solid {color}44;">{text}</span>', unsafe_allow_html=True)

# ─── RENDERERS ──────────────────────────────────────────────────────────────
def render_hero(prices, analysis, gn_gold):
    # Hero row: Mood + VIX/DXY/AED/Gold | Asset bars | Yields
    mood = analysis.get("mood", {}) if analysis else {}
    score = int(mood.get("score", 50)) if mood.get("score") is not None else 50
    label = mood.get("label", "—")
    regime = mood.get("regime", "")
    c1, c2, c3 = st.columns([1.15, 1.55, 1.3])
    with c1:
        with st.container(border=True):
            st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0;">Market Mood</p>', unsafe_allow_html=True)
            st.markdown(chart_gauge(score), unsafe_allow_html=True)
            mood_col = {"Risk-On":G,"Cautious":A,"Risk-Off":R,"Volatile":B}.get(label, A)
            st.markdown(f'<div style="text-align:center;margin:4px 0;"><span style="background:{mood_col}22;color:{mood_col};border:1px solid {mood_col}44;border-radius:20px;padding:4px 14px;font-family:JetBrains Mono,monospace;font-size:13px;font-weight:700;">{label}</span></div>' + (f'<p style="text-align:center;font-size:10px;color:{MUT};font-family:JetBrains Mono,monospace;margin:3px 0;">{regime}</p>' if regime else ""), unsafe_allow_html=True)
            if hd(mood.get("summary")): st.caption(mood["summary"])
            st.divider()
            vix, dxy, aed, ga = prices.get("VIX",{}), prices.get("DXY",{}), prices.get("AEDIINR",{}), prices.get("GoldAED",{})
            if gn_gold and gn_gold.get("price"):
                ga = {"price": gn_gold["price"], "prev": ga.get("prev"), "pct": None, "source": "Gulf News"}
            sc1, sc2 = st.columns(2)
            with sc1:
                if vix.get("price"): st.metric("VIX", f"{vix['price']:.1f}", delta=pct_str(vix.get("pct")) if vix.get("pct") else None, delta_color="inverse")
                if aed.get("price"): st.metric(f"AED/INR", f"{aed['price']:.4f}", delta=pct_str(aed.get("pct")) if aed.get("pct") else None, delta_color="normal")
            with sc2:
                if dxy.get("price"): st.metric("DXY", f"{dxy['price']:.2f}", delta=pct_str(dxy.get("pct")) if dxy.get("pct") else None, delta_color="inverse")
                if ga.get("price"): st.metric(f"Gold/g 24K ({ga.get('source','')})", f"AED {ga['price']:.2f}", delta=pct_str(ga.get("pct")) if ga.get("pct") else None, delta_color="normal")
    with c2:
        with st.container(border=True):
            st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0 0 3px 0;">Asset Performance — vs Prior Close</p><p style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{BD};">Current price · % change · Prev close on hover</p>', unsafe_allow_html=True)
            fig = chart_asset_bars(prices)
            if fig.data: st.plotly_chart(fig, config={"displayModeBar": False})
            else: st.caption("Price data loading…")
    with c3:
        with st.container(border=True):
            st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;letter-spacing:1.8px;color:{MUT};text-transform:uppercase;margin:0 0 3px 0;">Sovereign Yields</p><p style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{BD};">Purple = 10Y · Blue = 3M/2Y · US · Germany · Japan</p>', unsafe_allow_html=True)
            fig = chart_yields(prices)
            if fig.data: st.plotly_chart(fig, config={"displayModeBar": False})
            else: st.caption("Yield data loading…")
            us10 = prices.get("US10Y",{}).get("price")
            us3m = prices.get("US3M",{}).get("price")
            if us10 and us3m:
                spread = round(us10 - us3m, 2)
                col = G if spread > 0 else R
                st.markdown(f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:{col};margin-top:6px;">US Yield Curve (10Y−3M): {spread:+.2f}%  {"▲ Normal" if spread > 0 else "▼ INVERTED"}</div>', unsafe_allow_html=True)
            if hd(mood.get("primary_risk")): st.warning(f"⚠ **Primary Risk:** {mood['primary_risk']}")

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
                    st.markdown(f'<div style="display:flex;align-items:center;gap:8px;"><span style="font-size:22px;">{m.get("flag","")}</span><span style="font-size:15px;font-weight:800;color:{INK};">{m.get("country","")}</span></div>', unsafe_allow_html=True)
                with r2: mini_badge(m.get("sentiment","").upper(), sc)
                if hd(m.get("key_signal")): st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{B};margin:4px 0 6px 0;">{m["key_signal"]}</p>', unsafe_allow_html=True)
                st.markdown(f"**{m.get('headline','')}**")
                st.markdown(m.get("analysis",""))
                if hd(m.get("contrarian")): st.info(f"🔍 **Contrarian:** {m['contrarian']}")
                if hd(m.get("cb_note")): st.markdown(f'<div style="background:rgba(91,141,239,.08);border:1px solid rgba(91,141,239,.22);border-radius:6px;padding:6px 9px;margin-top:6px;font-size:11px;color:{B};">🏦 {m["cb_note"]}</div>', unsafe_allow_html=True)

def render_commodities(prices, analysis):
    comms_ai = {c["name"]: c for c in (analysis.get("commodities") or [])} if analysis else {}
    SYM_MAP = [("Gold","Gold","XAU","$/oz"), ("Crude","Crude Oil","WTI","$/bbl"), ("Silver","Silver","XAG","$/oz"), ("Copper","Copper","HG","$/lb"), ("NatGas","Natural Gas","NG","$/MMBtu")]
    section_title("02", "Commodities", "Live prices · AI analysis")
    cols = st.columns(5)
    for i, (pkey, name, tkr, unit) in enumerate(SYM_MAP):
        pd_ = prices.get(pkey, {})
        ai = comms_ai.get(name, {})
        sc = sig_color(ai.get("signal","")) if ai.get("signal") else MUT
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                nr, br = st.columns([3,1])
                with nr:
                    st.markdown(f'<div style="font-size:14px;font-weight:800;color:{INK};">{name}</div><div style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{MUT};letter-spacing:.8px;">{tkr} · {unit}</div>', unsafe_allow_html=True)
                with br:
                    if ai.get("signal"): mini_badge(ai["signal"].upper(), sc)
                if pd_.get("price"):
                    pv = pd_.get("pct")
                    st.metric("", value=fmt_num(pd_["price"]), delta=pct_str(pv) if pv else None, delta_color="normal")
                    if pd_.get("prev"): st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};margin-top:-4px;">prev {fmt_num(pd_["prev"])}</p>', unsafe_allow_html=True)
                if ai.get("support") or ai.get("resistance"):
                    sc1, sc2 = st.columns(2)
                    with sc1:
                        if ai.get("support"): st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:8px;color:{MUT};margin-bottom:1px;">SUPPORT</p><p style="font-family:JetBrains Mono,monospace;font-size:11px;font-weight:700;color:{G};">{ai["support"]}</p>', unsafe_allow_html=True)
                    with sc2:
                        if ai.get("resistance"): st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:8px;color:{MUT};margin-bottom:1px;">RESISTANCE</p><p style="font-family:JetBrains Mono,monospace;font-size:11px;font-weight:700;color:{R};">{ai["resistance"]}</p>', unsafe_allow_html=True)
                if hd(ai.get("analysis")): st.caption(ai["analysis"])
                if hd(ai.get("positioning_note")): st.info(f"📊 {ai['positioning_note']}")

def render_heatmaps(prices):
    us_sec = {"Technology": prices.get("secTech",{}), "Financials": prices.get("secFin",{}), "Healthcare": prices.get("secHlth",{}), "Energy": prices.get("secEnrg",{}), "Materials": prices.get("secMatl",{}), "Industrials": prices.get("secInds",{}), "Cons. Disc": prices.get("secCyc",{}), "Cons. Stap": prices.get("secStpl",{}), "Utilities": prices.get("secUtil",{}), "Real Estate": prices.get("secRE",{}), "Comm. Svc": prices.get("secComm",{})}
    in_sec = {"IT": prices.get("inSIT",{}), "Banking": prices.get("inSBnk",{}), "Pharma": prices.get("inSPhrm",{}), "Auto": prices.get("inSAut",{}), "FMCG": prices.get("inSFmcg",{}), "Metal": prices.get("inSMtl",{}), "Energy": prices.get("inSEnrg",{}), "Realty": prices.get("inSRlt",{}), "Infra": prices.get("inSInf",{}), "Media": prices.get("inSMed",{}), "PSU Bank": prices.get("inSPSU",{})}
    has_us = any(d.get("pct") is not None for d in us_sec.values())
    has_in = any(d.get("pct") is not None for d in in_sec.values())
    if not has_us and not has_in: return
    section_title("03", "Sector Heatmaps", "US + India · vs prior close")
    c1, c2 = st.columns(2)
    with c1:
        fig = chart_treemap(us_sec, "🇺🇸  US — S&P 500 GICS Sectors")
        if fig: st.plotly_chart(fig, config={"displayModeBar":False})
        else: st.caption("US sector data unavailable")
    with c2:
        fig = chart_treemap(in_sec, "🇮🇳  India — NSE Sectoral Indices")
        if fig: st.plotly_chart(fig, config={"displayModeBar":False})
        else: st.caption("India sector data unavailable")

def render_central_banks(cb: dict):
    section_title("04", "Central Banks & Inflation", "Rates + CPI · Source: Trading Economics")
    rows = []
    for key, v in cb.items():
        rd = round(v["rate"]-v["rp"],2) if v.get("rate") and v.get("rp") else None
        cd = round(v["cpi"]-v["cpip"],2) if v.get("cpi") is not None and v.get("cpip") is not None else None
        rows.append({
            "": f"{v['flag']} {key}", "Bank": v["bank"],
            "Rate": f"{v['rate']:.2f}%", "Prev Rate": f"{v['rp']:.2f}%" + (f" ({'+' if rd>=0 else ''}{rd:.2f}pp)" if rd else ""),
            "Rate Date": v.get("rd","—"),
            "CPI": f"{v['cpi']:.1f}%" + ("  ↑" if cd and cd>0 else "  ↓" if cd and cd<0 else ""),
            "Prev CPI": f"{v['cpip']:.1f}%" if v.get("cpip") is not None else "—",
            "CPI Date": v.get("cpid","—"),
            "Stance": stance_label(v.get("stance","")),
            "Next Mtg": v.get("next","—"),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True, column_config={"": st.column_config.TextColumn(width="small"), "Rate": st.column_config.TextColumn(width="small"), "Stance": st.column_config.TextColumn(width="medium")})

def render_picks(analysis):
    picks = [p for p in (analysis.get("picks") or []) if hd(p.get("name"))]
    if not picks: return
    section_title("05", "Trade Ideas", f"{len(picks)} picks · AI-generated")
    cols = st.columns(len(picks))
    for i, p in enumerate(picks):
        dc = dir_color(p.get("direction",""))
        with cols[i]:
            with st.container(border=True):
                color_bar(dc)
                st.markdown(f'<div style="font-size:15px;font-weight:800;color:{INK};">{p.get("name","")}</div><div style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};">{p.get("type","").upper()} · {p.get("region","")}</div>', unsafe_allow_html=True)
                dir_lbl = "▲ LONG" if p.get("direction")=="long" else "▼ SHORT"
                st.markdown(f'<div style="margin:6px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;"><span style="font-family:JetBrains Mono,monospace;font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;background:{dc}22;color:{dc};border:1px solid {dc}44;">{dir_lbl}</span><span style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};">{p.get("timeframe","")}</span><span style="font-family:JetBrains Mono,monospace;font-size:9px;color:{MUT};">{p.get("conviction","").upper()} CONV</span></div>', unsafe_allow_html=True)
                if hd(p.get("headline")): st.markdown(f"**{p['headline']}**")
                if hd(p.get("thesis")): st.markdown(p["thesis"])
                if hd(p.get("risk")): st.error(f"⚡ **Risk:** {p['risk']}")

def render_geo(analysis):
    geo = [g for g in (analysis.get("geo") or []) if hd(g.get("headline"))]
    if not geo: return
    section_title("06", "Hedge Fund Radar", f"{len(geo)} themes")
    cols = st.columns(3)
    for i, g in enumerate(geo):
        uc = urgency_color(g.get("urgency",""))
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;"><span style="font-family:JetBrains Mono,monospace;font-size:9px;font-weight:700;letter-spacing:1.2px;color:{MUT};text-transform:uppercase;">{g.get("icon","")} {g.get("category","")}</span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{uc};box-shadow:0 0 5px {uc};"></span></div>', unsafe_allow_html=True)
                st.markdown(f"**{g.get('headline','')}**")
                if hd(g.get("analysis")): st.markdown(g["analysis"])

def render_setup():
    st.markdown("<br>", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 2.5, 1])
    with col:
        st.markdown(f'<div style="text-align:center;font-size:3.5rem;margin-bottom:.5rem;">📊</div><h2 style="text-align:center;color:{INK};font-weight:800;letter-spacing:-.4px;">AlphaTerminal Setup</h2><p style="text-align:center;color:{BODY};">3 steps · takes 2 minutes · powered by Groq (free)</p>', unsafe_allow_html=True)
        st.divider()
        for n, title, detail in [
            ("1", "Get a free Groq API key", "Go to **[console.groq.com](https://console.groq.com/)** → sign up → **API Keys** → **Create API Key** → copy it."),
            ("2", "Get a free NewsAPI key", "Go to **[newsapi.org](https://newsapi.org/register)** → sign up → copy API key."),
            ("3", "Add secrets", "Paste the following into Streamlit Secrets:"),
        ]:
            c1, c2 = st.columns([0.08, 0.92])
            with c1: st.markdown(f'<div style="background:{G};color:{BG};font-weight:800;font-size:13px;padding:3px 8px;border-radius:5px;text-align:center;margin-top:4px;">{n}</div>', unsafe_allow_html=True)
            with c2: st.markdown(f"**{title}**"); st.markdown(detail)
        st.code('''GROQ_API_KEY = "gsk_your_key"
NEWSAPI_KEY = "your_key"''', language="toml")
        st.success("Dashboard restarts after saving secrets.")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    groq_key = st.secrets.get("GROQ_API_KEY", "").strip()
    news_key = st.secrets.get("NEWSAPI_KEY", "").strip()
    now_uae = datetime.now(ZoneInfo("Asia/Dubai"))

    # HEADER
    h1, h2, h3, h4 = st.columns([2, 5, 1, 1])
    with h1:
        st.markdown(f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0 4px;"><div style="width:32px;height:32px;border-radius:8px;flex-shrink:0;background:linear-gradient(135deg,{G},{B});display:flex;align-items:center;justify-content:center;font-size:18px;color:{BG};font-weight:900;">α</div><div><div style="font-size:20px;font-weight:900;color:{INK};letter-spacing:-.4px;">Alpha<span style="color:{G};">Terminal</span></div><div style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{MUT};letter-spacing:1.5px;">MACRO · COMMODITIES · ALPHA</div></div></div>', unsafe_allow_html=True)
    with h2:
        st.markdown(f'<div style="padding:10px 0 0;"><span style="font-family:JetBrains Mono,monospace;font-size:10px;color:{MUT};">{now_uae.strftime("%A, %d %B %Y  %H:%M")} GST · Live data: Yahoo Finance, Trading Economics, NewsAPI · AI: Groq</span></div>', unsafe_allow_html=True)
    with h3:
        if st.button("↺ Prices", use_container_width=True):
            fetch_prices.clear(); fetch_live_macro.clear(); fetch_gold_aed_gulfnews.clear(); st.rerun()
    with h4:
        if st.button("⚡ Analysis", use_container_width=True):
            fetch_full_analysis.clear(); st.rerun()
    st.markdown(f'<hr style="border:none;border-top:1px solid {BD};margin:2px 0 10px 0;">', unsafe_allow_html=True)

    if not groq_key or not news_key:
        render_setup()
        return

    with st.spinner("Fetching live data…"):
        prices = fetch_prices()
        cb = fetch_live_macro()
        gn = fetch_gold_aed_gulfnews()
    if gn and gn.get("price") and "GoldAED" in prices:
        prices["GoldAED"]["price_gn"] = gn["price"]
        prices["GoldAED"]["source"] = "Gulf News"

    analysis = None
    with st.spinner("Running institutional AI analysis (4 Groq calls, ~2 min)…"):
        try:
            analysis = fetch_full_analysis(groq_key, news_key, TODAY)
        except Exception as e:
            st.error(f"❌ Analysis failed: {e}")
            analysis = None

    # STATUS BAR
    c1, c2, c3 = st.columns([2,2,2])
    with c1: st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{MUT};">● PRICES: {prices.get("_fetched","—")}</p>', unsafe_allow_html=True)
    with c2: st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{G if analysis else A};">{"✓ ANALYSIS LOADED" if analysis else "⚠ ANALYSIS UNAVAILABLE"}</p>', unsafe_allow_html=True)
    with c3: st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9.5px;color:{MUT};">{"✓ Live Macro" if any(v.get("rd") for v in cb.values()) else "⚠ TE fallback"}</p>', unsafe_allow_html=True)
    if not analysis: st.warning("AI analysis unavailable – prices and CB data are live. Click ⚡ Analysis to retry.", icon="⚠️")

    st.divider()

    # RENDER DASHBOARD
    render_hero(prices, analysis, gn)
    if analysis:
        render_macro(analysis)
        render_commodities(prices, analysis)
        render_heatmaps(prices)
        render_central_banks(cb)
        render_picks(analysis)
        render_geo(analysis)
    else:
        render_commodities(prices, None)
        render_heatmaps(prices)
        render_central_banks(cb)

    st.divider()
    st.markdown(f'<p style="font-family:JetBrains Mono,monospace;font-size:9px;color:{BD};text-align:center;padding:8px 0;">ALPHA TERMINAL · PRICES: YAHOO FINANCE · CB DATA: TRADING ECONOMICS · ANALYSIS: GROQ (LLAMA 3.3) · NOT FINANCIAL ADVICE</p>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
