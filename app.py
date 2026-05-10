"""
AlphaTerminal — Daily Macro Dashboard
Architecture:
  • Prices   → yfinance (free, no key, 15-min cache)
  • Analysis → Gemini 2.0 Flash via 4 parallel focused calls
               Each call covers one section → avoids giant prompt failing
               Cached to /tmp/{date}.json → survives page reloads
               Rate-limit safe: 4 calls in parallel, 3 retries with backoff each
  • UI       → All controls inline in main page (no sidebar dependency)
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import re
import time
import logging
import warnings
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── silence noise ─────────────────────────────────────────────────────────────
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaTerminal · Live Macro Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",   # sidebar not needed
)

# ── palette ───────────────────────────────────────────────────────────────────
C = dict(
    bg="#08090d", surf="#101319", bord="#1d2330",
    ink="#eaeef5", body="#a8b3c5", mute="#5a6577", dim="#363f51",
    green="#2dd4a7", red="#f4516c", amber="#f0b429",
    blue="#5b8def", purple="#a78bfa",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700
  &family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700;9..40,800
  &family=Instrument+Serif:ital@0;1&display=swap');

*{{box-sizing:border-box;margin:0;padding:0}}
html,body,[class*="css"],.stApp{{background:{C["bg"]} !important;
  font-family:"DM Sans",sans-serif !important;color:{C["body"]}}}
#MainMenu,footer,header,.stDeployButton{{visibility:hidden;display:none}}
.main .block-container{{padding-top:.6rem;max-width:1440px}}
div[data-testid="stSidebar"]{{display:none !important}}

/* buttons */
.stButton>button{{background:{C["green"]} !important;color:{C["bg"]} !important;
  font-weight:700 !important;border:none !important;border-radius:6px !important;
  font-size:12px !important;box-shadow:0 2px 10px rgba(45,212,167,.2) !important}}
.stButton>button:hover{{box-shadow:0 4px 18px rgba(45,212,167,.4) !important;
  transform:translateY(-1px)}}
.stButton>button:disabled{{opacity:.45 !important;cursor:not-allowed !important}}
button[kind="secondary"]{{background:{C["surf"]} !important;color:{C["body"]} !important;
  border:1px solid {C["bord"]} !important}}

/* metrics */
div[data-testid="metric-container"]{{background:{C["surf"]};
  border:1px solid {C["bord"]};border-radius:8px;padding:10px 13px}}
div[data-testid="metric-container"] label{{color:{C["mute"]} !important;
  font-family:"JetBrains Mono",monospace !important;font-size:9px !important;
  letter-spacing:1.2px !important}}
div[data-testid="stMetricValue"]{{color:{C["ink"]} !important;
  font-family:"JetBrains Mono",monospace !important;font-size:19px !important;font-weight:700 !important}}
div[data-testid="stMetricDelta"]{{font-family:"JetBrains Mono",monospace !important;font-size:11px !important}}

/* misc */
div[data-testid="stMarkdownContainer"] p{{color:{C["body"]}}}
.stSpinner>div{{border-top-color:{C["green"]} !important}}
.stProgress .st-bo{{background:{C["green"]} !important}}
hr{{border-color:{C["bord"]} !important;margin:.5rem 0}}
</style>""", unsafe_allow_html=True)

# ── constants ─────────────────────────────────────────────────────────────────
AED_PEG = 3.6725
GRAM_OZ = 31.1035
TODAY   = str(date.today())
CACHE_F = Path(f"/tmp/alpha_{TODAY}.json")

CORE_SYMS = {
    "^GSPC":"S&P 500","^IXIC":"Nasdaq","^DJI":"Dow Jones",
    "^NSEI":"Nifty 50","^BSESN":"Sensex","^N225":"Nikkei",
    "^HSI":"Hang Seng","^GDAXI":"DAX",
    "GC=F":"Gold","CL=F":"WTI Crude","SI=F":"Silver",
    "HG=F":"Copper","NG=F":"Nat Gas","BTC-USD":"Bitcoin",
    "DX-Y.NYB":"DXY","^VIX":"VIX","AEDIINR=X":"AED/INR",
}
US_SEC = {
    "XLK":"Technology","XLF":"Financials","XLV":"Healthcare",
    "XLE":"Energy","XLB":"Materials","XLI":"Industrials",
    "XLY":"Consumer Disc.","XLP":"Consumer Stap.","XLU":"Utilities",
    "XLRE":"Real Estate","XLC":"Comm. Services",
}
IN_SEC = {
    "^CNXIT":"IT","^NSEBANK":"Banking","^CNXPHARMA":"Pharma",
    "^CNXAUTO":"Auto","^CNXFMCG":"FMCG","^CNXMETAL":"Metal",
    "^CNXENERGY":"Energy","^CNXREALTY":"Realty","^CNXINFRA":"Infra",
    "^CNXMEDIA":"Media","^CNXPSUBANK":"PSU Bank",
}
YIELD_MAP = [
    ("US",      "🇺🇸", ["^TNX"],                      ["^IRX"]),
    ("Germany", "🇩🇪", ["^GDBR10","^BUND"],            []),
    ("UK",      "🇬🇧", ["^TMBMKGB-10Y","^GBGB10YT"],   []),
    ("Japan",   "🇯🇵", ["^JRGB","^TMBMKJP-10Y"],       []),
    ("India",   "🇮🇳", ["INDGB10Y=X","IN10YT=RR"],      []),
    ("China",   "🇨🇳", ["CNGB10Y=X","CN10YT=RR"],       []),
]
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.5-flash-preview-05-20", "gemini-1.5-flash-latest"]

# ─────────────────────────────────────────────────────────────────────────────
#  PRICE FETCHING  (yfinance, free, no API key)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def fetch_prices() -> dict:
    result: dict = {}
    # Batch — core symbols
    batch = [s for s in list(CORE_SYMS) + list(US_SEC) + list(IN_SEC) if s != "AEDIINR=X"]
    try:
        raw = yf.download(batch, period="5d", auto_adjust=True,
                          progress=False, threads=True, group_by="ticker")
        if not raw.empty:
            lvl0 = raw.columns.get_level_values(0) if hasattr(raw.columns,"get_level_values") else []
            for sym in batch:
                try:
                    cl = (raw[sym]["Close"] if sym in lvl0 else raw["Close"]).dropna()
                    if len(cl) < 1: continue
                    last = float(cl.iloc[-1]); prev = float(cl.iloc[-2]) if len(cl)>=2 else None
                    result[sym] = {"price":last,"prev":prev,
                                   "pct":((last-prev)/prev*100) if prev else None}
                except Exception: continue
    except Exception: pass

    # Individual — yields + AED/INR (fail in batch)
    indiv_syms = ["AEDIINR=X"] + [s for row in YIELD_MAP for s in row[2]+row[3]]
    seen = set()
    for sym in dict.fromkeys(indiv_syms):
        if sym in result: continue
        meta_country = next((row[0] for row in YIELD_MAP if sym in row[2]+row[3]), None)
        if meta_country and meta_country in seen: continue
        try:
            hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
            if hist.empty: continue
            cl = hist["Close"].dropna()
            if len(cl)<1: continue
            last=float(cl.iloc[-1]); prev=float(cl.iloc[-2]) if len(cl)>=2 else None
            result[sym] = {"price":last,"prev":prev,"pct":((last-prev)/prev*100) if prev else None}
            if meta_country: seen.add(meta_country)
        except Exception: continue

    # Gold in AED/gram 24K (derived)
    if result.get("GC=F",{}).get("price"):
        g = result["GC=F"]
        result["GOLD_AED"] = {
            "price": g["price"]/GRAM_OZ*AED_PEG,
            "prev":  g["prev"] /GRAM_OZ*AED_PEG if g.get("prev") else None,
        }
    result["_at"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y %H:%M %Z")
    return result

# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI  —  4 parallel targeted calls (one per section)
# ─────────────────────────────────────────────────────────────────────────────
SECTIONS = {

"macro": {
"sys": "Elite macro analyst. Google Search for today's news. Return ONLY JSON, no fences.",
"user": f"Today {TODAY}. Search Google for latest news on US/India/China/Japan/Eurozone economy, "
        "central bank actions, yields, currency moves. Return this JSON:\n"
        '{"mood":{"score":52,"label":"Cautious","sum":"1 sentence"},'
        '"macro":[{"c":"US","f":"🇺🇸","hl":"headline≤12w","sum":"2-3 sentence analysis",'
        '"sent":"bullish","km":"10Y:4.52%","cb":"CB note or empty"},'
        '{"c":"India","f":"🇮🇳","hl":"","sum":"","sent":"neutral","km":"","cb":""},'
        '{"c":"China","f":"🇨🇳","hl":"","sum":"","sent":"bearish","km":"","cb":""},'
        '{"c":"Japan","f":"🇯🇵","hl":"","sum":"","sent":"neutral","km":"","cb":""},'
        '{"c":"Eurozone","f":"🇪🇺","hl":"","sum":"","sent":"neutral","km":"","cb":""}]}\n'
        'label→Risk-On|Cautious|Risk-Off|Volatile sent→bullish|bearish|neutral. Use "" for unknowns.',
},

"comms": {
"sys": "Elite commodity analyst. Google Search for today's data. Return ONLY JSON, no fences.",
"user": f"Today {TODAY}. Search Google for Gold/Silver/Copper/WTI Crude/Natural Gas "
        "current technical outlook, key support and resistance levels, analyst signals. Return:\n"
        '{"comms":[{"n":"Gold","s":"XAU","out":"2-sentence outlook","sig":"hold","sup":"$2280","res":"$2400"},'
        '{"n":"Silver","s":"XAG","out":"","sig":"buy","sup":"","res":""},'
        '{"n":"Copper","s":"HG","out":"","sig":"buy","sup":"","res":""},'
        '{"n":"Crude Oil","s":"WTI","out":"","sig":"hold","sup":"","res":""},'
        '{"n":"Natural Gas","s":"NG","out":"","sig":"watch","sup":"","res":""}]}\n'
        'sig→buy|sell|hold|watch. Use "" for unknowns.',
},

"banks": {
"sys": "Elite CB and investment analyst. Google Search for latest data. Return ONLY JSON, no fences.",
"user": f"Today {TODAY}. Search Google for: (1) Current policy rates and latest CPI for "
        "Fed/RBI/BOE/ECB/BOJ/PBOC — exact numbers and dates. "
        "(2) Generate 5 high-conviction trade ideas across US/India/Global markets. Return:\n"
        '{"banks":[{"c":"US","f":"🇺🇸","bank":"Fed","rate":"5.25","rp":"5.50","rd":"Sep 2024",'
        '"cpi":"3.2","cpip":"3.4","cpid":"Apr 2025","next":"Jun 11","stance":"hold"},'
        '{"c":"India","f":"🇮🇳","bank":"RBI","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"cut"},'
        '{"c":"UK","f":"🇬🇧","bank":"BOE","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hold"},'
        '{"c":"Euro","f":"🇪🇺","bank":"ECB","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"cut"},'
        '{"c":"Japan","f":"🇯🇵","bank":"BOJ","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hike"},'
        '{"c":"China","f":"🇨🇳","bank":"PBOC","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"cut"}],'
        '"picks":[{"t":"etf","n":"name","reg":"Global","dir":"long","conv":"high",'
        '"hl":"headline≤12w","thesis":"2-3 sentence thesis with catalyst and risk","tf":"3 months"},'
        '{"t":"stock","n":"","reg":"US","dir":"long","conv":"high","hl":"","thesis":"","tf":""},'
        '{"t":"sector","n":"","reg":"India","dir":"long","conv":"medium","hl":"","thesis":"","tf":""},'
        '{"t":"commodity","n":"","reg":"Global","dir":"long","conv":"medium","hl":"","thesis":"","tf":""},'
        '{"t":"etf","n":"","reg":"EM","dir":"short","conv":"low","hl":"","thesis":"","tf":""}]}\n'
        'stance→hold|cut|hike dir→long|short|neutral conv→high|medium|low rate/cpi→plain number no % symbol.',
},

"geo": {
"sys": "Elite geopolitical analyst. Google Search for today's themes. Return ONLY JSON, no fences.",
"user": f"Today {TODAY}. Search Google for today's key themes hedge funds are watching: "
        "geopolitical risks, Fed/CB watch, bond market stress, earnings season themes, "
        "crypto moves, EM currency stress. Return:\n"
        '{"geo":[{"cat":"Geopolitics","icon":"🌍","hl":"headline≤12w","det":"2-sentence detail","urg":"high"},'
        '{"cat":"Fed Watch","icon":"🏦","hl":"","det":"","urg":"high"},'
        '{"cat":"Rates & Bonds","icon":"📈","hl":"","det":"","urg":"medium"},'
        '{"cat":"Earnings Season","icon":"📊","hl":"","det":"","urg":"medium"},'
        '{"cat":"Crypto","icon":"₿","hl":"","det":"","urg":"low"},'
        '{"cat":"EM Risk","icon":"🌏","hl":"","det":"","urg":"medium"}]}\n'
        'urg→high|medium|low.',
},
}


def _gemini_call(api_key: str, section_key: str) -> dict:
    """One Gemini call for one section. 3 retries with backoff. Tries multiple models."""
    sec = SECTIONS[section_key]
    body = {
        "systemInstruction": {"parts": [{"text": sec["sys"]}]},
        "contents": [{"role":"user","parts":[{"text": sec["user"]}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.1},
    }
    for model in GEMINI_MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta"
               f"/models/{model}:generateContent?key={api_key}")
        for attempt in range(3):
            try:
                resp = requests.post(url, json=body, timeout=60)
                if resp.status_code == 404:
                    break                    # try next model
                if resp.status_code == 429:
                    if attempt < 2:
                        time.sleep(10 * (attempt + 1))
                        continue
                    break                    # try next model
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    if data["error"].get("code") == 429:
                        if attempt < 2:
                            time.sleep(10 * (attempt + 1))
                            continue
                        break
                    break
                parts = (data.get("candidates",[{}])[0]
                             .get("content",{}).get("parts",[]))
                text = "".join(p.get("text","") for p in parts if "text" in p)
                text = re.sub(r'\[\d+\]', '', text).replace("```json","").replace("```","").strip()
                s, e = text.find("{"), text.rfind("}")
                if s == -1:
                    break
                return json.loads(text[s:e+1])
            except json.JSONDecodeError:
                break
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(5)
                    continue
                break
            except Exception:
                break
    return {}   # section failed → renders without this section, others still show


def load_cache() -> dict | None:
    if CACHE_F.exists():
        try:
            d = json.loads(CACHE_F.read_text())
            return d if isinstance(d, dict) and d else None
        except Exception:
            return None
    return None


def save_cache(data: dict):
    try:
        CACHE_F.parent.mkdir(parents=True, exist_ok=True)
        CACHE_F.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def fetch_all_analysis(api_key: str) -> dict:
    """Runs 4 Gemini section calls in PARALLEL. Returns merged dict."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_gemini_call, api_key, k): k for k in SECTIONS}
        for fut in as_completed(futures, timeout=120):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = {}

    merged: dict = {}
    merged.update(results.get("macro", {}))    # mood + macro
    if results.get("comms"):
        merged["comms"] = results["comms"].get("comms", [])
    if results.get("banks"):
        merged["banks"] = results["banks"].get("banks", [])
        merged["picks"] = results["banks"].get("picks", [])
    if results.get("geo"):
        merged["geo"] = results["geo"].get("geo", [])

    merged["_sections"] = {k: bool(v) for k, v in results.items()}
    merged["_fetched"]  = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y %H:%M %Z")
    return merged

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
hd   = lambda v: v is not None and v != ""
pctv = lambda s: float(m.group()) if (m := re.search(r'-?\d+(?:\.\d+)?', str(s or ""))) else 0.0
isup = lambda s: pctv(s) >= 0

def fmt(v, d=2, pre=""):
    if v is None: return "—"
    return f"{pre}{v:,.{d}f}" if v >= 1000 else f"{pre}{v:.{d}f}"

def sent_c(s): return C["green"] if s=="bullish" else C["red"] if s=="bearish" else C["amber"]
def sig_c(s):  return C["green"] if s=="buy" else C["red"] if s=="sell" else C["amber"] if s=="hold" else C["blue"]
def urg_c(u):  return C["red"] if u=="high" else C["amber"] if u=="medium" else C["green"]
def dir_s(d):
    if d=="long":  return "rgba(45,212,167,.1)",C["green"],"rgba(45,212,167,.22)"
    if d=="short": return "rgba(244,81,108,.1)",C["red"],  "rgba(244,81,108,.22)"
    return "rgba(240,180,41,.1)",C["amber"],"rgba(240,180,41,.22)"
def stance_c(s): return C["green"] if s=="cut" else C["red"] if s=="hike" else C["amber"]
def stance_l(s): return "↓ EASING" if s=="cut" else "↑ TIGHTENING" if s=="hike" else "◆ HOLD"
MOOD_M = {
    "Risk-On": (C["green"],"rgba(45,212,167,.15)"),
    "Cautious":(C["amber"], "rgba(240,180,41,.15)"),
    "Risk-Off": (C["red"],  "rgba(244,81,108,.15)"),
    "Volatile": (C["blue"], "rgba(91,141,239,.15)"),
}

def panel(content_fn, title="", sub=""):
    """Dark panel wrapper using st.container."""
    with st.container():
        st.markdown(
            f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
            f'border-radius:11px;padding:16px;">'
            f'<div style="font-family:monospace;font-size:9.5px;letter-spacing:1.6px;'
            f'color:{C["mute"]};text-transform:uppercase;margin-bottom:3px;">{title}</div>'
            f'<div style="font-family:monospace;font-size:9px;color:{C["dim"]};'
            f'margin-bottom:11px;">{sub}</div></div>',
            unsafe_allow_html=True,
        )
        content_fn()

def sec_hdr(n, title, sub=""):
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:11px;margin:1.8rem 0 .9rem 0;'
        f'padding-bottom:9px;border-bottom:1px solid {C["bord"]};">'
        f'<span style="font-family:monospace;font-size:10px;color:{C["mute"]};letter-spacing:1.4px;">{n}</span>'
        f'<span style="font-family:\'Instrument Serif\',serif;font-size:25px;color:{C["ink"]};'
        f'font-weight:400;letter-spacing:-.4px;">{title}</span>'
        f'<div style="flex:1;height:1px;background:{C["bord"]};"></div>'
        f'<span style="font-family:monospace;font-size:9.5px;color:{C["mute"]};'
        f'letter-spacing:1px;text-transform:uppercase;">{sub}</span></div>',
        unsafe_allow_html=True,
    )

def md(html): st.markdown(html, unsafe_allow_html=True)

def small_badge(color, text):
    return (f'<span style="display:inline-block;font-family:monospace;font-size:9px;'
            f'font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.5px;'
            f'text-transform:uppercase;background:{color}22;color:{color};'
            f'border:1px solid {color}44;">{text}</span>')

# ─────────────────────────────────────────────────────────────────────────────
#  CHART COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────
def mood_gauge(score=50):
    r, cx, cy = 70, 90, 90
    a = (score/100)*3.14159-3.14159
    import math
    seg = lambda s,e,col: (
        f'<path d="M{cx+r*math.cos((s/100)*3.14159-3.14159):.1f} '
        f'{cy+r*math.sin((s/100)*3.14159-3.14159):.1f} A{r} {r} 0 0 1 '
        f'{cx+r*math.cos((e/100)*3.14159-3.14159):.1f} '
        f'{cy+r*math.sin((e/100)*3.14159-3.14159):.1f}" '
        f'stroke="{col}" stroke-width="10" fill="none" stroke-linecap="round"/>'
    )
    nx, ny = cx+r*math.cos(a), cy+r*math.sin(a)
    md(f'<svg viewBox="0 0 180 110" width="160" height="100" style="display:block;margin:0 auto;">'
       f'{seg(2,33,C["red"])}{seg(34,66,C["amber"])}{seg(67,98,C["green"])}'
       f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
       f'stroke="{C["ink"]}" stroke-width="2.5" stroke-linecap="round"/>'
       f'<circle cx="{cx}" cy="{cy}" r="5" fill="{C["ink"]}"/>'
       f'<circle cx="{cx}" cy="{cy}" r="2" fill="{C["bg"]}"/>'
       f'<text x="{cx}" y="{cy+22}" text-anchor="middle" '
       f'font-family="monospace" font-size="20" font-weight="700" '
       f'fill="{C["ink"]}">{score}</text></svg>')

def asset_bar_chart(prices):
    syms = [("^GSPC","S&P 500"),("^IXIC","Nasdaq"),("^DJI","Dow Jones"),
            ("^NSEI","Nifty 50"),("^BSESN","Sensex"),("^N225","Nikkei"),
            ("^HSI","Hang Seng"),("^GDAXI","DAX"),
            ("GC=F","Gold"),("CL=F","WTI Crude"),("BTC-USD","Bitcoin"),("DX-Y.NYB","DXY")]
    lbls, vals, cols, tips = [], [], [], []
    for sym, lbl in syms:
        d = prices.get(sym)
        if d and d.get("pct") is not None:
            v = round(d["pct"], 2)
            lbls.append(lbl); vals.append(v)
            cols.append(C["green"] if v>=0 else C["red"])
            tips.append(f"<b>{lbl}</b><br>Now: {fmt(d.get('price',0))}"
                        f"<br>Prev: {fmt(d.get('prev',0))}<br>Chg: {'+' if v>=0 else ''}{v:.2f}%")
    if not lbls:
        st.caption("Price data loading…"); return
    fig = go.Figure(go.Bar(x=vals, y=lbls, orientation="h",
        marker_color=cols, marker_line_width=0, opacity=.85,
        text=[f"{'+' if v>=0 else ''}{v:.2f}%" for v in vals],
        textposition="outside",
        textfont={"color":C["body"],"size":10,"family":"JetBrains Mono"},
        hovertemplate="%{customdata}<extra></extra>", customdata=tips))
    fig.update_layout(height=max(300,len(lbls)*28),margin=dict(l=0,r=65,t=5,b=5),
        paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True,gridcolor=C["bord"],zerolinecolor=C["dim"],
                   tickfont={"color":C["mute"],"size":9,"family":"JetBrains Mono"},ticksuffix="%"),
        yaxis=dict(showgrid=False,tickfont={"color":C["body"],"size":11,"family":"DM Sans"},automargin=True),
        bargap=.35,shapes=[dict(type="line",x0=0,x1=0,y0=-.5,y1=len(lbls)-.5,
                                line=dict(color=C["dim"],width=1),layer="below")])
    st.plotly_chart(fig, config={"displayModeBar":False})

def yield_chart(prices):
    lbls, y10, y2, y10p = [], [], [], []
    mx = 0
    for cname, flag, syms10, syms2 in YIELD_MAP:
        d10 = next((prices[s] for s in syms10 if prices.get(s,{}).get("price")), None)
        d2  = next((prices[s] for s in syms2  if prices.get(s,{}).get("price")), None)
        if not d10: continue
        v10 = round(d10["price"], 2); mx = max(mx, v10)
        lbls.append(f"{flag} {cname}"); y10.append(v10)
        y2.append(round(d2["price"],2) if d2 else None)
        y10p.append(round(d10["prev"],2) if d10.get("prev") else None)
    if not lbls:
        st.caption("Yield data loading…"); return
    fig = go.Figure()
    fig.add_trace(go.Bar(x=y10,y=lbls,orientation="h",name="10Y",
        marker_color=C["purple"],marker_line_width=0,opacity=.85,
        text=[f"{v:.2f}%" for v in y10],textposition="outside",
        textfont={"color":C["body"],"size":10,"family":"JetBrains Mono"}))
    if any(v is not None for v in y2):
        fig.add_trace(go.Bar(x=[v or 0 for v in y2],y=lbls,orientation="h",name="2Y/3M",
            marker_color=C["blue"],marker_line_width=0,opacity=.65,
            text=[f"{v:.2f}%" if v else "" for v in y2],textposition="outside",
            textfont={"color":C["body"],"size":10,"family":"JetBrains Mono"}))
    fig.update_layout(barmode="overlay",height=max(220,len(lbls)*42),
        margin=dict(l=0,r=60,t=5,b=5),paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True,gridcolor=C["bord"],zerolinecolor=C["dim"],
                   tickfont={"color":C["mute"],"size":9,"family":"JetBrains Mono"},ticksuffix="%"),
        yaxis=dict(showgrid=False,tickfont={"color":C["body"],"size":11,"family":"DM Sans"},automargin=True),
        legend=dict(orientation="h",x=0,y=-0.1,font={"color":C["mute"],"size":10},
                    bgcolor="rgba(0,0,0,0)"),bargap=.3)
    st.plotly_chart(fig, config={"displayModeBar":False})

def sector_heatmap(sector_dict, prices, title):
    names, vals, pcts, texts = [], [], [], []
    for sym, lbl in sector_dict.items():
        d = prices.get(sym)
        if d and d.get("pct") is not None:
            v = round(d["pct"], 2)
            names.append(lbl); vals.append(abs(v)+0.05); pcts.append(v)
            texts.append(f"{'+' if v>=0 else ''}{v:.2f}%")
    if not names:
        st.caption("Sector data unavailable"); return
    fig = go.Figure(go.Treemap(
        labels=names, parents=[""]*len(names), values=vals,
        text=texts, texttemplate="<b>%{label}</b><br>%{text}",
        hovertemplate="<b>%{label}</b><br>%{text}<extra></extra>",
        marker=dict(
            colors=pcts,
            colorscale=[[0,"rgba(180,40,60,.8)"],[.35,"rgba(100,30,50,.5)"],
                        [.5,"rgba(22,27,37,.9)"],[.65,"rgba(15,60,50,.5)"],
                        [1,"rgba(30,160,110,.85)"]],
            cmid=0, line=dict(width=1.5,color=C["bg"])),
        pathbar_visible=False,
        textfont={"family":"DM Sans","size":12,"color":C["ink"]}))
    fig.update_layout(title=dict(text=title,font={"color":C["mute"],"size":11,
                      "family":"JetBrains Mono"},x=0,pad=dict(l=0,t=0)),
        height=270, margin=dict(l=0,r=0,t=28,b=0),
        paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, config={"displayModeBar":False})

# ─────────────────────────────────────────────────────────────────────────────
#  SECTION RENDERERS
# ─────────────────────────────────────────────────────────────────────────────
def render_hero(prices, analysis):
    mood = analysis.get("mood",{}) if analysis else {}
    score = int(mood.get("score",50)) if hd(mood.get("score")) else 50
    label = mood.get("label","—")
    mc, mbg = MOOD_M.get(label, (C["amber"],"rgba(240,180,41,.12)"))

    c1, c2, c3 = st.columns([1.1, 1.5, 1.3])
    with c1:
        md(f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
           f'border-radius:11px;padding:15px 15px 8px 15px;">'
           f'<div style="font-family:monospace;font-size:9px;letter-spacing:1.8px;'
           f'color:{C["mute"]};text-transform:uppercase;margin-bottom:12px;">Market Mood</div>')
        mood_gauge(score)
        md(f'<div style="text-align:center;margin:6px 0 4px;">'
           f'<span style="background:{mbg};color:{mc};border:1px solid {mc}44;'
           f'border-radius:20px;padding:3px 14px;font-family:monospace;'
           f'font-size:13px;font-weight:700;">{label}</span></div>')
        if hd(mood.get("sum")):
            md(f'<div style="font-family:\'Instrument Serif\',serif;font-style:italic;'
               f'font-size:11px;color:{C["body"]};text-align:center;line-height:1.5;'
               f'padding:0 4px 8px;">{mood["sum"]}</div>')

        # Stats row: VIX DXY AED/INR Gold AED
        vix = prices.get("^VIX",{}); dxy = prices.get("DX-Y.NYB",{})
        aed = prices.get("AEDIINR=X",{}); ga = prices.get("GOLD_AED",{})
        stats = []
        if vix.get("price"): stats.append(("VIX",f"{vix['price']:.1f}",f"prev {vix['prev']:.1f}" if vix.get("prev") else ""))
        if dxy.get("price"): stats.append(("DXY",f"{dxy['price']:.2f}",f"prev {dxy['prev']:.2f}" if dxy.get("prev") else ""))
        if aed.get("price"): stats.append(("AED/INR",f"{aed['price']:.4f}",f"prev {aed['prev']:.4f}" if aed.get("prev") else ""))
        if ga.get("price"):  stats.append(("GOLD/g 24K",f"AED {ga['price']:.2f}",f"prev {ga['prev']:.2f}" if ga.get("prev") else ""))
        if stats:
            inner = "".join(f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;'
                            f'padding:5px 4px;border-right:1px solid {C["bord"]};" '
                            f'class="ls">'
                            f'<div style="font-family:monospace;font-size:8px;color:{C["mute"]};'
                            f'letter-spacing:.9px;text-transform:uppercase;">{s[0]}</div>'
                            f'<div style="font-family:monospace;font-size:12px;font-weight:700;'
                            f'color:{C["ink"]};margin-top:1px;">{s[1]}</div>'
                            f'<div style="font-family:monospace;font-size:8px;color:{C["mute"]};">{s[2]}</div>'
                            f'</div>' for s in stats)
            md(f'<div style="display:flex;border-top:1px solid {C["bord"]};margin-top:6px;">'
               f'{inner}</div>'
               f'<style>.ls:last-child{{border-right:none!important}}</style>')
        md('</div>')

    with c2:
        md(f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
           f'border-radius:11px;padding:15px;">'
           f'<div style="font-family:monospace;font-size:9px;letter-spacing:1.8px;'
           f'color:{C["mute"]};text-transform:uppercase;margin-bottom:3px;">Asset Performance — vs Prior Close</div>'
           f'<div style="font-family:monospace;font-size:9px;color:{C["dim"]};margin-bottom:10px;">'
           f'CURRENT · CHANGE % · PREV CLOSE · YAHOO FINANCE</div>')
        asset_bar_chart(prices)
        md('</div>')

    with c3:
        md(f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
           f'border-radius:11px;padding:15px;">'
           f'<div style="font-family:monospace;font-size:9px;letter-spacing:1.8px;'
           f'color:{C["mute"]};text-transform:uppercase;margin-bottom:3px;">Sovereign Yields — 10Y + 2Y/3M</div>'
           f'<div style="font-family:monospace;font-size:9px;color:{C["dim"]};margin-bottom:10px;">'
           f'PURPLE = 10Y · BLUE = 2Y/3M · YAHOO FINANCE</div>')
        yield_chart(prices)
        md('</div>')


def render_macro(analysis):
    items = [m for m in (analysis.get("macro") or []) if hd(m.get("hl"))]
    if not items: return
    sec_hdr("01","Global Macro",f"{len(items)} economies")
    cols = st.columns(len(items))
    for i, m in enumerate(items):
        c = sent_c(m.get("sent",""))
        with cols[i]:
            md(f'<div style="border-top:3px solid {c};border:1px solid {C["bord"]};'
               f'border-top:3px solid {c};border-radius:0 0 10px 10px;'
               f'background:{C["surf"]};padding:14px;">'
               f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">'
               f'<div style="display:flex;align-items:center;gap:7px;">'
               f'<span style="font-size:21px;">{m.get("f","")}</span>'
               f'<div><div style="font-family:\'Instrument Serif\',serif;font-size:17px;'
               f'color:{C["ink"]};font-weight:400;letter-spacing:-.2px;">{m.get("c","")}</div>'
               + (f'<div style="font-family:monospace;font-size:9.5px;color:{C["blue"]};margin-top:1px;">'
                  f'{m["km"]}</div>' if hd(m.get("km")) else "")
               + f'</div></div>{small_badge(c, m.get("sent",""))}</div>'
               f'<div style="font-size:12.5px;font-weight:700;color:{C["ink"]};margin-bottom:5px;'
               f'line-height:1.45;">{m.get("hl","")}</div>'
               f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{m.get("sum","")}</div>'
               + (f'<div style="margin-top:8px;padding:5px 8px;background:rgba(91,141,239,.06);'
                  f'border:1px solid rgba(91,141,239,.18);border-radius:5px;'
                  f'font-size:10px;color:{C["blue"]};font-family:monospace;">🏦 {m["cb"]}</div>'
                  if hd(m.get("cb")) else "")
               + '</div>')


def render_commodities(prices, analysis):
    comms_ai = {c["n"]:c for c in (analysis.get("comms") or [])} if analysis else {}
    SYM_MAP = [("GC=F","Gold","XAU"),("CL=F","Crude Oil","WTI"),
               ("SI=F","Silver","XAG"),("HG=F","Copper","HG"),("NG=F","Natural Gas","NG")]
    sec_hdr("02","Commodities","Live prices · AI outlook")
    cols = st.columns(5)
    for i,(sym,name,tkr) in enumerate(SYM_MAP):
        pd_ = prices.get(sym,{}); ai = comms_ai.get(name,{})
        sc  = sig_c(ai.get("sig","")) if ai.get("sig") else C["mute"]
        pv  = pd_.get("pct"); up = (pv or 0)>=0
        with cols[i]:
            md(f'<div style="height:3px;background:{sc};border-radius:2px;margin-bottom:9px;"></div>')
            md(f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">'
               f'<div><div style="font-size:15px;font-weight:800;color:{C["ink"]};">{name}</div>'
               f'<div style="font-family:monospace;font-size:9px;color:{C["mute"]};'
               f'letter-spacing:1px;margin-top:2px;">{tkr}</div></div>'
               + (small_badge(sc, ai["sig"].upper()) if ai.get("sig") else "")
               + '</div>')
            if pd_.get("price"):
                pcc = C["green"] if up else C["red"]
                arrow = "▲" if up else "▼"
                md(f'<div style="background:{C["bg"]};border:1px solid {C["bord"]};'
                   f'border-radius:6px;padding:7px 9px;margin-bottom:5px;">'
                   f'<div style="font-family:monospace;font-size:18px;font-weight:700;'
                   f'color:{C["ink"]};">{fmt(pd_["price"])}'
                   + (f'<span style="font-size:12px;font-weight:700;color:{pcc};margin-left:7px;">'
                      f'{arrow} {("+" if up else "")}{pv:.2f}%</span>' if pv is not None else "")
                   + '</div>'
                   + (f'<div style="font-family:monospace;font-size:9.5px;color:{C["mute"]};">'
                      f'prev {fmt(pd_["prev"])}</div>' if pd_.get("prev") else "")
                   + '</div>')
            if ai.get("sup") or ai.get("res"):
                md(f'<div style="display:flex;gap:10px;background:{C["bg"]};'
                   f'border:1px solid {C["bord"]};border-radius:5px;padding:6px 8px;margin-bottom:6px;">'
                   + (f'<div><div style="font-family:monospace;font-size:8px;color:{C["mute"]};'
                      f'text-transform:uppercase;letter-spacing:.9px;">Supp</div>'
                      f'<div style="font-family:monospace;font-size:11px;font-weight:700;'
                      f'color:{C["green"]};">{ai["sup"]}</div></div>' if ai.get("sup") else "")
                   + (f'<div><div style="font-family:monospace;font-size:8px;color:{C["mute"]};'
                      f'text-transform:uppercase;letter-spacing:.9px;">Res</div>'
                      f'<div style="font-family:monospace;font-size:11px;font-weight:700;'
                      f'color:{C["red"]};">{ai["res"]}</div></div>' if ai.get("res") else "")
                   + '</div>')
            if ai.get("out"):
                st.caption(ai["out"])


def render_heatmaps(prices):
    has_us = any(prices.get(s,{}).get("pct") is not None for s in US_SEC)
    has_in = any(prices.get(s,{}).get("pct") is not None for s in IN_SEC)
    if not has_us and not has_in: return
    sec_hdr("03","Sector Heatmaps","US + India · vs prior close")
    c1, c2 = st.columns(2)
    with c1: sector_heatmap(US_SEC, prices, "🇺🇸  US — S&P 500 GICS Sectors")
    with c2: sector_heatmap(IN_SEC, prices, "🇮🇳  India — NSE Sectoral Indices")


def render_banks(analysis):
    banks = [b for b in (analysis.get("banks") or []) if hd(b.get("rate"))]
    if not banks: return
    sec_hdr("04","Central Banks & Inflation","policy rate · CPI · stance · next meeting")
    ths = ["","Bank","Rate","Prev","Date","CPI","Prev CPI","CPI Date","Stance","Next Mtg"]
    rows = ""
    for i,b in enumerate(banks):
        sc = stance_c(b.get("stance",""))
        rd = round(float(b["rate"])-float(b["rp"]),2) if hd(b.get("rate")) and hd(b.get("rp")) else None
        cd = round(float(b["cpi"])-float(b["cpip"]),2) if hd(b.get("cpi")) and hd(b.get("cpip")) else None
        rows += (
            f'<tr style="background:{"rgba(255,255,255,.01)" if i%2 else "transparent"};">'
            f'<td style="padding:9px 10px;border-bottom:1px solid {C["bord"]};">'
            f'<div style="display:flex;align-items:center;gap:7px;">'
            f'<span style="font-size:18px;">{b.get("f","")}</span>'
            f'<span style="font-size:13px;font-weight:700;color:{C["ink"]};font-family:DM Sans,sans-serif;">'
            f'{b.get("c","")}</span></div></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:11px;color:{C["mute"]};font-family:monospace;">{b.get("bank","")}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:16px;font-weight:800;color:{C["ink"]};font-family:monospace;">'
            f'{b["rate"]+"%"  if hd(b.get("rate")) else "—"}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:1px;">'
            f'<span style="font-size:12px;color:{C["body"]};font-family:monospace;">'
            f'{b["rp"]+"%"  if hd(b.get("rp")) else "—"}</span>'
            + (f'<span style="font-size:10px;color:{"#2dd4a7" if rd<0 else "#f4516c"};font-family:monospace;font-weight:700;">'
               f'{("+" if rd>0 else "")}{rd:.2f}pp</span>' if rd is not None and abs(rd)>.001 else "")
            + f'</div></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:10px;color:{C["mute"]};font-family:monospace;">'
            f'{b.get("rd") or "—"}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<div style="display:flex;align-items:center;justify-content:center;gap:4px;">'
            f'<span style="font-size:14px;font-weight:800;color:{C["ink"]};font-family:monospace;">'
            f'{b["cpi"]+"%"  if hd(b.get("cpi")) else "—"}</span>'
            + (f'<span style="font-size:11px;color:{"#2dd4a7" if cd<0 else "#f4516c"};">{"↓" if cd<0 else "↑"}</span>' if cd is not None else "")
            + f'</div></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:12px;color:{C["body"]};font-family:monospace;">'
            f'{b["cpip"]+"%"  if hd(b.get("cpip")) else "—"}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:10px;color:{C["mute"]};font-family:monospace;">'
            f'{b.get("cpid") or "—"}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-family:monospace;font-size:9.5px;font-weight:700;'
            f'background:{sc}1a;color:{sc};border:1px solid {sc}35;border-radius:4px;'
            f'padding:3px 7px;white-space:nowrap;">{stance_l(b.get("stance",""))}</span></td>'
            f'<td style="padding:9px 10px;text-align:center;border-bottom:1px solid {C["bord"]};">'
            f'<span style="font-size:10px;color:{C["blue"]};font-family:monospace;font-weight:500;">'
            f'{b.get("next") or "—"}</span></td></tr>'
        )
    th_css = (f"padding:7px 10px;text-align:center;font-size:9px;font-family:monospace;"
              f"font-weight:700;color:{C['mute']};letter-spacing:1px;text-transform:uppercase;"
              f"border-bottom:1px solid {C['bord']};background:{C['bg']};white-space:nowrap;")
    th_row = "".join(f'<th style="{th_css}">{h}</th>' for h in ths)
    md(f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
       f'border-radius:11px;overflow:hidden;overflow-x:auto;">'
       f'<table style="width:100%;border-collapse:separate;border-spacing:0;">'
       f'<thead><tr>{th_row}</tr></thead><tbody>{rows}</tbody></table></div>')


def render_picks(analysis):
    picks = [p for p in (analysis.get("picks") or []) if hd(p.get("n"))]
    if not picks: return
    sec_hdr("05","Top Picks & Trade Ideas",f"{len(picks)} ideas")
    cols = st.columns(len(picks))
    for i,p in enumerate(picks):
        db, dc, dbd = dir_s(p.get("dir",""))
        cc = C["green"] if p.get("conv")=="high" else C["amber"] if p.get("conv")=="medium" else C["mute"]
        dots = "".join(f'<div style="width:6px;height:6px;border-radius:50%;background:'
                       f'{cc if j<(3 if p.get("conv")=="high" else 2 if p.get("conv")=="medium" else 1) else C["dim"]};"></div>'
                       for j in range(3))
        dir_txt = "▲ LONG" if p.get("dir")=="long" else "▼ SHORT" if p.get("dir")=="short" else "◆ NEUTRAL"
        tf_badge = (f'<span style="font-family:monospace;font-size:9px;color:{C["mute"]};'
                    f'background:{C["bg"]};border:1px solid {C["bord"]};padding:2px 6px;'
                    f'border-radius:3px;letter-spacing:.4px;">{p["tf"]}</span>' if hd(p.get("tf")) else "")
        with cols[i]:
            md(f'<div style="border-top:3px solid {dc};border:1px solid {C["bord"]};'
               f'border-top:3px solid {dc};border-radius:0 0 10px 10px;'
               f'background:{C["surf"]};padding:14px;">'
               f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:7px;">'
               f'<div style="font-family:\'Instrument Serif\',serif;font-size:18px;'
               f'color:{C["ink"]};font-weight:400;letter-spacing:-.2px;">{p.get("n","")}</div>'
               f'<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;">'
               + (f'<span style="font-family:monospace;font-size:9px;color:{C["mute"]};'
                  f'background:{C["bg"]};border:1px solid {C["bord"]};padding:2px 5px;'
                  f'border-radius:3px;text-transform:uppercase;">{p["reg"]}</span>' if hd(p.get("reg")) else "")
               + (f'<span style="font-family:monospace;font-size:9px;color:{C["blue"]};'
                  f'background:rgba(91,141,239,.06);border:1px solid rgba(91,141,239,.2);'
                  f'padding:2px 5px;border-radius:3px;text-transform:uppercase;">{p["t"]}</span>' if hd(p.get("t")) else "")
               + f'</div></div>'
               f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:7px;align-items:center;">'
               f'<span style="font-family:monospace;font-size:10px;font-weight:700;'
               f'padding:3px 8px;border-radius:4px;background:{db};color:{dc};'
               f'border:1px solid {dbd};">{dir_txt}</span>'
               f'<div style="display:flex;align-items:center;gap:5px;">'
               f'<div style="display:flex;gap:3px;">{dots}</div>'
               f'<span style="font-family:monospace;font-size:9px;color:{C["body"]};'
               f'text-transform:uppercase;font-weight:700;">{p.get("conv","")}</span></div>'
               f'{tf_badge}</div>'
               + (f'<div style="font-size:12.5px;font-weight:700;color:{C["ink"]};'
                  f'margin-bottom:5px;line-height:1.4;">{p["hl"]}</div>' if hd(p.get("hl")) else "")
               + (f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{p["thesis"]}</div>' if hd(p.get("thesis")) else "")
               + '</div>')


def render_geo(analysis):
    geo = [g for g in (analysis.get("geo") or []) if hd(g.get("hl"))]
    if not geo: return
    sec_hdr("06","Hedge Fund Radar",f"{len(geo)} themes")
    cols = st.columns(3)
    for i,g in enumerate(geo):
        uc = urg_c(g.get("urg",""))
        with cols[i%3]:
            md(f'<div style="background:{C["surf"]};border:1px solid {C["bord"]};'
               f'border-radius:10px;padding:13px;margin-bottom:10px;">'
               f'<div style="display:flex;gap:10px;align-items:flex-start;">'
               f'<div style="flex-shrink:0;width:34px;height:34px;display:flex;align-items:center;'
               f'justify-content:center;background:{C["bg"]};border:1px solid {C["bord"]};'
               f'border-radius:7px;font-size:16px;">{g.get("icon","")}</div>'
               f'<div style="flex:1;min-width:0;">'
               f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
               f'<span style="font-family:monospace;font-size:9.5px;font-weight:700;'
               f'letter-spacing:1.2px;color:{C["mute"]};text-transform:uppercase;">{g.get("cat","")}</span>'
               f'<div style="width:7px;height:7px;border-radius:50%;background:{uc};'
               f'box-shadow:0 0 6px {uc};"></div></div>'
               f'<div style="font-size:12.5px;font-weight:700;color:{C["ink"]};'
               f'margin-bottom:4px;line-height:1.4;">{g.get("hl","")}</div>'
               + (f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{g["det"]}</div>'
                  if hd(g.get("det")) else "")
               + '</div></div></div>')

# ─────────────────────────────────────────────────────────────────────────────
#  SETUP PAGE  (shown when no API key)
# ─────────────────────────────────────────────────────────────────────────────
def show_setup():
    st.markdown("<br>", unsafe_allow_html=True)
    _, cc, _ = st.columns([1, 2, 1])
    with cc:
        md(f'<div style="background:{C["surf"]};border:2px solid rgba(45,212,167,.35);'
           f'border-radius:14px;padding:36px;">'
           f'<div style="font-family:\'Instrument Serif\',serif;font-size:50px;color:{C["green"]};'
           f'font-style:italic;text-align:center;margin-bottom:10px;">α</div>'
           f'<div style="font-family:\'Instrument Serif\',serif;font-size:22px;color:{C["ink"]};'
           f'text-align:center;margin-bottom:22px;">One-time setup — 3 steps</div>')

        for n, title, body in [
            ("1", "Get free Gemini API key",
             f'Go to <a href="https://aistudio.google.com" target="_blank" '
             f'style="color:{C["blue"]};font-weight:600;">aistudio.google.com</a> → '
             f'Sign in → <b style="color:{C["ink"]}">Get API key → Create API key</b>. '
             f'Free tier: 15 req/min, 1000 req/day.'),
            ("2", "Open Streamlit Secrets",
             f'In <b style="color:{C["ink"]}">share.streamlit.io</b> → find your app → '
             f'click <b style="color:{C["ink"]}">⋮ (3-dot menu) → Settings → Secrets</b>'),
            ("3", "Paste and Save",
             f'In the Secrets text box, paste:<br>'
             f'<code style="background:{C["bg"]};color:{C["green"]};padding:4px 8px;'
             f'border-radius:4px;font-size:12px;display:block;margin-top:5px;">'
             f'GEMINI_API_KEY = "AIzaSy...your_key..."</code><br>'
             f'Click <b style="color:{C["ink"]}">Save</b>. App restarts. Done.')
        ]:
            md(f'<div style="display:flex;gap:12px;margin-bottom:16px;">'
               f'<div style="background:{C["green"]};color:{C["bg"]};font-family:monospace;'
               f'font-weight:700;font-size:12px;padding:3px 9px;border-radius:4px;'
               f'flex-shrink:0;height:fit-content;margin-top:2px;">{n}</div>'
               f'<div><div style="font-size:14px;font-weight:700;color:{C["ink"]};'
               f'margin-bottom:4px;">{title}</div>'
               f'<div style="font-size:12.5px;color:{C["body"]};line-height:1.65;">{body}</div>'
               f'</div></div>')

        md(f'<div style="background:rgba(45,212,167,.06);border:1px solid rgba(45,212,167,.18);'
           f'border-radius:8px;padding:12px;font-size:11.5px;color:{C["body"]};'
           f'line-height:1.8;margin-top:6px;text-align:center;">'
           f'Free: <b style="color:{C["green"]};">1000 req/day · 1M tokens/day</b> · '
           f'Analysis cached daily · Prices from Yahoo Finance · '
           f'<b style="color:{C["green"]};">Total cost $0</b></div></div>')

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    api_key = st.secrets.get("GEMINI_API_KEY", "").strip()
    now_uae = datetime.now(ZoneInfo("Asia/Dubai"))

    # ── HEADER ───────────────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns([2.2, 4, 1.1, 1.1])
    with h1:
        md(f'<div style="display:flex;align-items:center;gap:11px;padding:10px 0 6px 0;">'
           f'<div style="width:34px;height:34px;border-radius:8px;'
           f'background:linear-gradient(135deg,{C["green"]},{C["blue"]});'
           f'display:flex;align-items:center;justify-content:center;'
           f'font-family:\'Instrument Serif\',serif;font-size:20px;font-style:italic;'
           f'color:{C["bg"]};box-shadow:0 3px 12px rgba(45,212,167,.2);">α</div>'
           f'<div><div style="font-family:\'Instrument Serif\',serif;font-size:22px;'
           f'color:{C["ink"]};letter-spacing:-.4px;line-height:1;">'
           f'Alpha<i style="font-style:italic;color:{C["green"]};">Terminal</i></div>'
           f'<div style="font-family:monospace;font-size:9px;color:{C["mute"]};'
           f'letter-spacing:1.5px;margin-top:1px;">MACRO · COMMODITIES · ALPHA</div>'
           f'</div></div>')
    with h2:
        md(f'<div style="padding:12px 0 0 0;font-family:monospace;font-size:10px;'
           f'color:{C["mute"]};letter-spacing:.4px;">'
           f'{now_uae.strftime("%A, %d %B %Y  %H:%M")} GST &nbsp;·&nbsp; '
           f'All % changes vs prior trading day close &nbsp;·&nbsp; '
           f'Prices: Yahoo Finance (15-min) &nbsp;·&nbsp; Analysis: Gemini (daily cache)</div>')
    with h3:
        refresh_prices = st.button("↺ Prices", use_container_width=True)
    with h4:
        refresh_analysis = st.button("⚡ Analysis", use_container_width=True)

    st.markdown(f"<hr style='border:none;border-top:1px solid {C['bord']};margin:2px 0 10px 0;'>",
                unsafe_allow_html=True)

    # ── HANDLE REFRESHES ─────────────────────────────────────────────────────
    if refresh_prices:
        st.cache_data.clear()
        st.session_state.pop("analysis", None)
        st.rerun()

    if refresh_analysis:
        if CACHE_F.exists():
            CACHE_F.unlink(missing_ok=True)
        st.session_state.pop("analysis", None)
        st.rerun()

    # ── GATE: needs API key ───────────────────────────────────────────────────
    if not api_key:
        show_setup()
        return

    # ── PRICES (fast, always shown first) ────────────────────────────────────
    with st.spinner("Fetching live market prices…"):
        prices = fetch_prices()

    # ── ANALYSIS (file-cached, parallel Gemini fetch) ─────────────────────────
    analysis = None

    # 1. Check file cache
    cached = load_cache()
    if cached:
        analysis = cached
        fetched_label = cached.get("_fetched", "cached")
    else:
        # 2. Check session state (prevents re-fetching on page interactions)
        if st.session_state.get("analysis_date") == TODAY and st.session_state.get("analysis"):
            analysis = st.session_state["analysis"]
            fetched_label = analysis.get("_fetched","session")
        else:
            # 3. Fetch fresh — 4 parallel Gemini calls
            progress_slot = st.empty()
            with progress_slot:
                with st.spinner("🤖 Loading AI analysis — 4 parallel Gemini searches (first load of the day)…"):
                    analysis = fetch_all_analysis(api_key)

            progress_slot.empty()
            if analysis:
                save_cache(analysis)
                st.session_state["analysis"] = analysis
                st.session_state["analysis_date"] = TODAY
            fetched_label = analysis.get("_fetched", "just now") if analysis else "unavailable"

    # Section status banner
    if analysis:
        secs = analysis.get("_sections", {})
        status_parts = []
        for k, label in [("macro","Macro"),("comms","Commodities"),("banks","Banks/Picks"),("geo","Radar")]:
            ok = secs.get(k, True)
            col = C["green"] if ok else C["amber"]
            status_parts.append(f'<span style="color:{col};font-weight:700;">{"✓" if ok else "⚠"} {label}</span>')

        md(f'<div style="font-family:monospace;font-size:9.5px;color:{C["dim"]};'
           f'margin-bottom:10px;">● PRICES: {prices.get("_at","—")} &nbsp;·&nbsp; '
           f'AI: {fetched_label} &nbsp;·&nbsp; '
           + " &nbsp;·&nbsp; ".join(status_parts)
           + '</div>')
    else:
        md(f'<div style="background:rgba(244,81,108,.05);border:1px solid rgba(244,81,108,.2);'
           f'border-radius:8px;padding:11px 14px;margin-bottom:12px;font-size:12px;'
           f'color:{C["body"]};">⚠ AI analysis unavailable — prices and charts are still live. '
           f'Click <b>⚡ Analysis</b> (top right) to retry.</div>')

    # Fetched-at line
    md(f'<div style="font-family:monospace;font-size:9px;color:{C["dim"]};'
       f'margin-bottom:14px;">All % movements are current vs prior trading day regular session close.</div>')

    # ── RENDER ────────────────────────────────────────────────────────────────
    render_hero(prices, analysis)

    if analysis:
        render_macro(analysis)

    render_commodities(prices, analysis)
    render_heatmaps(prices)

    if analysis:
        render_banks(analysis)
        render_picks(analysis)
        render_geo(analysis)

    md(f'<div style="padding:14px 0;border-top:1px solid {C["bord"]};margin-top:26px;'
       f'font-size:9.5px;color:{C["dim"]};font-family:monospace;'
       f'display:flex;justify-content:space-between;flex-wrap:wrap;gap:5px;">'
       f'<span>ALPHA TERMINAL · PRICES: YAHOO FINANCE (FREE) · ANALYSIS: GEMINI (FREE TIER)</span>'
       f'<span>NOT FINANCIAL ADVICE · INFORMATIONAL PURPOSES ONLY</span></div>')


if __name__ == "__main__":
    main()
