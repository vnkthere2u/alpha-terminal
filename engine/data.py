"""Data ingestion: yfinance (prices), FRED (rates/credit/liquidity), CFTC (positioning).

Every fetcher degrades gracefully: a failed source is recorded in `gaps` and the
rest of the pipeline continues. Nothing is ever filled with invented values.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 30
UA = {"User-Agent": "alpha-terminal/1.0 (+macro research bot)"}


@dataclass
class MarketData:
    prices: pd.DataFrame                      # daily closes, columns = asset keys + FRED keys + ratios
    fred_units: dict[str, str] = field(default_factory=dict)
    cot: dict[str, pd.DataFrame] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    asof: str = ""


def _retry(fn, attempts: int = 3, base: float = 2.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # network / parsing
            last = e
            time.sleep(base * (2 ** i))
    raise last  # type: ignore[misc]


# ─── yfinance ────────────────────────────────────────────────────────────────
def fetch_yahoo(years: int = config.HISTORY_YEARS) -> tuple[pd.DataFrame, list[str]]:
    import yfinance as yf

    sym_to_key = {sym: key for key, (sym, _, _) in config.ASSETS.items()}
    symbols = list(sym_to_key)
    gaps: list[str] = []

    def _download(syms):
        df = yf.download(
            syms, period=f"{years}y", interval="1d", auto_adjust=True,
            group_by="column", threads=True, progress=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
        if not isinstance(df.columns, pd.MultiIndex):
            close.columns = syms
        return close

    closes = pd.DataFrame()
    try:
        closes = _retry(lambda: _download(symbols))
    except Exception as e:
        log.warning("bulk yahoo download failed: %s", e)

    # Retry any symbol that came back empty, one at a time (Yahoo throttles bulk calls)
    missing = [s for s in symbols if s not in closes.columns or closes[s].dropna().empty]
    for s in missing:
        try:
            one = _retry(lambda s=s: _download([s]), attempts=2)
            if not one.empty and not one.iloc[:, 0].dropna().empty:
                closes[s] = one.iloc[:, 0]
                continue
        except Exception as e:
            log.warning("yahoo %s failed: %s", s, e)
        gaps.append(f"price:{sym_to_key[s]}")

    closes = closes.rename(columns=sym_to_key)
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes = closes[[c for c in closes.columns if c in config.ASSETS]]
    return closes.sort_index(), gaps


# ─── FRED ────────────────────────────────────────────────────────────────────
FRED_URL = "https://api.stlouisfed.org/fred"


def _fred_series(series_id: str, api_key: str, start: str) -> tuple[pd.Series, str]:
    obs = requests.get(
        f"{FRED_URL}/series/observations",
        params={"series_id": series_id, "api_key": api_key, "file_type": "json",
                "observation_start": start},
        timeout=HTTP_TIMEOUT, headers=UA,
    )
    obs.raise_for_status()
    rows = obs.json().get("observations", [])
    s = pd.Series(
        {pd.Timestamp(r["date"]): (float(r["value"]) if r["value"] not in (".", "") else np.nan)
         for r in rows},
        dtype=float,
    )
    meta = requests.get(
        f"{FRED_URL}/series",
        params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
        timeout=HTTP_TIMEOUT, headers=UA,
    )
    units = ""
    if meta.ok:
        ss = meta.json().get("seriess", [])
        units = ss[0].get("units", "") if ss else ""
    return s, units


def fetch_fred(api_key: str, years: int = config.HISTORY_YEARS) -> tuple[pd.DataFrame, dict, list[str]]:
    start = (date.today() - timedelta(days=365 * years + 30)).isoformat()
    out, units, gaps = {}, {}, []
    if not api_key:
        return pd.DataFrame(), {}, ["fred:no_api_key"]
    for key, (sid, _) in config.FRED.items():
        try:
            s, u = _retry(lambda sid=sid: _fred_series(sid, api_key, start))
            if s.dropna().empty:
                gaps.append(f"fred:{key}")
                continue
            out[key], units[key] = s, u
        except Exception as e:
            log.warning("FRED %s failed: %s", sid, e)
            gaps.append(f"fred:{key}")
    df = pd.DataFrame(out).sort_index()
    return df, units, gaps


def _to_billions(s: pd.Series, units: str) -> pd.Series:
    u = units.lower()
    if "million" in u:
        return s / 1_000.0
    if "trillion" in u:
        return s * 1_000.0
    return s  # assume billions


def add_net_liquidity(panel: pd.DataFrame, units: dict) -> pd.DataFrame:
    """Fed net liquidity = Fed balance sheet - TGA - ON RRP (USD bn)."""
    need = {"WALCL", "TGA", "RRP"}
    if not need.issubset(panel.columns):
        return panel
    w = _to_billions(panel["WALCL"], units.get("WALCL", "")).ffill(limit=10)
    t = _to_billions(panel["TGA"], units.get("TGA", "")).ffill(limit=10)
    r = _to_billions(panel["RRP"], units.get("RRP", "")).ffill(limit=5)
    panel["NETLIQ"] = w - t - r
    return panel


# ─── CFTC COT ────────────────────────────────────────────────────────────────
CFTC_URL = "https://publicreporting.cftc.gov/resource/{ds}.json"


def fetch_cot(weeks: int = config.COT_LOOKBACK_WEEKS + 10) -> tuple[dict[str, pd.DataFrame], list[str]]:
    out, gaps = {}, []
    for key, (ds, code, _) in config.COT_MARKETS.items():
        try:
            def _get(ds=ds, code=code):
                r = requests.get(
                    CFTC_URL.format(ds=ds),
                    params={"cftc_contract_market_code": code,
                            "$order": "report_date_as_yyyy_mm_dd DESC",
                            "$limit": str(weeks)},
                    timeout=HTTP_TIMEOUT, headers=UA,
                )
                r.raise_for_status()
                return r.json()
            rows = _retry(_get)
            if not rows:
                gaps.append(f"cot:{key}")
                continue
            df = pd.DataFrame(rows)
            if ds == config.COT_DISAGG:
                lcol, scol = "m_money_positions_long_all", "m_money_positions_short_all"
            else:
                lcol, scol = "lev_money_positions_long", "lev_money_positions_short"
            if lcol not in df or scol not in df or "open_interest_all" not in df:
                gaps.append(f"cot:{key}:schema")
                continue
            df = pd.DataFrame({
                "date": pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.tz_localize(None),
                "long": pd.to_numeric(df[lcol], errors="coerce"),
                "short": pd.to_numeric(df[scol], errors="coerce"),
                "oi": pd.to_numeric(df["open_interest_all"], errors="coerce"),
            }).dropna().sort_values("date").set_index("date")
            df["net"] = df["long"] - df["short"]
            df["net_oi"] = df["net"] / df["oi"]
            out[key] = df
        except Exception as e:
            log.warning("COT %s failed: %s", key, e)
            gaps.append(f"cot:{key}")
    return out, gaps


# ─── Assemble ────────────────────────────────────────────────────────────────
def build_panel(prices: pd.DataFrame, fred: pd.DataFrame, units: dict) -> pd.DataFrame:
    """Align all series on a business-day calendar.

    Forward-fill is capped so a stale series cannot masquerade as current:
    daily series up to 3 sessions (holiday/timezone mismatch), weekly up to 7.
    """
    idx = pd.bdate_range(
        min([d.index.min() for d in (prices, fred) if not d.empty]),
        max([d.index.max() for d in (prices, fred) if not d.empty]),
    )
    p = prices.reindex(idx).ffill(limit=3) if not prices.empty else pd.DataFrame(index=idx)
    if not fred.empty:
        weekly = {"NFCI", "STLFSI", "WALCL", "TGA", "CLAIMS"}
        f = fred.reindex(fred.index.union(idx)).sort_index()
        for c in f.columns:
            f[c] = f[c].ffill(limit=7 if c in weekly else 3)
        f = f.reindex(idx)
        p = p.join(f, how="left")
        p = add_net_liquidity(p, units)
    for key, (num, den, _) in config.RATIOS.items():
        if num in p and den in p:
            p[key] = p[num] / p[den]
    # Anchor the snapshot to the last completed US cash session. The job runs
    # before the Asian close, so later rows would only hold partial Asia/FX/crypto bars.
    if "SPX" in prices and not prices["SPX"].dropna().empty:
        p = p.loc[: prices["SPX"].dropna().index.max()]
    return p


def load_all(fred_key: str) -> MarketData:
    prices, g1 = fetch_yahoo()
    fred, units, g2 = fetch_fred(fred_key)
    cot, g3 = fetch_cot()
    if prices.empty and fred.empty:
        raise RuntimeError("No market data could be fetched (Yahoo and FRED both failed).")
    panel = build_panel(prices, fred, units)
    return MarketData(prices=panel, fred_units=units, cot=cot, gaps=g1 + g2 + g3,
                      asof=str(panel.index.max().date()))
