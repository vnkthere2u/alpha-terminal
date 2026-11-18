"""Universe, data series, relationship definitions and thresholds.

Everything the engine tracks is declared here so the logic modules stay generic.
"""

from __future__ import annotations

# ─── yfinance universe ────────────────────────────────────────────────────────
# key -> (yahoo symbol, display name, group)
ASSETS: dict[str, tuple[str, str, str]] = {
    # Equities
    "SPX":     ("^GSPC",     "S&P 500",            "equity"),
    "NDX":     ("^NDX",      "Nasdaq 100",         "equity"),
    "RSP":     ("RSP",       "S&P 500 Equal Wt",   "equity"),
    "SPY":     ("SPY",       "SPY",                "equity"),
    "IWM":     ("IWM",       "Russell 2000",       "equity"),
    "SX5E":    ("^STOXX50E", "Euro Stoxx 50",      "equity"),
    "NKY":     ("^N225",     "Nikkei 225",         "equity"),
    "HSI":     ("^HSI",      "Hang Seng",          "equity"),
    "CSI300":  ("000300.SS", "CSI 300",            "equity"),
    "NIFTY":   ("^NSEI",     "Nifty 50",           "equity"),
    "BANKNIFTY": ("^NSEBANK", "Bank Nifty",        "equity"),
    "EEM":     ("EEM",       "EM Equities",        "equity"),
    # US sector / style
    "XLY":     ("XLY",       "Cons Discretionary", "sector"),
    "XLP":     ("XLP",       "Cons Staples",       "sector"),
    "XLE":     ("XLE",       "Energy",             "sector"),
    "XLF":     ("XLF",       "Financials",         "sector"),
    "XLU":     ("XLU",       "Utilities",          "sector"),
    "KRE":     ("KRE",       "Regional Banks",     "sector"),
    "SMH":     ("SMH",       "Semiconductors",     "sector"),
    # Rates / credit ETFs
    "TLT":     ("TLT",       "UST 20Y+",           "rates"),
    "IEF":     ("IEF",       "UST 7-10Y",          "rates"),
    "HYG":     ("HYG",       "US High Yield",      "credit"),
    "LQD":     ("LQD",       "US Inv Grade",       "credit"),
    # Volatility
    "VIX":     ("^VIX",      "VIX",                "vol"),
    "VIX3M":   ("^VIX3M",    "VIX 3M",             "vol"),
    "MOVE":    ("^MOVE",     "MOVE (UST vol)",     "vol"),
    # FX
    "DXY":     ("DX-Y.NYB",  "Dollar Index",       "fx"),
    "EURUSD":  ("EURUSD=X",  "EUR/USD",            "fx"),
    "USDJPY":  ("USDJPY=X",  "USD/JPY",            "fx"),
    "USDCNH":  ("USDCNH=X",  "USD/CNH",            "fx"),
    "USDINR":  ("USDINR=X",  "USD/INR",            "fx"),
    "AUDJPY":  ("AUDJPY=X",  "AUD/JPY",            "fx"),
    # Commodities
    "GOLD":    ("GC=F",      "Gold",               "commodity"),
    "SILVER":  ("SI=F",      "Silver",             "commodity"),
    "COPPER":  ("HG=F",      "Copper",             "commodity"),
    "WTI":     ("CL=F",      "WTI Crude",          "commodity"),
    "BRENT":   ("BZ=F",      "Brent Crude",        "commodity"),
    "NATGAS":  ("NG=F",      "Nat Gas (HH)",       "commodity"),
    # Crypto
    "BTC":     ("BTC-USD",   "Bitcoin",            "crypto"),
}

# Assets whose "up" means stress (used when describing moves)
INVERSE_RISK = {"VIX", "VIX3M", "MOVE", "DXY", "USDINR", "USDCNH", "TLT", "IEF"}

# ─── FRED series ──────────────────────────────────────────────────────────────
# key -> (series id, display name)
FRED: dict[str, tuple[str, str]] = {
    "UST3M":   ("DGS3MO",        "UST 3M yield"),
    "UST2Y":   ("DGS2",          "UST 2Y yield"),
    "UST10Y":  ("DGS10",         "UST 10Y yield"),
    "UST30Y":  ("DGS30",         "UST 30Y yield"),
    "REAL10Y": ("DFII10",        "US 10Y real yield (TIPS)"),
    "BE10Y":   ("T10YIE",        "US 10Y breakeven inflation"),
    "FWD5Y5Y": ("T5YIFR",        "US 5y5y fwd inflation"),
    "TP10Y":   ("THREEFYTP10",   "US 10Y term premium (Kim-Wright)"),
    "HYOAS":   ("BAMLH0A0HYM2",  "US HY OAS"),
    "IGOAS":   ("BAMLC0A0CM",    "US IG OAS"),
    "EFFR":    ("DFF",           "Effective Fed Funds"),
    "USDBROAD": ("DTWEXBGS",     "Broad trade-weighted USD"),
    "NFCI":    ("NFCI",          "Chicago Fed NFCI (weekly)"),
    "STLFSI":  ("STLFSI4",       "St Louis Fed stress (weekly)"),
    "WALCL":   ("WALCL",         "Fed balance sheet"),
    "TGA":     ("WTREGEN",       "Treasury General Account"),
    "RRP":     ("RRPONTSYD",     "Overnight reverse repo"),
    "CLAIMS":  ("ICSA",          "Initial jobless claims"),
}

# ─── CFTC Commitments of Traders ──────────────────────────────────────────────
# Socrata datasets on publicreporting.cftc.gov
COT_DISAGG = "72hh-3qpy"   # Disaggregated futures-only (commodities) -> managed money
COT_TFF = "gpe5-46if"      # Traders in Financial Futures futures-only -> leveraged funds
# key -> (dataset, cftc_contract_market_code, display name)
COT_MARKETS: dict[str, tuple[str, str, str]] = {
    "GOLD":   (COT_DISAGG, "088691", "Gold (COMEX)"),
    "SILVER": (COT_DISAGG, "084691", "Silver (COMEX)"),
    "COPPER": (COT_DISAGG, "085692", "Copper (COMEX)"),
    "WTI":    (COT_DISAGG, "067651", "WTI Crude (NYMEX)"),
    "NATGAS": (COT_DISAGG, "023651", "Nat Gas (NYMEX)"),
    "EUR":    (COT_TFF,    "099741", "Euro FX (CME)"),
    "JPY":    (COT_TFF,    "097741", "Japanese Yen (CME)"),
    "UST10":  (COT_TFF,    "043602", "UST 10Y Note (CBOT)"),
    "ES":     (COT_TFF,    "13874A", "E-mini S&P 500 (CME)"),
}

# ─── Derived ratio series (numerator / denominator, both asset keys) ──────────
RATIOS: dict[str, tuple[str, str, str]] = {
    "GOLD_SILVER": ("GOLD", "SILVER", "Gold/Silver ratio"),
    "COPPER_GOLD": ("COPPER", "GOLD", "Copper/Gold ratio (growth proxy)"),
    "OIL_GOLD":    ("WTI", "GOLD", "Oil/Gold ratio"),
    "SPX_GOLD":    ("SPX", "GOLD", "S&P 500 in gold terms"),
    "BREADTH":     ("RSP", "SPY", "Equal-wt vs cap-wt S&P (breadth)"),
    "SMALL_LARGE": ("IWM", "SPY", "Small vs large caps"),
    "CYC_DEF":     ("XLY", "XLP", "Cyclicals vs defensives"),
    "BANKS_MKT":   ("KRE", "SPY", "Regional banks vs market"),
    "SEMIS_MKT":   ("SMH", "SPY", "Semis vs market"),
    "HY_UST":      ("HYG", "IEF", "HY credit vs Treasuries"),
    "EM_US":       ("EEM", "SPY", "EM vs US equities"),
}

# ─── Relationship monitor ─────────────────────────────────────────────────────
# Historical relationships whose breakdown is itself information.
# (key, series A, series B, kind A, kind B, display name, interpretation hint)
# kind: "ret" = log return of a price, "chg" = first difference of a level (yields/spreads)
RELATIONSHIPS: list[tuple[str, str, str, str, str, str, str]] = [
    ("gold_real", "GOLD", "REAL10Y", "ret", "chg", "Gold vs US real yields",
     "Normally strongly negative. Breakdown = non-rate buyer (CB/EM reserve diversification) or debasement bid dominating."),
    ("gold_usd", "GOLD", "DXY", "ret", "ret", "Gold vs Dollar",
     "Normally negative. Gold rising with USD = fear/reserve-diversification bid."),
    ("oil_be", "WTI", "BE10Y", "ret", "chg", "Oil vs US breakevens",
     "Normally positive. Breakdown = inflation expectations de-anchoring from (or ignoring) energy."),
    ("stock_bond", "SPX", "TLT", "ret", "ret", "Equities vs long bonds",
     "Negative = bonds hedge equities (growth regime). Positive = inflation/term-premium regime, 60/40 hedge fails."),
    ("copgold_10y", "COPPER_GOLD", "UST10Y", "ret", "chg", "Copper/Gold vs UST 10Y",
     "Normally positive (both growth proxies). Divergence flags which market is mispricing growth."),
    ("hy_spx", "HYOAS", "SPX", "chg", "ret", "HY spreads vs equities",
     "Normally negative. Equities rallying while spreads widen = credit leading equities lower."),
    ("usdjpy_10y", "USDJPY", "UST10Y", "ret", "chg", "USD/JPY vs UST 10Y",
     "Normally positive (rate differential). Breakdown = intervention, repatriation or carry unwind."),
    ("inr_dxy", "USDINR", "DXY", "ret", "ret", "USD/INR vs Dollar",
     "Normally positive. Low beta = RBI smoothing; rising beta = RBI stepping back."),
    ("btc_ndx", "BTC", "NDX", "ret", "ret", "Bitcoin vs Nasdaq",
     "Positive = BTC trading as liquidity/risk asset; decoupling = idiosyncratic flow."),
    ("audjpy_spx", "AUDJPY", "SPX", "ret", "ret", "AUD/JPY vs equities",
     "Carry/risk barometer. Divergence warns of carry stress before equities react."),
    ("copper_cnh", "COPPER", "USDCNH", "ret", "ret", "Copper vs USD/CNH",
     "Normally negative (weaker CNH = weaker China demand). Breakdown = supply-driven copper."),
]

# ─── Engine parameters ────────────────────────────────────────────────────────
HISTORY_YEARS = 6
CORR_WINDOW_WEEKS = 26          # rolling correlation window (weekly returns)
CORR_BASELINE_YEARS = 5         # baseline distribution of the rolling correlation
CORR_BREAK_Z = 1.75             # |z| of current corr vs baseline to flag
CORR_SIGN_FLIP_MIN = 0.25       # min |corr| on both sides to call a sign flip

VOL_MOVE_Z = 2.0                # 5-day vol-adjusted move threshold
COT_EXTREME_PCT = 90            # percentile (3y) for crowded positioning
COT_LOOKBACK_WEEKS = 156

MAX_SIGNALS_TO_LLM = 18

# Claude settings (override via env)
DEFAULT_MODEL = "claude-opus-5-5"
NEWS_MAX_SEARCHES = 6
