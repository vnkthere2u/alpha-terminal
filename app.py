"""
AlphaTerminal — Institutional Macro Dashboard v12 (Strict Data Integrity)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prices       → yfinance (15-min cache)
Macro/Yields → FRED API (Unbreakable Central Bank, CPI, and Int'l Yields)
Analysis     → Gemini 2.5 Flash (Strict Anti-Hallucination rules)
UI           → Streamlit (Slate Theme, Infographic Charts)
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

# ─── PROFESSIONAL SLATE PALETTE & CSS ─────────────────────────────────────────
G = "#10b981"; R = "#ef4444"; A = "#f59e0b"; B = "#3b82f6"; P = "#8b5cf6"
BG = "#0f172a"; SF = "#1e293b"; BD = "#334155"
INK = "#f8fafc"; BODY = "#cbd5e1"; MUT = "#94a3b8"

st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600;700&display=swap');
html,body,[class*="css"],.stApp{{background:{BG}!important;font-family:'Inter',sans-serif!important}}
#MainMenu,footer,header,.stDeployButton{{visibility:hidden;display:none}}
div[data-testid="stSidebar"]{{display:none!important}}
.main .block-container{{padding-top:1rem;max-width:1500px;padding-left:2rem;padding-right:2rem}}
.stButton>button{{background:{B}!important;color:{INK}!important;font-weight:600!important;
  border:none!important;border-radius:6px!important;font-size:13px!important;
  box-shadow:0 2px 10px rgba(59,130,246,.2)!important;transition:all .2s!important}}
.stButton>button:hover{{box-shadow:0 4px 15px rgba(59,130,246,.4)!important;transform:translateY(-1px)!important}}
div[data-testid="metric-container"]{{background:{SF}!important;border:1px solid {BD}!important;
  border-radius:10px!important;padding:12px 16px!important;box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1)!important;}}
div[data-testid="metric-container"] label{{color:{MUT}!important;
  font-family:'JetBrains Mono',monospace!important;font-size:10px!important;letter-spacing:1px!important; text-transform: uppercase;}}
div[data-testid="stMetricValue"]{{color:{INK}!important;font-family:'JetBrains Mono',monospace!important;
  font-size:20px!important;font-weight:700!important}}
div[data-testid="stMetricDelta"]{{font-family:'JetBrains Mono',monospace!important;font-size:12px!important}}
div[data-testid="stMarkdownContainer"] p{{color:{BODY}!important}}
.stSpinner>div{{border-top-color:{B}!important}}
hr{{border-color:{BD}!important;margin:8px 0!important}}
.stAlert{{border-radius:8px!important; border: 1px solid {BD}!important; background: {SF}!important;}}
</style>""", unsafe_allow_html=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
AED_PEG  = 3.6725
TROY_OZ  = 31.1035
CURRENT_DATE = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%B %d, %Y")

FRED_SERIES = {
    "US": {"rate": "FEDFUNDS", "cpi": "CPIAUCSL", "yield": "IRLTLT01USM156N", "bank": "Fed", "flag": "🇺🇸"},
    "Euro": {"rate": "ECBDFR", "cpi": "CP0000EZ19M086NES", "yield": "IRLTLT01EZM156N", "bank": "ECB", "flag": "🇪🇺"},
    "UK": {"rate": "BOERUDG", "cpi": "GBRCPIALLMINMEI", "yield": "IRLTLT01GBM156N", "bank": "BOE", "flag": "🇬🇧"},
    "Japan": {"rate": "INTDSRJPM193N", "cpi": "JPNCPIALLMINMEI", "yield": "IRLTLT01JPM156N", "bank": "BOJ", "flag": "🇯🇵"},
    "India": {"rate": "INTDSRINM193N", "cpi": "INDCPIALLMINMEI", "yield": "INDIRLTLT01STM", "bank": "RBI", "flag": "🇮🇳"},
    "China": {"rate": "INTDSRCNM193N", "cpi": "CHNCPIALLMINMEI", "yield": "CHNIRLTLT01STM", "bank": "PBOC", "flag": "🇨🇳"}
}

# ─── AI SYSTEM PROMPTS (DYNAMIC DATE INJECTION) ───────────────────────────────
SYSTEM_PROMPT = f"""You are a senior macro strategist. Today's exact date is {CURRENT_DATE}. 
Analyze the provided live market data and central bank stats.

CRITICAL INSTRUCTION FOR 'next_meeting': 
Do NOT output stale 2024 or 2025 dates. If you do not have high-confidence knowledge of the upcoming central bank meeting date relative to {CURRENT_DATE}, you MUST output "TBD". Do not guess or estimate.

Return ONLY valid JSON:
{{
  "mood": {{"score": 50, "label": "Risk-On/Risk-Off", "regime": "Current market regime", "primary_risk": "One sentence risk"}},
  "macro": [{{"country": "US", "flag": "🇺🇸", "next_meeting": "Month DD or TBD", "headline": "Macro insight", "analysis": "Deep analysis of yields and rates", "sentiment": "bullish/bearish/neutral"}}],
  "commodities": [{{"name": "Gold", "signal": "buy/sell/hold", "analysis": "Technical + Macro thesis"}}],
  "picks": [{{"type": "Stock/ETF", "name": "Ticker", "region": "US/India", "direction": "long/short", "timeframe": "1M/3M", "headline": "Thesis summary", "thesis": "Logic"}}],
  "geo": [{{"category": "Geopolitics", "icon": "🌍", "headline": "Theme headline", "analysis": "Market impact"}}]
}}
Ensure exactly 6 objects in 'macro' (US, Euro, UK, Japan, India, China) and 5 in 'commodities'."""

# ─── DATA FETCHING: YFINANCE ──────────────────────────────────────────────────
def _yf_ticker(sym: str) -> dict:
    try:
        hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        if hist.empty: return {}
        cl = hist["Close"].dropna()
        if len(cl) < 1: return {}
        last = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) >= 2 else last
        pct  = ((last - prev) / prev * 100) if prev else 0
        return {"price": last, "prev": prev, "pct": pct}
    except Exception:
        return {}

@st.cache_data(ttl=900, show_spinner=False)
def fetch_prices() -> dict:
    syms = {
        "^GSPC": "SP500", "^IXIC": "Nasdaq", "^DJI": "Dow",
        "^NSEI": "Nifty50","^BSESN": "Sensex",
        "GC=F": "Gold", "CL=F": "Crude", "SI=F": "Silver", "HG=F": "Copper","NG=F": "NatGas",
        "DX-Y.NYB": "DXY", "^VIX": "VIX", "USDINR=X": "USDINR", 
        "^TNX": "US10Y", "^IRX": "US3M"
    }
    results = {key: _yf_ticker(sym) for sym, key in syms.items()}
    
    if "USDINR" in results and results["USDINR"].get("price"):
        usd_inr = results["USDINR"]["price"]
        results["AEDIINR"] = {
            "price": usd_inr / AED_PEG,
            "prev": results["USDINR"].get("prev", usd_inr) / AED_PEG,
            "pct": results["USDINR"].get("pct"),
        }
    return results

@st.cache_data(ttl=900, show_spinner=False)
def fetch_commodity_ohlc() -> dict:
    symbols = {"Gold": "GC=F", "Crude Oil": "CL=F", "Silver": "SI=F", "Copper": "HG=F", "Natural Gas": "NG=F"}
    ohlc = {}
    for name, sym in symbols.items():
        hist = yf.Ticker(sym).history(period="3d")
        if not hist.empty:
            df = hist[["Open","High","Low","Close"]]
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
            yield_data = fred.get_series(params["yield"], observation_start=datetime.now() - timedelta(days=90))
            
            curr_rate = rate_data.iloc[-1] if not rate_data.empty else 0.0
            prev_rate = rate_data.iloc[-2] if len(rate_data) > 1 else curr_rate
            
            if len(cpi_data) >= 13:
                cpi_yoy = ((cpi_data.iloc[-1] - cpi_data.iloc[-13]) / cpi_data.iloc[-13]) * 100
                prev_cpi_yoy = ((cpi_data.iloc[-2] - cpi_data.iloc[-14]) / cpi_data.iloc[-14]) * 100
            else:
                cpi_yoy = prev_cpi_yoy = 0.0

            curr_yield = yield_data.iloc[-1] if not yield_data.empty else None

            macro_data[country] = {
                "bank": params["bank"],
                "flag": params["flag"],
                "rate": round(curr_rate, 2), "rp": round(prev_rate, 2),
                "cpi": round(cpi_yoy, 2), "cpip": round(prev_cpi_yoy, 2),
                "yield": round(curr_yield, 2) if curr_yield else None,
            }
        except Exception as e:
            macro_data[country] = {"bank": params["bank"], "flag": params["flag"], "rate": 0, "rp": 0, "cpi": 0, "cpip": 0, "yield": None}
    return macro_data

# ─── AI ENGINE (GEMINI 2.5 FLASH) ─────────────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def run_full_analysis(gemini_key: str, prices: dict, macro: dict, ohlc: dict) -> dict:
    client = genai.Client(api_key=gemini_key)
    prompt = f"Live Prices: {json.dumps(prices)}\nMacro Data: {json.dumps(macro)}\nOHLC Data: {json.dumps(ohlc)}\nGenerate comprehensive macro analysis."
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,  # Lowered temperature for stricter JSON adherence
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"AI Generation Failed: {e}")
        return {}

# ─── PLOTLY CHARTS (INFOGRAPHIC STYLE) ────────────────────────────────────────
def chart_macro_comparison(macro_data: dict) -> go.Figure:
    countries = list(macro_data.keys())
    rates = [d["rate"] for d in macro_data.values()]
    cpis = [d["cpi"] for d in macro_data.values()]
    flags = [d["flag"] for d in macro_data.values()]
    x_labels = [f"{f} {c}" for f, c in zip(flags, countries)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=x_labels, y=rates, name='CB Rate %', marker_color=B, text=[f"{v:.1f}%" for v in rates], textposition='auto'
    ))
    fig.add_trace(go.Bar(
        x=x_labels, y=cpis, name='Inflation %', marker_color=A, text=[f"{v:.1f}%" for v in cpis], textposition='auto'
    ))

    fig.update_layout(
        barmode='group', height=280, margin=dict(l=0, r=0, t=30, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color=BODY)),
        xaxis=dict(showgrid=False, tickfont=dict(color=INK)),
        yaxis=dict(showgrid=True, gridcolor=BD, tickfont=dict(color=MUT))
    )
    return fig

def chart_asset_bars(prices: dict) -> go.Figure:
    DISPLAY = [
        ("SP500","S&P 500"),("Nasdaq","Nasdaq"),("Nifty50","Nifty 50"),
        ("Gold","Gold"),("Crude","WTI Crude"),("Bitcoin","Bitcoin"),("DXY","DXY"),
    ]
    lbls, vals, cols, tips = [], [], [], []
    for key, lbl in DISPLAY:
        d = prices.get(key, {})
        if d.get("pct") is not None:
            v = round(d["pct"], 2)
            lbls.append(lbl); vals.append(v)
            cols.append(G if v >= 0 else R)
            tips.append(f"<b>{lbl}</b><br>Now: {d.get('price', 0):,.2f}<br>Prev: {d.get('prev', 0):,.2f}<br>Change: {v:.2f}%")
            
    fig = go.Figure(go.Bar(
        x=vals, y=lbls, orientation="h", marker_color=cols, opacity=0.9,
        text=[f"{'+'if v>=0 else ''}{v:.2f}%" for v in vals], textposition="outside",
        textfont={"color": BODY, "size": 11}, customdata=tips, hovertemplate="%{customdata}<extra></extra>"
    ))
    fig.update_layout(
        height=280, margin=dict(l=0,r=50,t=0,b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=BD, zerolinecolor=BD),
        yaxis=dict(showgrid=False, tickfont=dict(color=INK)), bargap=0.4
    )
    return fig

def chart_yields(prices: dict, macro: dict) -> go.Figure:
    us_10 = prices.get("US10Y", {}).get("price", 0)
    YIELD_DATA = [
        ("🇺🇸 US 10Y", us_10),
        ("🇬🇧 UK 10Y", macro.get("UK", {}).get("yield")),
        ("🇩🇪 GER 10Y", macro.get("Euro", {}).get("yield")),
        ("🇯🇵 JPN 10Y", macro.get("Japan", {}).get("yield")),
        ("🇮🇳 IND 10Y", macro.get("India", {}).get("yield")),
        ("🇨🇳 CHN 10Y", macro.get("China", {}).get("yield"))
    ]
    
    lbls = [row[0] for row in YIELD_DATA if row[1] is not None]
    vals = [round(row[1], 2) for row in YIELD_DATA if row[1] is not None]

    fig = go.Figure(go.Bar(
        x=vals, y=lbls, orientation="h", marker_color=P, opacity=0.8,
        text=[f"{v:.2f}%" for v in vals], textposition="outside",
        textfont={"color": BODY, "size": 11}
    ))
    fig.update_layout(
        height=280, margin=dict(l=0,r=50,t=0,b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=BD), yaxis=dict(showgrid=False, tickfont=dict(color=INK))
    )
    return fig

# ─── UI RENDERERS ─────────────────────────────────────────────────────────────
def section_title(icon, title, sub=""): 
    st.markdown(f'<div style="display:flex;align-items:center;gap:12px;margin:2rem 0 1rem 0;border-bottom:1px solid {BD};padding-bottom:10px;"><span style="font-size:24px;">{icon}</span><span style="font-size:1.6rem;font-weight:700;color:{INK};">{title}</span><span style="font-size:12px;color:{MUT};margin-left:auto;">{sub}</span></div>', unsafe_allow_html=True)

def render_top_row(prices, analysis, macro):
    mood = analysis.get("mood", {}) if analysis else {}
    c1, c2, c3 = st.columns([1, 1, 1])
    
    with c1:
        with st.container(border=True):
            st.markdown(f'<p style="color:{MUT}; font-weight:600; font-size:12px;">MARKET MOOD & FX</p>', unsafe_allow_html=True)
            sc1, sc2 = st.columns(2)
            with sc1:
                st.metric("VIX Volatility", f"{prices.get('VIX',{}).get('price', 0):.2f}")
                st.metric("AED/INR Peg", f"{prices.get('AEDIINR',{}).get('price', 0):.4f}")
            with sc2:
                st.metric("DXY Dollar Idx", f"{prices.get('DXY',{}).get('price', 0):.2f}")
                st.markdown(f"**Regime:** {mood.get('label', 'Neutral')}<br><span style='color:{R}; font-size:12px;'>Risk: {mood.get('primary_risk', '')}</span>", unsafe_allow_html=True)

    with c2:
        with st.container(border=True):
            st.markdown(f'<p style="color:{MUT}; font-weight:600; font-size:12px;">GLOBAL YIELDS (10Y)</p>', unsafe_allow_html=True)
            st.plotly_chart(chart_yields(prices, macro), use_container_width=True, config={"displayModeBar": False})

    with c3:
        with st.container(border=True):
            st.markdown(f'<p style="color:{MUT}; font-weight:600; font-size:12px;">ASSET PERFORMANCE</p>', unsafe_allow_html=True)
            st.plotly_chart(chart_asset_bars(prices), use_container_width=True, config={"displayModeBar": False})

def render_macro_section(analysis, macro_data):
    section_title("🏦", "Global Central Banks & Inflation", "Current & Previous Prints via FRED")
    
    c1, c2 = st.columns([1.5, 2])
    with c1:
        st.plotly_chart(chart_macro_comparison(macro_data), use_container_width=True, config={"displayModeBar": False})
    
    with c2:
        ai_macro = {m.get("country", ""): m for m in analysis.get("macro", [])}
        rows = []
        for country, data in macro_data.items():
            ai_data = ai_macro.get(country, {})
            rows.append({
                "Bank": f"{data['flag']} {data['bank']}",
                "Rate": f"{data['rate']:.2f}%",
                "Prev Rate": f"{data['rp']:.2f}%",
                "CPI (YoY)": f"{data['cpi']:.1f}%",
                "Prev CPI": f"{data['cpip']:.1f}%",
                "Next Meeting": ai_data.get("next_meeting", "TBD")
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

def render_ai_insights(analysis):
    section_title("🧠", "Macro Strategist Insights", "Powered by Gemini 2.5 Flash")
    macro_items = analysis.get("macro", [])
    
    cols = st.columns(3)
    for i, m in enumerate(macro_items):
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{m.get('flag', '')} {m.get('country', '')}**: {m.get('headline', '')}")
                st.caption(m.get('analysis', ''))

# ─── MAIN APP ROUTING ─────────────────────────────────────────────────────────
def main():
    gemini_key = st.secrets.get("GEMINI_API_KEY", "").strip()
    fred_key = st.secrets.get("FRED_API_KEY", "").strip()

    st.markdown(f'<div style="font-size:28px;font-weight:800;color:{INK}; margin-bottom:1rem;">AlphaTerminal <span style="color:{B}; font-size:14px; vertical-align:middle;">PRO</span></div>', unsafe_allow_html=True)

    if not gemini_key or not fred_key:
        st.error("Please add `GEMINI_API_KEY` and `FRED_API_KEY` to Streamlit Secrets.")
        return

    with st.spinner("Fetching Live Market & FRED Data..."):
        prices = fetch_prices()
        macro = fetch_fred_macro(fred_key)
        ohlc = fetch_commodity_ohlc()

    analysis = None
    with st.spinner("Gemini compiling macro intelligence..."):
        analysis = run_full_analysis(gemini_key, prices, macro, ohlc)

    if analysis:
        render_top_row(prices, analysis, macro)
        render_macro_section(analysis, macro)
        render_ai_insights(analysis)
        
        picks = analysis.get("picks", [])
        if picks:
            section_title("🎯", "Trade Ideas")
            p_cols = st.columns(4)
            for i, p in enumerate(picks[:4]):
                with p_cols[i]:
                    st.success(f"**{p.get('name')}** ({p.get('direction').upper()})\n\n{p.get('headline')}")

    else:
        st.error("AI Analysis failed to load. Check API keys.")

if __name__ == "__main__":
    main()
