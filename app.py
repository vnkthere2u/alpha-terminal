"""
AlphaTerminal — Institutional Macro Dashboard v10 (Updated SDK)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prices       → yfinance (15-min cache)
CB rates/CPI → FRED API (Federal Reserve Economic Data - Unbreakable)
Analysis     → Gemini 1.5 Pro (Using new google-genai SDK)
UI           → 100% native Streamlit
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import json
import logging
import warnings
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict
import time

# Essential API Integrations
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

# ─── PALETTE & CSS ────────────────────────────────────────────────────────────
G = "#2dd4a7"; R = "#f4516c"; A = "#f0b429"; B = "#5b8def"; P = "#a78bfa"
BG = "#08090d"; SF = "#111318"; BD = "#1e2636"
INK = "#eaeef5"; BODY = "#99adc0"; MUT = "#4a5e72"

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
</style>""", unsafe_allow_html=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
AED_PEG  = 3.6725
TROY_OZ  = 31.1035
TODAY    = str(date.today())

FRED_SERIES = {
    "US": {"rate": "FEDFUNDS", "cpi": "CPIAUCSL", "bank": "Fed", "flag": "🇺🇸"},
    "Euro": {"rate": "ECBDFR", "cpi": "CP0000EZ19M086NES", "bank": "ECB", "flag": "🇪🇺"},
    "Japan": {"rate": "INTDSRJPM193N", "cpi": "JPNCPIALLMINMEI", "bank": "BOJ", "flag": "🇯🇵"},
    "India": {"rate": "INTDSRINM193N", "cpi": "INDCPIALLMINMEI", "bank": "RBI", "flag": "🇮🇳"},
    "China": {"rate": "INTDSRCNM193N", "cpi": "CHNCPIALLMINMEI", "bank": "PBOC", "flag": "🇨🇳"}
}

# ─── AI SYSTEM PROMPTS (GEMINI 1.5 PRO) ───────────────────────────────────────
SYSTEM_PROMPT = """You are a senior macro strategist, geopolitical analyst, and hedge fund portfolio manager. 
Analyze the provided live market data, yield curves, and central bank data. Do not recite numbers; explain the structural shifts and second-order effects.
You must return a raw JSON object with the following exact structure (no markdown, no code blocks):
{
  "mood": {"score": 50, "label": "Risk-On/Cautious/Risk-Off/Volatile", "regime": "Brief regime description", "summary": "2-3 sentences on primary risks", "primary_risk": "One sentence risk"},
  "macro": [{"country": "US", "flag": "🇺🇸", "headline": "Sharp headline", "analysis": "Deep macro analysis", "sentiment": "bullish/bearish/neutral", "key_signal": "Metric to watch", "contrarian": "Contrarian view", "cb_note": "Central bank positioning"}],
  "commodities": [{"name": "Gold", "signal": "buy/sell/hold", "support": 0, "resistance": 0, "analysis": "Technical + Macro thesis", "positioning_note": "Fund positioning"}],
  "picks": [{"type": "Stock/ETF", "name": "Ticker", "region": "US/India", "direction": "long/short", "conviction": "high/medium", "timeframe": "1M/3M/6M", "headline": "Trade thesis", "thesis": "Detailed logic", "risk": "Downside risk"}],
  "geo": [{"category": "Geopolitics/Fed Watch", "icon": "🌍", "headline": "Theme headline", "analysis": "Market impact", "urgency": "high/medium/low"}]
}
Ensure the 'macro' array contains exactly 5 objects for US, India, China, Japan, Euro.
Ensure the 'commodities' array contains exactly 5 objects for Gold, Silver, Crude Oil, Copper, Natural Gas.
Generate 4 'picks' and 4 'geo' themes."""

# ─── DATA FETCHING: YFINANCE ──────────────────────────────────────────────────
def _yf_ticker(sym: str) -> dict:
    try:
        hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        if hist.empty: return {}
        cl = hist["Close"].dropna()
        if len(cl) < 1: return {}
        last = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) >= 2 else None
        pct  = ((last - prev) / prev * 100) if prev and prev != 0 else None
        return {"price": last, "prev": prev, "pct": pct, "date": str(cl.index[-1].date())}
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
        "USDINR=X": "USDINR", "^TNX": "US10Y", "^IRX": "US3M",
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
        
    if "USDINR" in results:
        usd_inr = results["USDINR"]["price"]
        results["AEDIINR"] = {
            "price": usd_inr / AED_PEG,
            "prev": results["USDINR"].get("prev", usd_inr) / AED_PEG,
            "pct": results["USDINR"].get("pct"),
        }
    if "Gold" in results:
        gp = results["Gold"]["price"]
        pp = results["Gold"].get("prev", gp)
        results["GoldAED"] = {
            "price": gp / TROY_OZ * AED_PEG,
            "prev": pp / TROY_OZ * AED_PEG,
            "pct": results["Gold"].get("pct"),
        }
    results["_fetched"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y  %H:%M %Z")
    return results

@st.cache_data(ttl=900, show_spinner=False)
def fetch_commodity_ohlc() -> dict:
    symbols = {"Gold": "GC=F", "Crude Oil": "CL=F", "Silver": "SI=F", "Copper": "HG=F", "Natural Gas": "NG=F"}
    ohlc = {}
    for name, sym in symbols.items():
        hist = yf.Ticker(sym).history(period="5d")
        if not hist.empty:
            df = hist[["Open","High","Low","Close"]].tail(3)
            ohlc[name] = df.reset_index().to_dict(orient="records")
            for rec in ohlc[name]: rec["Date"] = str(rec["Date"].date())
    return ohlc

# ─── DATA FETCHING: FRED API ──────────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fred_macro(api_key: str) -> dict:
    if not api_key: return {}
    fred = Fred(api_key=api_key)
    macro_data = {}
    
    for country, params in FRED_SERIES.items():
        try:
            rate_data = fred.get_series(params["rate"], observation_start=datetime.now() - timedelta(days=90))
            cpi_data = fred.get_series(params["cpi"], observation_start=datetime.now() - timedelta(days=400))
            
            curr_rate = rate_data.iloc[-1] if not rate_data.empty else 0.0
            prev_rate = rate_data.iloc[-2] if len(rate_data) > 1 else curr_rate
            
            if len(cpi_data) >= 12:
                cpi_yoy = ((cpi_data.iloc[-1] - cpi_data.iloc[-13]) / cpi_data.iloc[-13]) * 100
                prev_cpi_yoy = ((cpi_data.iloc[-2] - cpi_data.iloc[-14]) / cpi_data.iloc[-14]) * 100
            else:
                cpi_yoy = prev_cpi_yoy = 0.0

            macro_data[country] = {
                "bank": params["bank"],
                "flag": params["flag"],
                "rate": round(curr_rate, 2),
                "rp": round(prev_rate, 2),
                "rd": rate_data.index[-1].strftime("%b %Y") if not rate_data.empty else "—",
                "cpi": round(cpi_yoy, 2),
                "cpip": round(prev_cpi_yoy, 2),
                "cpid": cpi_data.index[-1].strftime("%b %Y") if not cpi_data.empty else "—",
                "stance": "hike" if curr_rate > prev_rate else ("cut" if curr_rate < prev_rate else "hold")
            }
        except Exception as e:
            macro_data[country] = {"bank": params["bank"], "flag": params["flag"], "rate": 0, "rp": 0, "cpi": 0, "cpip": 0, "stance": "hold"}
    return macro_data

# ─── AI ENGINE (UPDATED GOOGLE-GENAI SDK) ─────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def run_full_analysis(gemini_key: str, prices: dict, macro: dict, ohlc: dict) -> dict:
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    Live Prices & Yields: {json.dumps(prices)}
    Central Bank & Inflation Data: {json.dumps(macro)}
    Commodity OHLC Data: {json.dumps(ohlc)}
    Generate the comprehensive analysis based on these latest figures.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-1.5-pro',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"AI Generation Failed: {e}")
        return {}

# ─── PLOTLY CHARTS ────────────────────────────────────────────────────────────
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
            lbls.append(lbl); vals.append(v)
            cols.append(G if v >= 0 else R)
            tips.append(f"<b>{lbl}</b><br>Now: {d.get('price', 0):,.2f}<br>Change: {v:.2f}%")
            
    fig = go.Figure(go.Bar(
        x=vals, y=lbls, orientation="h", marker_color=cols, opacity=0.88,
        text=[f"{'+'if v>=0 else ''}{v:.2f}%" for v in vals], textposition="outside",
        textfont={"color": BODY, "size": 10}, customdata=tips, hovertemplate="%{customdata}<extra></extra>"
    ))
    fig.update_layout(
        height=max(310, len(lbls)*27), margin=dict(l=0,r=60,t=4,b=4),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=BD, zerolinecolor=BD),
        yaxis=dict(showgrid=False, automargin=True), bargap=0.33
    )
    return fig

def chart_yields(prices: dict) -> go.Figure:
    YIELD_ROWS = [("🇺🇸 US", "US10Y", "US3M"), ("🇩🇪 Germany", "DE10Y", None), ("🇯🇵 Japan", "JP10Y", None)]
    lbls, y10, y2 = [], [], []
    for lbl, sym10, sym2 in YIELD_ROWS:
        d10 = prices.get(sym10, {})
        if not d10.get("price"): continue
        lbls.append(lbl); y10.append(round(d10["price"], 2))
        y2.append(round(prices.get(sym2, {}).get("price", 0), 2) if sym2 and prices.get(sym2, {}).get("price") else None)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=y10, y=lbls, orientation="h", name="10Y", marker_color=P, text=[f"{v:.2f}%" for v in y10], textposition="outside"))
    if any(y2): fig.add_trace(go.Bar(x=[v or 0 for v in y2], y=lbls, orientation="h", name="3M", marker_color=B, text=[f"{v:.2f}%" if v else "" for v in y2], textposition="outside", opacity=0.65))
    fig.update_layout(barmode="overlay", height=180, margin=dict(l=0,r=55,t=4,b=4), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", yaxis=dict(automargin=True), legend=dict(orientation="h", y=-0.12))
    return fig

def chart_treemap(sector_prices: dict, title: str) -> Optional[go.Figure]:
    names, vals, pcts, texts = [], [], [], []
    for name, d in sector_prices.items():
        if d.get("pct") is not None:
            v = round(d["pct"], 2)
            names.append(name); vals.append(abs(v)+0.05); pcts.append(v)
            texts.append(f"{'+'if v>=0 else ''}{v:.2f}%")
    if not names: return None
    fig = go.Figure(go.Treemap(
        labels=names, parents=[""]*len(names), values=vals, text=texts,
        marker=dict(colors=pcts, colorscale=[[0,R],[.5,BG],[1,G]], cmid=0, line=dict(width=1.5, color=BG)),
        textinfo="label+text"
    ))
    fig.update_layout(title=dict(text=title, font={"color":MUT,"size":11}, x=0), height=265, margin=dict(l=0,r=0,t=28,b=0), paper_bgcolor="rgba(0,0,0,0)")
    return fig

# ─── UI HELPERS & RENDERERS ───────────────────────────────────────────────────
def hd(v): return v is not None and v != ""
def fmt_num(v): return f"{v:,.2f}" if v else "—"
def pct_str(v): return f"{'+'if v>=0 else ''}{v:.2f}%" if v else "—"
def sent_color(s): return G if str(s).lower() == "bullish" else R if str(s).lower() == "bearish" else A
def sig_color(s): return G if str(s).lower() == "buy" else R if str(s).lower() == "sell" else A
def dir_color(d): return G if str(d).lower() == "long" else R
def section_title(n, title, sub=""): st.markdown(f'<div style="display:flex;align-items:baseline;gap:10px;margin:1.6rem 0 .8rem 0;padding-bottom:8px;border-bottom:1px solid {BD};"><span style="font-size:10px;color:{MUT};">{n}</span><span style="font-size:1.5rem;font-weight:800;color:{INK};">{title}</span><div style="flex:1;height:1px;background:{BD};"></div><span style="font-size:9px;color:{MUT};">{sub}</span></div>', unsafe_allow_html=True)
def color_bar(col): st.markdown(f'<div style="height:3px;background:{col};border-radius:2px;margin-bottom:8px;"></div>', unsafe_allow_html=True)
def mini_badge(txt, col): st.markdown(f'<span style="font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;background:{col}22;color:{col};border:1px solid {col}44;">{txt}</span>', unsafe_allow_html=True)

def render_hero(prices, analysis):
    mood = analysis.get("mood", {}) if analysis else {}
    c1, c2, c3 = st.columns([1.15, 1.55, 1.3])
    with c1:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};text-transform:uppercase;margin:0;">Market Mood</p>', unsafe_allow_html=True)
            st.markdown(chart_gauge(int(mood.get("score", 50))), unsafe_allow_html=True)
            st.markdown(f'<div style="text-align:center;margin:4px 0;"><b>{mood.get("label", "—")}</b></div>', unsafe_allow_html=True)
            if hd(mood.get("summary")): st.caption(mood["summary"])
            st.divider()
            sc1, sc2 = st.columns(2)
            with sc1:
                st.metric("VIX", fmt_num(prices.get("VIX",{}).get("price")), delta=pct_str(prices.get("VIX",{}).get("pct")), delta_color="inverse")
                st.metric("AED/INR", fmt_num(prices.get("AEDIINR",{}).get("price")), delta=pct_str(prices.get("AEDIINR",{}).get("pct")))
            with sc2:
                st.metric("DXY", fmt_num(prices.get("DXY",{}).get("price")), delta=pct_str(prices.get("DXY",{}).get("pct")), delta_color="inverse")
                st.metric("Gold/g (AED)", fmt_num(prices.get("GoldAED",{}).get("price")), delta=pct_str(prices.get("GoldAED",{}).get("pct")))
    with c2:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};">Asset Performance</p>', unsafe_allow_html=True)
            st.plotly_chart(chart_asset_bars(prices), config={"displayModeBar": False})
    with c3:
        with st.container(border=True):
            st.markdown(f'<p style="font-size:9px;color:{MUT};">Sovereign Yields</p>', unsafe_allow_html=True)
            st.plotly_chart(chart_yields(prices), config={"displayModeBar": False})
            if hd(mood.get("primary_risk")): st.warning(f"⚠ **Risk:** {mood['primary_risk']}")

def render_macro(analysis):
    items = analysis.get("macro", [])
    if not items: return
    section_title("01", "Global Macro", "FRED Data + Gemini AI")
    cols = st.columns(len(items))
    for i, m in enumerate(items):
        sc = sent_color(m.get("sentiment",""))
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                st.markdown(f'<b>{m.get("flag","")} {m.get("country","")}</b>', unsafe_allow_html=True)
                if hd(m.get("key_signal")): st.markdown(f'<p style="font-size:9.5px;color:{B};">{m["key_signal"]}</p>', unsafe_allow_html=True)
                st.markdown(f"**{m.get('headline','')}**")
                st.markdown(m.get("analysis",""))
                if hd(m.get("cb_note")): st.info(f"🏦 {m['cb_note']}")

def render_commodities(prices, analysis):
    comms_ai = {c["name"]: c for c in analysis.get("commodities", [])}
    SYM_MAP = [("Gold","Gold","XAU"), ("Crude","Crude Oil","WTI"), ("Silver","Silver","XAG"), ("Copper","Copper","HG"), ("NatGas","Natural Gas","NG")]
    section_title("02", "Commodities", "Live prices · AI Analysis")
    cols = st.columns(5)
    for i, (pkey, name, tkr) in enumerate(SYM_MAP):
        pd_ = prices.get(pkey, {})
        ai = comms_ai.get(name, {})
        sc = sig_color(ai.get("signal",""))
        with cols[i]:
            with st.container(border=True):
                color_bar(sc)
                st.markdown(f'<b>{name}</b>', unsafe_allow_html=True)
                if ai.get("signal"): mini_badge(ai.get("signal", "").upper(), sc)
                if pd_.get("price"): st.metric("", value=fmt_num(pd_["price"]), delta=pct_str(pd_.get("pct")))
                if hd(ai.get("analysis")): st.caption(ai["analysis"])

def render_picks_and_geo(analysis):
    picks = analysis.get("picks", [])
    geo = analysis.get("geo", [])
    
    if picks:
        section_title("03", "Trade Ideas", "AI-generated")
        cols = st.columns(len(picks))
        for i, p in enumerate(picks):
            dc = dir_color(p.get("direction",""))
            with cols[i]:
                with st.container(border=True):
                    color_bar(dc)
                    st.markdown(f'<b>{p.get("name","")}</b> ({p.get("type","")})', unsafe_allow_html=True)
                    st.markdown(f"**{p.get('headline','')}**")
                    if hd(p.get("thesis")): st.caption(p["thesis"])
                    
    if geo:
        section_title("04", "Hedge Fund Radar", "Macro Themes")
        cols = st.columns(min(len(geo), 4))
        for i, g in enumerate(geo[:4]):
            with cols[i]:
                with st.container(border=True):
                    st.markdown(f'{g.get("icon","")} <b>{g.get("category","")}</b>', unsafe_allow_html=True)
                    st.markdown(f"**{g.get('headline','')}**")
                    if hd(g.get("analysis")): st.caption(g["analysis"])

# ─── SETUP SCREEN ─────────────────────────────────────────────────────────────
def render_setup():
    st.markdown("<br><br><h2 style='text-align:center;'>AlphaTerminal Setup</h2>", unsafe_allow_html=True)
    st.info("Please add `GEMINI_API_KEY` and `FRED_API_KEY` to your Streamlit Community Cloud Secrets.")

# ─── MAIN APP ROUTING ─────────────────────────────────────────────────────────
def main():
    gemini_key = st.secrets.get("GEMINI_API_KEY", "").strip()
    fred_key = st.secrets.get("FRED_API_KEY", "").strip()

    h1, h2, h3 = st.columns([6, 1, 1])
    with h1: st.markdown(f'<div style="font-size:20px;font-weight:900;">AlphaTerminal</div>', unsafe_allow_html=True)
    with h2:
        if st.button("↺ Data", use_container_width=True): fetch_prices.clear(); fetch_fred_macro.clear(); st.rerun()
    with h3:
        if st.button("⚡ AI Sync", use_container_width=True): run_full_analysis.clear(); st.rerun()
    st.divider()

    if not gemini_key or not fred_key:
        render_setup()
        return

    with st.spinner("Fetching Live Prices & FRED Data..."):
        prices = fetch_prices()
        macro = fetch_fred_macro(fred_key)
        ohlc = fetch_commodity_ohlc()

    analysis = None
    with st.spinner("Gemini 1.5 Pro analyzing macro structures..."):
        analysis = run_full_analysis(gemini_key, prices, macro, ohlc)

    if analysis:
        render_hero(prices, analysis)
        render_macro(analysis)
        render_commodities(prices, analysis)
        render_picks_and_geo(analysis)
    else:
        st.error("AI Analysis failed to load. Try clicking '⚡ AI Sync'.")

if __name__ == "__main__":
    main()
