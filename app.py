"""
AlphaTerminal — Live Macro Dashboard
Stack : Streamlit (UI) · yfinance (prices, zero cost) · Gemini 1.5 Flash (analysis, free tier)
Deploy: Streamlit Community Cloud — https://streamlit.io/cloud
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import plotly.graph_objects as go

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AlphaTerminal · Macro Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
#  CSS  — dark terminal aesthetic
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=DM+Sans:wght@400;500;600;700;800&family=Instrument+Serif:ital@0;1&display=swap');

html, body, [class*="css"], .stApp { background-color: #08090d !important; font-family: 'DM Sans', sans-serif !important; }
#MainMenu, footer, header, .stDeployButton { visibility: hidden; display: none; }
.main .block-container { padding-top: 0.5rem; max-width: 1440px; }
div[data-testid="stSidebar"] { background: #101319 !important; border-right: 1px solid #1d2330; }
.stButton>button { background: #2dd4a7 !important; color: #08090d !important; font-weight: 700 !important; border: none !important; border-radius: 6px !important; font-family: 'DM Sans', sans-serif !important; }
.stButton>button:hover { box-shadow: 0 4px 16px rgba(45,212,167,.35) !important; transform: translateY(-1px); }
.stTextInput>div>div>input { background: #101319 !important; color: #eaeef5 !important; border: 1px solid #1d2330 !important; border-radius: 6px !important; font-family: 'JetBrains Mono', monospace !important; }
.stSpinner > div { border-top-color: #2dd4a7 !important; }
div[data-testid="metric-container"] { background: #101319; border: 1px solid #1d2330; border-radius: 10px; padding: 12px 14px; }
div[data-testid="metric-container"] label { color: #5a6577 !important; font-family: 'JetBrains Mono', monospace !important; font-size: 10px !important; letter-spacing: 1.2px !important; }
div[data-testid="metric-container"] div[data-testid="stMetricValue"] { color: #eaeef5 !important; font-family: 'JetBrains Mono', monospace !important; font-size: 20px !important; font-weight: 700 !important; }
div[data-testid="metric-container"] div[data-testid="stMetricDelta"] { font-family: 'JetBrains Mono', monospace !important; font-size: 11px !important; }
div[data-testid="stMarkdownContainer"] p { color: #a8b3c5; }
.stAlert { background: #101319 !important; border: 1px solid #1d2330 !important; color: #a8b3c5 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
CORE = {
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
YIELDS = {
    "^TNX":("US","🇺🇸","10Y"),"^IRX":("US","🇺🇸","3M"),
    "IN10YT=RR":("India","🇮🇳","10Y"),"DE10YT=RR":("Germany","🇩🇪","10Y"),
    "GB10YT=RR":("UK","🇬🇧","10Y"),"JP10YT=RR":("Japan","🇯🇵","10Y"),
    "CN10YT=RR":("China","🇨🇳","10Y"),
}
AED_PEG = 3.6725
GRAM_OZ = 31.1035
C = dict(
    bg="#08090d",surf="#101319",bord="#1d2330",
    ink="#eaeef5",body="#a8b3c5",mute="#5a6577",dim="#363f51",
    green="#2dd4a7",red="#f4516c",amber="#f0b429",blue="#5b8def",purple="#a78bfa",
)

# ─────────────────────────────────────────────────────────────────────────────
#  PRICE FETCHING  (yfinance — server-side Python, zero cost, no API key)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)   # 15-min cache
def fetch_prices() -> dict:
    """Batch-download all prices via yfinance. Completely free, no API key needed."""
    all_syms = list(CORE) + list(US_SEC) + list(IN_SEC) + list(YIELDS)
    result: dict = {}
    try:
        raw = yf.download(
            all_syms, period="5d", auto_adjust=True,
            progress=False, threads=True, group_by="ticker",
        )
        if raw.empty:
            return result

        for sym in all_syms:
            try:
                if sym in raw.columns.get_level_values(0):
                    closes = raw[sym]["Close"].dropna()
                else:
                    closes = pd.Series(dtype=float)

                if len(closes) < 1:
                    continue
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
                pct  = ((last - prev) / prev * 100) if prev and prev != 0 else None
                result[sym] = {"price": last, "prev": prev, "pct": pct,
                               "ts": str(closes.index[-1].date())}
            except Exception:
                continue
    except Exception as e:
        result["_error"] = str(e)

    # Gold in AED/gram (24K) — derived, no extra API needed
    if "GC=F" in result and result["GC=F"].get("price"):
        g = result["GC=F"]
        result["GOLD_AED"] = {
            "price": g["price"] / GRAM_OZ * AED_PEG,
            "prev":  (g["prev"]  / GRAM_OZ * AED_PEG) if g.get("prev") else None,
        }
    result["_fetched_at"] = datetime.now(ZoneInfo("Asia/Dubai")).strftime("%d %b %Y  %H:%M %Z")
    return result

# ─────────────────────────────────────────────────────────────────────────────
#  GEMINI ANALYSIS  (free tier: 1,500 req/day · 1M tokens/day)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_SYSTEM = """You are an elite macro hedge fund analyst with institutional-grade expertise.
Use Google Search to find REAL current market data and news for today.
Return ONLY a single valid JSON object — no markdown fences, no preamble, no trailing text.
Unavailable fields → empty string "". Never invent numbers.

JSON schema (copy structure exactly, fill with real data):
{
  "ts": "ISO-8601 timestamp",
  "basis": "All % moves are current vs prior trading day regular session close",
  "mood": {"score":52,"label":"Cautious","sum":"1 sentence market tone","vixComment":"VIX interpretation","dxyComment":"DXY interpretation"},
  "macro": [
    {"c":"US",      "f":"🇺🇸","hl":"≤12 word headline","sum":"2-3 sentence institutional analysis","sent":"bullish","km":"key metric e.g. 10Y: 4.52%","cb":"Central bank note or empty"},
    {"c":"India",   "f":"🇮🇳","hl":"","sum":"","sent":"neutral","km":"","cb":""},
    {"c":"China",   "f":"🇨🇳","hl":"","sum":"","sent":"bearish","km":"","cb":""},
    {"c":"Japan",   "f":"🇯🇵","hl":"","sum":"","sent":"neutral","km":"","cb":""},
    {"c":"Eurozone","f":"🇪🇺","hl":"","sum":"","sent":"neutral","km":"","cb":""}
  ],
  "comms": [
    {"n":"Gold",       "s":"XAU","out":"2-sentence technical+fundamental outlook","sig":"hold","sup":"key support level","res":"key resistance level"},
    {"n":"Silver",     "s":"XAG","out":"","sig":"buy","sup":"","res":""},
    {"n":"Copper",     "s":"HG", "out":"","sig":"buy","sup":"","res":""},
    {"n":"Crude Oil",  "s":"WTI","out":"","sig":"hold","sup":"","res":""},
    {"n":"Natural Gas","s":"NG", "out":"","sig":"watch","sup":"","res":""}
  ],
  "banks": [
    {"c":"US",   "f":"🇺🇸","bank":"Fed", "rate":"5.50","rp":"5.50","rd":"Mar 2025","cpi":"3.2","cpip":"3.4","cpid":"Apr 2025","next":"Jun 11","stance":"hold"},
    {"c":"India","f":"🇮🇳","bank":"RBI", "rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hold"},
    {"c":"UK",   "f":"🇬🇧","bank":"BOE", "rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hold"},
    {"c":"Euro", "f":"🇪🇺","bank":"ECB", "rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"cut"},
    {"c":"Japan","f":"🇯🇵","bank":"BOJ", "rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hold"},
    {"c":"China","f":"🇨🇳","bank":"PBOC","rate":"","rp":"","rd":"","cpi":"","cpip":"","cpid":"","next":"","stance":"hold"}
  ],
  "picks": [
    {"t":"etf",      "n":"name","reg":"Global","dir":"long","conv":"high","hl":"≤12 word headline","thesis":"2-3 sentence thesis with catalyst and risk","tf":"3 months"},
    {"t":"stock",    "n":"",    "reg":"US",    "dir":"long","conv":"high","hl":"","thesis":"","tf":""},
    {"t":"sector",   "n":"",    "reg":"India", "dir":"long","conv":"medium","hl":"","thesis":"","tf":""},
    {"t":"commodity","n":"",    "reg":"Global","dir":"long","conv":"medium","hl":"","thesis":"","tf":""},
    {"t":"etf",      "n":"",    "reg":"EM",    "dir":"short","conv":"low","hl":"","thesis":"","tf":""}
  ],
  "geo": [
    {"cat":"Geopolitics",    "icon":"🌍","hl":"","det":"2-sentence detail with hedge fund angle","urg":"high"},
    {"cat":"Fed Watch",      "icon":"🏦","hl":"","det":"","urg":"high"},
    {"cat":"Rates & Bonds",  "icon":"📈","hl":"","det":"","urg":"medium"},
    {"cat":"Earnings Season","icon":"📊","hl":"","det":"","urg":"medium"},
    {"cat":"Crypto",         "icon":"₿","hl":"","det":"","urg":"low"},
    {"cat":"EM Risk",        "icon":"🌏","hl":"","det":"","urg":"medium"}
  ]
}

ENUM RULES:
label  → "Risk-On"|"Cautious"|"Risk-Off"|"Volatile"
sent   → "bullish"|"bearish"|"neutral"
sig    → "buy"|"sell"|"hold"|"watch"
stance → "hold"|"cut"|"hike"
dir    → "long"|"short"|"neutral"
conv   → "high"|"medium"|"low"
urg    → "high"|"medium"|"low"
rate/cpi → plain number string without % e.g. "5.50" not "5.50%"
"""

@st.cache_data(ttl=86400, show_spinner=False)  # 24-hour cache — runs ONCE per day
def fetch_analysis(api_key: str, today: str) -> dict:
    """
    Calls Gemini 1.5 Flash with Google Search grounding.
    'today' parameter ensures cache refreshes each calendar day.
    Free tier: 1,500 requests/day, 1,000,000 tokens/day.
    """
    user_prompt = f"""Today is {today}. Search Google and get real current data for ALL of the following:

MACRO & POLICY (search for today's news):
- US: Fed stance, latest CPI/PCE, NFP, yield curve signals, key policy moves
- India: RBI stance, latest CPI, IIP, FII flows, rupee dynamics
- China: PBOC policy, PMI prints, property sector, stimulus signals
- Japan: BOJ stance, yen levels, yield curve control, CPI
- Eurozone: ECB stance, latest CPI, growth outlook, EUR/USD

CENTRAL BANKS (search for latest confirmed data):
- Exact policy rates: Fed, RBI, BOE, ECB, BOJ, PBOC
- Latest CPI prints and dates for each country
- Next meeting dates and market expectations
- Rate change history (current vs previous rate)

COMMODITIES (search for analyst outlooks):
- Gold: key technical levels (support/resistance), macro drivers, signal
- Silver: industrial demand outlook, gold/silver ratio, signal
- Copper: China demand, supply deficit thesis, signal
- Crude Oil: OPEC+ stance, demand outlook, technical levels, signal
- Natural Gas: storage data, seasonal outlook, signal

TRADE IDEAS (generate 5 high-conviction ideas):
- Include sectors, ETFs, stocks, commodities across US, India, Global
- Each needs: direction (long/short), conviction level, clear 2-3 sentence thesis
- Include both bull and bear ideas across different timeframes

GEOPOLITICAL THEMES (search for what hedge funds are watching):
- Geopolitical risks affecting markets (Middle East, Russia-Ukraine, Taiwan)
- Fed Watch: rate cut odds, inflation path, dot plot signals
- Bond market stress signals, duration risk, curve dynamics
- Earnings season: key beats/misses, guidance, sector read-throughs
- Crypto: Bitcoin/ETF flows, halving impact, altcoin dynamics
- EM risks: currency stress, debt dynamics, central bank intervention

Return the complete JSON with real, searched data. Use empty string for any value you cannot confirm."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": GEMINI_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.1},
    }
    resp = requests.post(url, json=body, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(data["error"].get("message", "Gemini API error"))
    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError("No candidates returned by Gemini")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    # Strip citation markers like [1], [2] that grounding adds
    text = re.sub(r'\[\d+\]', '', text)
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1:
        raise ValueError("No JSON found in Gemini response")
    return json.loads(text[s:e+1])

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def hd(v) -> bool:
    return v is not None and v != ""

def parse_pct(s: str) -> float:
    if not s:
        return 0.0
    m = re.search(r'-?\d+(?:\.\d+)?', str(s))
    return float(m.group()) if m else 0.0

def pct_color(v: float) -> str:
    return C["green"] if v >= 0 else C["red"]

def fmt_price(v: float, decimals: int = 2, prefix: str = "") -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{prefix}{v:,.{decimals}f}"
    return f"{prefix}{v:.{decimals}f}"

def sent_color(s: str) -> str:
    return C["green"] if s == "bullish" else C["red"] if s == "bearish" else C["amber"]

def sig_color(s: str) -> str:
    return C["green"] if s == "buy" else C["red"] if s == "sell" else C["amber"] if s == "hold" else C["blue"]

def urg_color(u: str) -> str:
    return C["red"] if u == "high" else C["amber"] if u == "medium" else C["green"]

def stance_color(s: str) -> str:
    return C["green"] if s == "cut" else C["red"] if s == "hike" else C["amber"]

def stance_label(s: str) -> str:
    return "↓ EASING" if s == "cut" else "↑ TIGHTENING" if s == "hike" else "◆ HOLD"

def dir_style(d: str) -> tuple:
    if d == "long":  return "rgba(45,212,167,.1)", C["green"], "rgba(45,212,167,.22)"
    if d == "short": return "rgba(244,81,108,.1)", C["red"],   "rgba(244,81,108,.22)"
    return "rgba(240,180,41,.1)", C["amber"], "rgba(240,180,41,.22)"

def card(top_color: str, content: str) -> str:
    return f"""<div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:11px;
        padding:15px;position:relative;overflow:hidden;margin-bottom:2px;">
        <div style="position:absolute;top:0;left:0;right:0;height:2px;
            background:linear-gradient(90deg,{top_color},transparent);border-radius:11px 11px 0 0;"></div>
        {content}</div>"""

def badge(color: str, text: str) -> str:
    return f"""<span style="display:inline-block;font-family:'JetBrains Mono',monospace;
        font-size:9.5px;font-weight:700;padding:3px 8px;border-radius:4px;
        letter-spacing:.5px;text-transform:uppercase;
        background:{color}20;color:{color};border:1px solid {color}40;">{text}</span>"""

def tag(text: str, color: str = None) -> str:
    c = color or C["mute"]
    bg = f"rgba({','.join(str(int(c.lstrip('#')[i:i+2],16)) for i in (0,2,4))},.08)" if color else C["bg"]
    return f"""<span style="display:inline-block;font-family:'JetBrains Mono',monospace;
        font-size:9px;color:{c};background:{C['bg']};border:1px solid {C['bord']};
        padding:2px 6px;border-radius:3px;letter-spacing:.4px;text-transform:uppercase;margin-right:4px;">{text}</span>"""

def section_header(num: str, title: str, sub: str = "") -> str:
    return f"""<div style="display:flex;align-items:baseline;gap:12px;margin:2rem 0 1rem 0;
        padding-bottom:10px;border-bottom:1px solid {C['bord']};">
        <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:{C['mute']};
            letter-spacing:1.4px;">{num}</span>
        <span style="font-family:'Instrument Serif',serif;font-size:26px;color:{C['ink']};
            font-weight:400;letter-spacing:-.4px;">{title}</span>
        <div style="flex:1;height:1px;background:{C['bord']};"></div>
        <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:{C['mute']};
            letter-spacing:1.1px;text-transform:uppercase;">{sub}</span>
    </div>"""

# ─────────────────────────────────────────────────────────────────────────────
#  CHARTS
# ─────────────────────────────────────────────────────────────────────────────
def mood_gauge(score: int) -> go.Figure:
    color = C["red"] if score < 34 else C["amber"] if score < 67 else C["green"]
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"color": C["ink"], "family": "JetBrains Mono", "size": 28}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": C["mute"], "tickwidth": 1,
                     "tickfont": {"color": C["mute"], "size": 9, "family": "JetBrains Mono"}},
            "bar": {"color": color, "thickness": 0.25},
            "bgcolor": C["bg"],
            "borderwidth": 0,
            "steps": [
                {"range": [0, 33],  "color": "rgba(244,81,108,0.18)"},
                {"range": [33, 67], "color": "rgba(240,180,41,0.18)"},
                {"range": [67, 100],"color": "rgba(45,212,167,0.18)"},
            ],
        },
        domain={"x": [0, 1], "y": [0.1, 1]},
    ))
    fig.update_layout(
        height=220, margin=dict(l=15, r=15, t=20, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"color": C["ink"], "family": "JetBrains Mono"},
    )
    return fig

def asset_bar_chart(prices: dict) -> go.Figure:
    display = [
        ("^GSPC","S&P 500"),("^IXIC","Nasdaq"),("^DJI","Dow Jones"),
        ("^NSEI","Nifty 50"),("^BSESN","Sensex"),("^N225","Nikkei"),
        ("^HSI","Hang Seng"),("^GDAXI","DAX"),
        ("GC=F","Gold"),("CL=F","WTI Crude"),("BTC-USD","Bitcoin"),("DX-Y.NYB","DXY"),
    ]
    labels, vals, colors, hover_texts = [], [], [], []
    for sym, label in display:
        d = prices.get(sym)
        if d and d.get("pct") is not None:
            v = d["pct"]
            p = d.get("price") or 0
            prev = d.get("prev") or 0
            labels.append(label)
            vals.append(round(v, 2))
            colors.append(C["green"] if v >= 0 else C["red"])
            sign = "+" if v >= 0 else ""
            hover_texts.append(f"<b>{label}</b><br>Current: {p:,.2f}<br>Prev Close: {prev:,.2f}<br>Change: {sign}{v:.2f}%")

    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h",
        marker_color=colors, marker_line_width=0,
        opacity=0.85,
        text=[f"{'+' if v >= 0 else ''}{v:.2f}%" for v in vals],
        textposition="outside",
        textfont={"color": C["body"], "size": 10, "family": "JetBrains Mono"},
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_texts,
    ))
    zero_line = dict(color=C["dim"], width=1)
    fig.update_layout(
        height=max(320, len(labels) * 28),
        margin=dict(l=0, r=60, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=C["bord"], zerolinecolor=C["dim"],
                   tickfont={"color": C["mute"], "size": 9, "family": "JetBrains Mono"},
                   ticksuffix="%"),
        yaxis=dict(showgrid=False, tickfont={"color": C["body"], "size": 11, "family": "DM Sans"},
                   automargin=True),
        bargap=0.35,
        shapes=[dict(type="line", x0=0, x1=0, y0=-0.5, y1=len(labels)-0.5,
                     line=zero_line, layer="below")],
    )
    return fig

def yield_bar_chart(prices: dict) -> go.Figure:
    countries = [
        ("US","🇺🇸","^TNX","^IRX"),
        ("India","🇮🇳","IN10YT=RR",None),
        ("Germany","🇩🇪","DE10YT=RR",None),
        ("UK","🇬🇧","GB10YT=RR",None),
        ("Japan","🇯🇵","JP10YT=RR",None),
        ("China","🇨🇳","CN10YT=RR",None),
    ]
    labels, y10_vals, y2_vals = [], [], []
    for cname, flag, sym10, sym2 in countries:
        d10 = prices.get(sym10)
        if d10 and d10.get("price") is not None:
            labels.append(f"{flag} {cname}")
            y10_vals.append(round(d10["price"], 2))
            d2 = prices.get(sym2) if sym2 else None
            y2_vals.append(round(d2["price"], 2) if d2 and d2.get("price") else None)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=y10_vals, y=labels, orientation="h", name="10Y",
        marker_color=C["purple"], marker_line_width=0, opacity=0.85,
        text=[f"{v:.2f}%" for v in y10_vals],
        textposition="outside",
        textfont={"color": C["body"], "size": 10, "family": "JetBrains Mono"},
    ))
    y2_display = [v if v else 0 for v in y2_vals]
    if any(y2_vals):
        fig.add_trace(go.Bar(
            x=y2_display, y=labels, orientation="h", name="2Y / 3M",
            marker_color=C["blue"], marker_line_width=0, opacity=0.65,
            text=[f"{v:.2f}%" if v else "" for v in y2_vals],
            textposition="outside",
            textfont={"color": C["body"], "size": 10, "family": "JetBrains Mono"},
        ))
    fig.update_layout(
        barmode="overlay",
        height=max(250, len(labels) * 42),
        margin=dict(l=0, r=60, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor=C["bord"], zerolinecolor=C["dim"],
                   tickfont={"color": C["mute"], "size": 9, "family": "JetBrains Mono"},
                   ticksuffix="%"),
        yaxis=dict(showgrid=False, tickfont={"color": C["body"], "size": 11, "family": "DM Sans"},
                   automargin=True),
        legend=dict(orientation="h", x=0, y=-0.08,
                    font={"color": C["mute"], "size": 10, "family": "JetBrains Mono"},
                    bgcolor="rgba(0,0,0,0)"),
        bargap=0.3,
    )
    return fig

def sector_treemap(sector_dict: dict, prices: dict, title: str) -> go.Figure:
    names, vals, texts = [], [], []
    for sym, label in sector_dict.items():
        d = prices.get(sym)
        if d and d.get("pct") is not None:
            v = d["pct"]
            names.append(label)
            vals.append(abs(v) + 0.05)
            sign = "+" if v >= 0 else ""
            texts.append(f"<b>{label}</b><br>{sign}{v:.2f}%")

    if not names:
        fig = go.Figure()
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            annotations=[dict(text="Sector data unavailable", x=0.5, y=0.5,
                              font={"color": C["mute"], "size": 14}, showarrow=False)],
        )
        return fig

    raw_pcts = []
    for sym in sector_dict:
        d = prices.get(sym)
        raw_pcts.append(d["pct"] if d and d.get("pct") is not None else 0)

    fig = go.Figure(go.Treemap(
        labels=names, parents=[""] * len(names), values=vals,
        customdata=[[p] for p in raw_pcts],
        texttemplate="%{label}<br><b>%{customdata[0]:+.2f}%</b>",
        hovertemplate="<b>%{label}</b><br>Change: %{customdata[0]:+.2f}%<extra></extra>",
        marker=dict(
            colors=raw_pcts,
            colorscale=[
                [0.0,  "rgba(180,40,60,0.8)"],
                [0.35, "rgba(100,30,50,0.5)"],
                [0.5,  "rgba(22,27,37,0.9)"],
                [0.65, "rgba(15,60,50,0.5)"],
                [1.0,  "rgba(30,160,110,0.85)"],
            ],
            cmid=0,
            line=dict(width=1.5, color=C["bg"]),
        ),
        pathbar_visible=False,
        textfont={"family": "DM Sans", "size": 12, "color": C["ink"]},
    ))
    fig.update_layout(
        title=dict(text=title, font={"color": C["mute"], "size": 11,
                                     "family": "JetBrains Mono"}, x=0, pad=dict(l=0, t=0)),
        height=280,
        margin=dict(l=0, r=0, t=30, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig

# ─────────────────────────────────────────────────────────────────────────────
#  SECTION RENDERERS
# ─────────────────────────────────────────────────────────────────────────────
def render_hero(prices: dict, analysis: dict):
    """Hero row: Mood Gauge | Asset Performance | Sovereign Yields"""
    mood = analysis.get("mood", {}) if analysis else {}
    score = int(mood.get("score", 50)) if hd(mood.get("score")) else 50
    label = mood.get("label", "—")
    mood_colors = {
        "Risk-On": (C["green"], "rgba(45,212,167,.15)"),
        "Cautious": (C["amber"], "rgba(240,180,41,.15)"),
        "Risk-Off": (C["red"],  "rgba(244,81,108,.15)"),
        "Volatile": (C["blue"], "rgba(91,141,239,.15)"),
    }
    mood_c, mood_bg = mood_colors.get(label, (C["amber"], "rgba(240,180,41,.15)"))

    col1, col2, col3 = st.columns([1.1, 1.5, 1.4])

    # ── Mood panel ───────────────────────────────────────────────────────────
    with col1:
        st.markdown(f"""
        <div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:12px;padding:16px;height:100%;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:1.8px;
              color:{C['mute']};text-transform:uppercase;margin-bottom:4px;">Market Mood</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['dim']};
              margin-bottom:8px;">AI-SCORED · GEMINI + GOOGLE SEARCH</div>
        """, unsafe_allow_html=True)

        st.plotly_chart(mood_gauge(score), use_container_width=True, config={"displayModeBar": False})

        st.markdown(f"""
          <div style="text-align:center;margin-top:-10px;">
            <span style="background:{mood_bg};color:{mood_c};border:1px solid {mood_c}40;
                border-radius:20px;padding:3px 14px;font-family:'JetBrains Mono',monospace;
                font-size:13px;font-weight:700;">{label}</span>
          </div>
          {"" if not hd(mood.get("sum")) else f'<div style="font-family:Instrument Serif,serif;font-style:italic;font-size:11.5px;color:{C["body"]};text-align:center;margin-top:8px;line-height:1.55;padding:0 4px;">{mood["sum"]}</div>'}
        """, unsafe_allow_html=True)

        # Stats row: VIX, DXY, AED/INR, Gold AED
        vix    = prices.get("^VIX", {})
        dxy    = prices.get("DX-Y.NYB", {})
        aed    = prices.get("AEDIINR=X", {})
        gold_a = prices.get("GOLD_AED", {})

        stats = []
        if vix.get("price"): stats.append(("VIX",      f"{vix['price']:.1f}",  f"prev {vix['prev']:.1f}" if vix.get("prev") else ""))
        if dxy.get("price"): stats.append(("DXY",      f"{dxy['price']:.2f}",  f"prev {dxy['prev']:.2f}" if dxy.get("prev") else ""))
        if aed.get("price"): stats.append(("AED/INR",  f"{aed['price']:.4f}",  f"prev {aed['prev']:.4f}" if aed.get("prev") else ""))
        if gold_a.get("price"): stats.append(("GOLD/g 24K", f"AED {gold_a['price']:.2f}", f"prev {gold_a['prev']:.2f}" if gold_a.get("prev") else ""))

        if stats:
            cols_inner = "".join(f"""
              <div style="flex:1;display:flex;flex-direction:column;align-items:center;
                  padding:5px 6px;border-right:1px solid {C['bord']};" class="last-no-border">
                <div style="font-family:'JetBrains Mono',monospace;font-size:8px;color:{C['mute']};
                    letter-spacing:1px;text-transform:uppercase;">{s[0]}</div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;
                    color:{C['ink']};margin-top:1px;">{s[1]}</div>
                <div style="font-family:'JetBrains Mono',monospace;font-size:8px;color:{C['mute']};">{s[2]}</div>
              </div>""" for s in stats)
            st.markdown(f"""
            <div style="display:flex;border-top:1px solid {C['bord']};margin-top:10px;padding-top:8px;">
              {cols_inner}
            </div>
            <style>.last-no-border:last-child{{border-right:none !important}}</style>
            """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Asset Performance ────────────────────────────────────────────────────
    with col2:
        st.markdown(f"""
        <div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:12px;padding:16px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:1.8px;
              color:{C['mute']};text-transform:uppercase;margin-bottom:4px;">Asset Performance — vs Prior Close</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['dim']};
              margin-bottom:10px;">CURRENT · CHANGE % · PREV CLOSE · SOURCE: YAHOO FINANCE</div>
        """, unsafe_allow_html=True)
        if prices and any(k in prices for k in ["^GSPC","^NSEI","GC=F"]):
            st.plotly_chart(asset_bar_chart(prices), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Price data loading…")
        st.markdown("</div>", unsafe_allow_html=True)

    # ── Yields ───────────────────────────────────────────────────────────────
    with col3:
        st.markdown(f"""
        <div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:12px;padding:16px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:1.8px;
              color:{C['mute']};text-transform:uppercase;margin-bottom:4px;">Sovereign Yields — 10Y + 2Y/3M</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['dim']};
              margin-bottom:10px;">SOURCE: YAHOO FINANCE · PURPLE = 10Y · BLUE = 2Y/3M</div>
        """, unsafe_allow_html=True)
        if prices and any(k in prices for k in ["^TNX","IN10YT=RR"]):
            st.plotly_chart(yield_bar_chart(prices), use_container_width=True,
                            config={"displayModeBar": False})
        else:
            st.caption("Yield data loading…")
        st.markdown("</div>", unsafe_allow_html=True)


def render_macro(analysis: dict):
    macro = [m for m in (analysis.get("macro") or []) if hd(m.get("hl"))]
    if not macro:
        return
    st.markdown(section_header("01", "Global Macro", f"{len(macro)} economies"), unsafe_allow_html=True)
    cols = st.columns(len(macro))
    for i, m in enumerate(macro):
        c = sent_color(m.get("sent", ""))
        with cols[i]:
            inner = f"""
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:9px;">
                <div style="display:flex;align-items:center;gap:8px;">
                  <span style="font-size:22px;">{m.get("f","")}</span>
                  <div>
                    <div style="font-family:'Instrument Serif',serif;font-size:18px;color:{C['ink']};
                        font-weight:400;letter-spacing:-.3px;line-height:1.1;">{m.get("c","")}</div>
                    {"" if not hd(m.get("km")) else f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:{C["blue"]};margin-top:2px;">{m["km"]}</div>'}
                  </div>
                </div>
                {badge(c, m.get("sent",""))}
              </div>
              <div style="font-size:12.5px;font-weight:700;color:{C['ink']};margin-bottom:6px;
                  line-height:1.45;">{m.get("hl","")}</div>
              <div style="font-size:11.5px;color:{C['body']};line-height:1.65;">{m.get("sum","")}</div>
              {"" if not hd(m.get("cb")) else f'<div style="margin-top:9px;padding:5px 9px;background:rgba(91,141,239,.06);border:1px solid rgba(91,141,239,.18);border-radius:5px;font-size:10px;color:{C["blue"]};font-family:JetBrains Mono,monospace;">🏦 {m["cb"]}</div>'}
            """
            st.markdown(card(c, inner), unsafe_allow_html=True)


def render_commodities(prices: dict, analysis: dict):
    comms_ai = {c["n"]: c for c in (analysis.get("comms") or [])} if analysis else {}
    sym_map = [("GC=F","Gold"),("CL=F","Crude Oil"),("SI=F","Silver"),
               ("HG=F","Copper"),("NG=F","Natural Gas")]
    sym_map_n = {"Gold":"Gold","Crude Oil":"Crude Oil","Silver":"Silver",
                 "Copper":"Copper","Natural Gas":"Natural Gas"}

    st.markdown(section_header("02", "Commodities", "Live prices · AI outlook"), unsafe_allow_html=True)
    cols = st.columns(5)
    for i, (sym, name) in enumerate(sym_map):
        pd_  = prices.get(sym, {})
        ai   = comms_ai.get(name, {})
        sc   = sig_color(ai.get("sig","")) if ai.get("sig") else C["mute"]
        pct_v= pd_.get("pct")
        isup = (pct_v or 0) >= 0
        with cols[i]:
            price_html = ""
            if pd_.get("price"):
                sign  = "+" if isup else ""
                arrow = "▲" if isup else "▼"
                pcc   = C["green"] if isup else C["red"]
                price_html = f"""
                <div style="background:{C['bg']};border:1px solid {C['bord']};border-radius:6px;
                    padding:8px 10px;margin:8px 0;">
                  <div>
                    <span style="font-family:'JetBrains Mono',monospace;font-size:19px;
                        font-weight:700;color:{C['ink']};">{fmt_price(pd_["price"])}</span>
                    {"" if pct_v is None else f'<span style="font-family:JetBrains Mono,monospace;font-size:12px;font-weight:700;color:{pcc};margin-left:8px;">{arrow} {sign}{pct_v:.2f}%</span>'}
                  </div>
                  {"" if not pd_.get("prev") else f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:{C["mute"]};margin-top:2px;">prev close {fmt_price(pd_["prev"])}</div>'}
                </div>"""

            levels_html = ""
            if ai.get("sup") or ai.get("res"):
                levels_html = f"""
                <div style="display:flex;gap:9px;background:{C['bg']};border:1px solid {C['bord']};
                    border-radius:5px;padding:6px 9px;margin-bottom:7px;">
                  {"" if not ai.get("sup") else f'<div style="flex:1;"><div style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{C["mute"]};letter-spacing:1px;text-transform:uppercase;margin-bottom:2px;">Support</div><div style="font-family:JetBrains Mono,monospace;font-size:11px;font-weight:700;color:{C["green"]};">{ai["sup"]}</div></div>'}
                  {"" if not ai.get("res") else f'<div style="flex:1;"><div style="font-family:JetBrains Mono,monospace;font-size:8.5px;color:{C["mute"]};letter-spacing:1px;text-transform:uppercase;margin-bottom:2px;">Resistance</div><div style="font-family:JetBrains Mono,monospace;font-size:11px;font-weight:700;color:{C["red"]};">{ai["res"]}</div></div>'}
                </div>"""

            inner = f"""
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                  <div style="font-family:'Instrument Serif',serif;font-size:18px;color:{C['ink']};
                      font-weight:400;line-height:1;">{name}</div>
                  <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['mute']};
                      margin-top:3px;letter-spacing:1px;">{ai.get("s",sym)}</div>
                </div>
                {badge(sc, ai.get("sig","")) if ai.get("sig") else ""}
              </div>
              {price_html}
              {levels_html}
              {"" if not ai.get("out") else f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{ai["out"]}</div>'}
            """
            st.markdown(card(sc, inner), unsafe_allow_html=True)


def render_heatmaps(prices: dict):
    has_us = any(sym in prices and prices[sym].get("pct") is not None for sym in US_SEC)
    has_in = any(sym in prices and prices[sym].get("pct") is not None for sym in IN_SEC)
    if not has_us and not has_in:
        return
    st.markdown(section_header("03", "Sector Heatmaps", "US + India · vs prior close"), unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(sector_treemap(US_SEC, prices, "🇺🇸  US — S&P 500 GICS Sectors"),
                        use_container_width=True, config={"displayModeBar": False})
    with c2:
        st.plotly_chart(sector_treemap(IN_SEC, prices, "🇮🇳  India — NSE Sectoral Indices"),
                        use_container_width=True, config={"displayModeBar": False})


def render_central_banks(analysis: dict):
    banks = [b for b in (analysis.get("banks") or []) if hd(b.get("rate"))]
    if not banks:
        return
    st.markdown(section_header("04", "Central Banks & Inflation",
                               "Policy rate · CPI · Stance · Next meeting"), unsafe_allow_html=True)
    th_style = f"padding:7px 10px;font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['mute']};" \
               f"letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid {C['bord']};background:{C['bg']};white-space:nowrap;"
    rows_html = ""
    for i, b in enumerate(banks):
        sc     = stance_color(b.get("stance",""))
        rd     = round(float(b["rate"]) - float(b["rp"]), 2) if hd(b.get("rate")) and hd(b.get("rp")) else None
        cd     = round(float(b["cpi"])  - float(b["cpip"]),2) if hd(b.get("cpi")) and hd(b.get("cpip")) else None
        row_bg = "rgba(255,255,255,.012)" if i % 2 else "transparent"
        rows_html += f"""
        <tr style="background:{row_bg};">
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};">
            <div style="display:flex;align-items:center;gap:7px;">
              <span style="font-size:18px;">{b.get("f","")}</span>
              <span style="font-size:13px;font-weight:700;color:{C['ink']};font-family:'DM Sans',sans-serif;">{b.get("c","")}</span>
            </div>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:11px;color:{C['mute']};font-family:'JetBrains Mono',monospace;">{b.get("bank","")}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:16px;font-weight:800;color:{C['ink']};font-family:'JetBrains Mono',monospace;">
              {b["rate"]+"%"  if hd(b.get("rate")) else "—"}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <div style="display:flex;flex-direction:column;align-items:center;gap:1px;">
              <span style="font-size:12px;color:{C['body']};font-family:'JetBrains Mono',monospace;">
                {b["rp"]+"%"  if hd(b.get("rp")) else "—"}</span>
              {"" if rd is None or abs(rd)<.001 else f'<span style="font-size:10px;color:{C["green"] if rd<0 else C["red"]};font-family:JetBrains Mono,monospace;font-weight:700;">{"+" if rd>0 else ""}{rd:.2f}pp</span>'}
            </div>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:10px;color:{C['mute']};font-family:'JetBrains Mono',monospace;">{b.get("rd") or "—"}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <div style="display:flex;align-items:center;justify-content:center;gap:4px;">
              <span style="font-size:14px;font-weight:800;color:{C['ink']};font-family:'JetBrains Mono',monospace;">
                {b["cpi"]+"%"  if hd(b.get("cpi")) else "—"}</span>
              {"" if cd is None else f'<span style="font-size:11px;color:{C["green"] if cd<0 else C["red"]};">{"↓" if cd<0 else "↑"}</span>'}
            </div>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:12px;color:{C['body']};font-family:'JetBrains Mono',monospace;">
              {b["cpip"]+"%" if hd(b.get("cpip")) else "—"}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:10px;color:{C['mute']};font-family:'JetBrains Mono',monospace;">{b.get("cpid") or "—"}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;font-weight:700;
                background:{sc}1a;color:{sc};border:1px solid {sc}35;border-radius:4px;
                padding:3px 7px;white-space:nowrap;">{stance_label(b.get("stance",""))}</span>
          </td>
          <td style="padding:9px 10px;border-bottom:1px solid {C['bord']};text-align:center;">
            <span style="font-size:10px;color:{C['blue']};font-family:'JetBrains Mono',monospace;font-weight:500;">{b.get("next") or "—"}</span>
          </td>
        </tr>"""

    st.markdown(f"""
    <div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:11px;overflow:hidden;overflow-x:auto;">
    <table style="width:100%;border-collapse:separate;border-spacing:0;font-size:12px;">
      <thead><tr>
        {"".join(f'<th style="{th_style}">{h}</th>' for h in ["","Bank","Rate","Prev","Rate Date","CPI","Prev CPI","CPI Date","Stance","Next Mtg"])}
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>""", unsafe_allow_html=True)


def render_picks(analysis: dict):
    picks = [p for p in (analysis.get("picks") or []) if hd(p.get("n"))]
    if not picks:
        return
    st.markdown(section_header("05", "Top Picks & Trade Ideas", f"{len(picks)} ideas"), unsafe_allow_html=True)
    cols = st.columns(len(picks))
    for i, p in enumerate(picks):
        db, dc, dbd = dir_style(p.get("dir",""))
        cc = C["green"] if p.get("conv")=="high" else C["amber"] if p.get("conv")=="medium" else C["mute"]
        dots = "".join(f'<div style="width:6px;height:6px;border-radius:50%;background:{"" + cc if j < (3 if p.get("conv")=="high" else 2 if p.get("conv")=="medium" else 1) else C["dim"]};"></div>'
                       for j in range(3))
        dir_txt = "▲ LONG" if p.get("dir")=="long" else "▼ SHORT" if p.get("dir")=="short" else "◆ NEUTRAL"
        with cols[i]:
            inner = f"""
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div style="font-family:'Instrument Serif',serif;font-size:18px;color:{C['ink']};
                    font-weight:400;letter-spacing:-.3px;line-height:1.1;">{p.get("n","")}</div>
                <div style="display:flex;gap:4px;flex-wrap:wrap;">
                  {tag(p["reg"], C["mute"]) if hd(p.get("reg")) else ""}
                  {tag(p["t"], C["blue"]) if hd(p.get("t")) else ""}
                </div>
              </div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;align-items:center;">
                <span style="font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;
                    padding:3px 9px;border-radius:4px;background:{db};color:{dc};
                    border:1px solid {dbd};">{dir_txt}</span>
                <div style="display:flex;align-items:center;gap:5px;">
                  <div style="display:flex;gap:3px;">{dots}</div>
                  <span style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['body']};
                      text-transform:uppercase;letter-spacing:.5px;font-weight:700;">{p.get("conv","")}</span>
                </div>
                {tag(p["tf"]) if hd(p.get("tf")) else ""}
              </div>
              {"" if not hd(p.get("hl")) else f'<div style="font-size:12.5px;font-weight:700;color:{C["ink"]};margin-bottom:6px;line-height:1.45;">{p["hl"]}</div>'}
              {"" if not hd(p.get("thesis")) else f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{p["thesis"]}</div>'}
            """
            st.markdown(card(dc, inner), unsafe_allow_html=True)


def render_geo(analysis: dict):
    geo = [g for g in (analysis.get("geo") or []) if hd(g.get("hl"))]
    if not geo:
        return
    st.markdown(section_header("06", "Hedge Fund Radar", f"{len(geo)} themes"), unsafe_allow_html=True)
    cols = st.columns(3)
    for i, g in enumerate(geo):
        uc = urg_color(g.get("urg",""))
        with cols[i % 3]:
            inner = f"""
              <div style="display:flex;gap:11px;align-items:flex-start;">
                <div style="flex-shrink:0;width:35px;height:35px;display:flex;align-items:center;
                    justify-content:center;background:{C['bg']};border:1px solid {C['bord']};
                    border-radius:8px;font-size:17px;">{g.get("icon","")}</div>
                <div style="flex:1;min-width:0;">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <span style="font-family:'JetBrains Mono',monospace;font-size:9.5px;font-weight:700;
                        letter-spacing:1.2px;color:{C['mute']};text-transform:uppercase;">{g.get("cat","")}</span>
                    <div style="width:7px;height:7px;border-radius:50%;background:{uc};
                        box-shadow:0 0 7px {uc};"></div>
                  </div>
                  <div style="font-size:12.5px;font-weight:700;color:{C['ink']};margin-bottom:5px;
                      line-height:1.45;">{g.get("hl","")}</div>
                  {"" if not hd(g.get("det")) else f'<div style="font-size:11.5px;color:{C["body"]};line-height:1.65;">{g["det"]}</div>'}
                </div>
              </div>"""
            st.markdown(card(uc, inner), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
#  SIDEBAR  — API key entry
# ─────────────────────────────────────────────────────────────────────────────
def sidebar_setup() -> str | None:
    with st.sidebar:
        st.markdown(f"""
        <div style="font-family:'Instrument Serif',serif;font-size:22px;color:{C['ink']};
            letter-spacing:-.4px;margin-bottom:4px;">Alpha<i style='color:{C["green"]};
            font-style:italic;'>Terminal</i></div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['mute']};
            letter-spacing:1.6px;margin-bottom:20px;">MACRO · COMMODITIES · ALPHA</div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
        <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;letter-spacing:1.2px;
            color:{C['mute']};text-transform:uppercase;margin-bottom:8px;">Gemini API Key</div>
        """, unsafe_allow_html=True)

        api_key = st.text_input("Gemini API Key", type="password", label_visibility="collapsed",
                                placeholder="AIzaSy...",
                                help="Get a free key at aistudio.google.com")

        st.markdown(f"""
        <div style="background:rgba(45,212,167,.06);border:1px solid rgba(45,212,167,.18);
            border-radius:8px;padding:10px 12px;margin-top:12px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['green']};
              font-weight:700;letter-spacing:1px;margin-bottom:6px;">FREE TIER LIMITS</div>
          <div style="font-size:11px;color:{C['body']};line-height:1.7;">
            ● 1,500 requests / day<br>
            ● 1,000,000 tokens / day<br>
            ● Analysis cached 24 hours<br>
            ● Prices refresh every 15 min<br>
            ● <b style="color:{C['green']}">Effectively zero daily cost</b>
          </div>
        </div>
        <div style="margin-top:12px;font-size:10px;color:{C['mute']};line-height:1.6;">
          Get free key at<br>
          <a href="https://aistudio.google.com" target="_blank"
              style="color:{C['blue']};">aistudio.google.com</a>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        st.markdown(f"""
        <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['mute']};
            letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">Data Sources</div>
        <div style="font-size:11px;color:{C['body']};line-height:1.8;">
          📊 <b>Prices</b> — Yahoo Finance (yfinance)<br>
          🤖 <b>Analysis</b> — Gemini 1.5 Flash<br>
          🔍 <b>Search</b> — Google Search grounding<br>
          💰 <b>Total daily cost</b> — <span style="color:{C['green']};">$0</span>
        </div>
        """, unsafe_allow_html=True)

    return api_key.strip() if api_key else None


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    api_key = sidebar_setup()

    # ── Header ───────────────────────────────────────────────────────────────
    h1, h2, h3 = st.columns([3, 5, 2])
    with h1:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:12px;padding:12px 0 8px 0;">
          <div style="width:36px;height:36px;border-radius:9px;
              background:linear-gradient(135deg,{C['green']},{C['blue']});
              display:flex;align-items:center;justify-content:center;
              font-family:'Instrument Serif',serif;font-size:22px;font-style:italic;
              color:{C['bg']};box-shadow:0 4px 14px rgba(45,212,167,.2);">α</div>
          <div>
            <div style="font-family:'Instrument Serif',serif;font-size:24px;color:{C['ink']};
                letter-spacing:-.5px;line-height:1;">Alpha<i style="font-style:italic;
                color:{C['green']};">Terminal</i></div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:{C['mute']};
                letter-spacing:1.6px;margin-top:2px;">MACRO · COMMODITIES · ALPHA</div>
          </div>
        </div>""", unsafe_allow_html=True)
    with h2:
        now_uae = datetime.now(ZoneInfo("Asia/Dubai"))
        st.markdown(f"""
        <div style="padding:14px 0 0 0;font-family:'JetBrains Mono',monospace;font-size:10px;
            color:{C['mute']};letter-spacing:.5px;">
          {now_uae.strftime("%A, %d %B %Y  %H:%M")} GST
        </div>
        <div style="font-size:10px;color:{C['dim']};font-family:'JetBrains Mono',monospace;margin-top:3px;">
          All % changes vs prior trading day regular session close · Prices: Yahoo Finance (15-min cache) · Analysis: Gemini (24-hr cache)
        </div>""", unsafe_allow_html=True)
    with h3:
        st.markdown("<div style='padding-top:10px;'>", unsafe_allow_html=True)
        if st.button("↺  Refresh Prices", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(f"<hr style='border:none;border-top:1px solid {C['bord']};margin:0 0 12px 0;'>",
                unsafe_allow_html=True)

    # ── Gate on API key ───────────────────────────────────────────────────────
    if not api_key:
        st.markdown(f"""
        <div style="background:{C['surf']};border:1px solid {C['bord']};border-radius:12px;
            padding:40px;text-align:center;margin:40px auto;max-width:500px;">
          <div style="font-family:'Instrument Serif',serif;font-size:52px;color:{C['green']};
              font-style:italic;margin-bottom:12px;">α</div>
          <div style="font-family:'Instrument Serif',serif;font-size:24px;color:{C['ink']};
              margin-bottom:10px;">Enter your Gemini API key</div>
          <div style="font-size:13px;color:{C['body']};line-height:1.7;margin-bottom:20px;">
            Open the sidebar (← arrow top-left) and paste your free Gemini API key.<br>
            Get one in 60 seconds at
            <a href="https://aistudio.google.com" target="_blank" style="color:{C['blue']};">aistudio.google.com</a>
          </div>
          <div style="background:rgba(45,212,167,.06);border:1px solid rgba(45,212,167,.2);
              border-radius:8px;padding:14px;font-size:12px;color:{C['body']};line-height:1.8;">
            Free tier: <b style="color:{C['green']};">1,500 requests/day · 1M tokens/day</b><br>
            Analysis cached 24 hours · Prices refresh every 15 min<br>
            <b style="color:{C['green']};">Total daily cost: $0</b>
          </div>
        </div>""", unsafe_allow_html=True)
        return

    # ── Fetch prices (yfinance, free, always) ────────────────────────────────
    with st.spinner("Fetching live market prices via Yahoo Finance…"):
        prices = fetch_prices()

    # ── Fetch/cache analysis (Gemini, once/day) ──────────────────────────────
    analysis = None
    analysis_error = None
    with st.spinner("Loading AI analysis (Gemini 1.5 Flash with Google Search)…"):
        try:
            analysis = fetch_analysis(api_key, str(date.today()))
        except Exception as e:
            analysis_error = str(e)

    if analysis_error:
        st.error(f"⚠ Gemini analysis failed: {analysis_error}")
        st.info("Prices are still shown below from Yahoo Finance. Check your API key in the sidebar.")

    # Fetched-at info
    if prices.get("_fetched_at"):
        st.markdown(f"""
        <div style="font-family:'JetBrains Mono',monospace;font-size:9.5px;color:{C['dim']};
            margin-bottom:16px;">● PRICES FETCHED AT {prices["_fetched_at"]} ·
        {"⚡ AI ANALYSIS LOADED (GEMINI)" if analysis else "⚠ AI ANALYSIS UNAVAILABLE"}</div>
        """, unsafe_allow_html=True)

    # ── Render sections ───────────────────────────────────────────────────────
    render_hero(prices, analysis)

    if analysis:
        render_macro(analysis)

    render_commodities(prices, analysis)
    render_heatmaps(prices)

    if analysis:
        render_central_banks(analysis)
        render_picks(analysis)
        render_geo(analysis)

    # Footer
    st.markdown(f"""
    <div style="padding:16px 0;border-top:1px solid {C['bord']};margin-top:28px;
        font-size:9.5px;color:{C['dim']};font-family:'JetBrains Mono',monospace;
        display:flex;justify-content:space-between;flex-wrap:wrap;gap:5px;">
      <span>ALPHA TERMINAL · PRICES: YAHOO FINANCE (FREE) · ANALYSIS: GEMINI 1.5 FLASH (FREE TIER)</span>
      <span>NOT FINANCIAL ADVICE · FOR INFORMATIONAL PURPOSES ONLY</span>
    </div>""", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
