"""Synthetic market data for offline tests (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine import config
from engine.data import add_net_liquidity, build_panel

LEVELS = {
    "UST3M": 4.3, "UST2Y": 4.0, "UST10Y": 4.2, "UST30Y": 4.5, "REAL10Y": 1.9, "BE10Y": 2.3,
    "FWD5Y5Y": 2.4, "TP10Y": 0.5, "HYOAS": 3.2, "IGOAS": 0.9, "EFFR": 4.3, "USDBROAD": 120,
    "NFCI": -0.5, "STLFSI": -0.4, "WALCL": 6_700_000, "TGA": 750, "RRP": 300, "CLAIMS": 220_000,
}


def make(seed: int = 7, years: int = 6, gold_regime_break: bool = True):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp("2026-11-17"), periods=252 * years)
    n = len(idx)
    common = rng.normal(0, 0.006, n)
    prices = {}
    for i, key in enumerate(config.ASSETS):
        vol = 0.01 + 0.01 * (i % 4) / 3
        drift = 0.0002 * ((i % 5) - 2)
        r = drift + 0.6 * common + rng.normal(0, vol, n)
        prices[key] = 100 * np.exp(np.cumsum(r))
    prices["VIX"] = 15 + 4 * np.abs(np.sin(np.arange(n) / 60)) + rng.normal(0, 0.5, n)
    prices["VIX3M"] = prices["VIX"] + 1.5
    prices["VIX"][-3:] = prices["VIX3M"][-3:] + 2  # inverted term structure at the end
    pdf = pd.DataFrame(prices, index=idx)

    fred = {}
    for key in config.FRED:
        base = LEVELS[key]
        step = 0.03 if base < 10 else base * 0.002
        fred[key] = base + np.cumsum(rng.normal(0, step, n))
    real_chg = np.diff(fred["REAL10Y"], prepend=fred["REAL10Y"][0])
    # Historically gold strongly inversely tied to real yields...
    noise = rng.normal(0, 0.006, n)
    gr = noise - 0.3 * real_chg
    if gold_regime_break:
        cut = n - 150  # ...but in the last ~7 months the link flips
        gr[cut:] = noise[cut:] + 0.3 * real_chg[cut:] + 0.0015
    pdf["GOLD"] = 100 * np.exp(np.cumsum(gr))
    fdf = pd.DataFrame(fred, index=idx)
    units = {"WALCL": "Millions of U.S. Dollars", "TGA": "Billions of U.S. Dollars",
             "RRP": "Billions of U.S. Dollars"}
    panel = build_panel(pdf, fdf, units)

    cot = {}
    widx = pd.date_range(end="2026-11-11", periods=170, freq="W-TUE")
    for key in config.COT_MARKETS:
        oi = 500_000 + rng.normal(0, 10_000, len(widx))
        net = np.cumsum(rng.normal(0, 8_000, len(widx)))
        if key == "GOLD":
            net[-1] = net.max() + 30_000
        df = pd.DataFrame({"long": np.maximum(net, 0) + 100_000, "short": np.maximum(-net, 0) + 100_000,
                           "oi": oi}, index=widx)
        df["net"] = df["long"] - df["short"]
        df["net_oi"] = df["net"] / df["oi"]
        cot[key] = df
    return panel, cot, units
