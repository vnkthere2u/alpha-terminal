"""Quantitative signal engine.

Deterministic, rule-based detection of macro trends and regime shifts across
short (1-4w), medium (1-6m) and long (6m+) horizons. The LLM layer only
interprets what this module finds; it never originates numbers.

Signal families
  trend        - 200d/50d regime changes, fresh 52w / 3m breakouts, trend reversals
  move         - volatility-adjusted weekly moves (|z| >= 2)
  behaviour    - how corrections/rallies resolve vs history ("dips no longer bought")
  relationship - historical cross-asset correlations breaking down or flipping sign
  regime       - risk appetite composite, growth/inflation quadrant, curve shape,
                 VIX term structure, credit, Fed net liquidity
  positioning  - CFTC managed-money / leveraged-fund extremes and swings
  india        - USD/INR smoothing (RBI), India vs EM rotation
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import config

TD_W, TD_M, TD_Q, TD_Y = 5, 21, 63, 252

# Series quoted as levels (percent / index points) -> changes in bp or points, not %
RATE_KEYS = {"UST3M", "UST2Y", "UST10Y", "UST30Y", "REAL10Y", "BE10Y", "FWD5Y5Y",
             "TP10Y", "HYOAS", "IGOAS", "EFFR", "S2S10", "S3M10"}
INDEX_LEVEL_KEYS = {"NFCI", "STLFSI"}
LEVEL_KEYS = RATE_KEYS | INDEX_LEVEL_KEYS | {"NETLIQ"}

SNAPSHOT_KEYS = [
    "SPX", "NDX", "SX5E", "NKY", "HSI", "CSI300", "NIFTY", "EEM",
    "UST2Y", "UST10Y", "S2S10", "S3M10", "REAL10Y", "BE10Y", "TP10Y",
    "HYOAS", "IGOAS", "VIX", "MOVE", "DXY", "EURUSD", "USDJPY", "USDCNH", "USDINR",
    "GOLD", "SILVER", "COPPER", "WTI", "BRENT", "NATGAS", "BTC",
    "NETLIQ", "NFCI", "GOLD_SILVER", "COPPER_GOLD", "BREADTH", "CYC_DEF",
]

BEHAVIOUR_KEYS = ["GOLD", "SILVER", "WTI", "COPPER", "SPX", "NDX", "NIFTY", "BTC",
                  "DXY", "USDINR", "USDJPY", "TLT", "EEM", "HSI"]


@dataclass
class Signal:
    id: str
    family: str
    horizon: str          # short | medium | long
    severity: float       # 0-10
    title: str
    detail: str
    asset: str = ""
    status: str = "new"   # new | ongoing (set against previous run)
    days_active: int = 1

    def to_dict(self):
        d = asdict(self)
        d["severity"] = round(self.severity, 1)
        return d


def name_of(key: str) -> str:
    if key in config.ASSETS:
        return config.ASSETS[key][1]
    if key in config.FRED:
        return config.FRED[key][1]
    if key in config.RATIOS:
        return config.RATIOS[key][2]
    return {"S2S10": "UST 2s10s", "S3M10": "UST 3m10y", "NETLIQ": "Fed net liquidity",
            "GOLD_INR": "Gold in INR", "GOLD_AED_G": "Gold AED/gram"}.get(key, key)


def _clip(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return float(max(lo, min(hi, x)))


def _ncdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _fmt_chg(key: str, v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if key in RATE_KEYS:
        return f"{v * 100:+.0f}bp"
    if key in INDEX_LEVEL_KEYS:
        return f"{v:+.2f}"
    if key == "NETLIQ":
        return f"{v:+,.0f}bn"
    return f"{v * 100:+.1f}%"


SPREAD_KEYS = {"S2S10", "S3M10", "HYOAS", "IGOAS", "TP10Y"}


def _fmt_lvl(key: str, v: float) -> str:
    if key in SPREAD_KEYS:
        return f"{v * 100:.0f}bp"
    if key in RATE_KEYS:
        return f"{v:.2f}%"
    if key == "NETLIQ":
        return f"{v:,.0f}bn"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:,.2f}"
    return f"{v:.4f}"


def enrich(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    if {"UST10Y", "UST2Y"} <= set(p.columns):
        p["S2S10"] = p["UST10Y"] - p["UST2Y"]
    if {"UST10Y", "UST3M"} <= set(p.columns):
        p["S3M10"] = p["UST10Y"] - p["UST3M"]
    if {"GOLD", "USDINR"} <= set(p.columns):
        p["GOLD_INR"] = p["GOLD"] * p["USDINR"]
        p["GOLD_AED_G"] = p["GOLD"] / 31.1035 * 3.6725
    return p


def _change(s: pd.Series, key: str, n: int) -> float | None:
    s = s.dropna()
    if len(s) <= n:
        return None
    a, b = s.iloc[-1], s.iloc[-1 - n]
    if key in LEVEL_KEYS:
        return float(a - b)
    return float(a / b - 1) if b else None


# ─── Snapshot ────────────────────────────────────────────────────────────────
def snapshot(p: pd.DataFrame) -> list[dict]:
    rows = []
    for key in SNAPSHOT_KEYS + ["GOLD_INR", "GOLD_AED_G"]:
        if key not in p or p[key].dropna().empty:
            continue
        s = p[key].dropna()
        last5y = s.iloc[-TD_Y * 5:]
        pct = float((last5y <= s.iloc[-1]).mean() * 100)
        rows.append({
            "key": key, "name": name_of(key), "last": float(s.iloc[-1]),
            "last_fmt": _fmt_lvl(key, float(s.iloc[-1])),
            "asof": str(s.index[-1].date()),
            "d1": _change(s, key, 1), "w1": _change(s, key, TD_W),
            "m1": _change(s, key, TD_M), "m3": _change(s, key, TD_Q), "y1": _change(s, key, TD_Y),
            "pct5y": round(pct),
            "is_level": key in LEVEL_KEYS, "is_rate": key in RATE_KEYS,
        })
    return rows


# ─── Trend / breakout / move ─────────────────────────────────────────────────
def _price_like_keys(p: pd.DataFrame) -> list[str]:
    keys = [k for k in config.ASSETS if k in p and k not in {"SPY", "VIX3M"}]
    keys += [k for k in config.RATIOS if k in p]
    return keys


def trend_signals(p: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    for key in _price_like_keys(p) + [k for k in ("UST10Y", "UST2Y", "REAL10Y", "BE10Y", "HYOAS", "S2S10") if k in p]:
        s = p[key].dropna()
        if len(s) < TD_Y + 20:
            continue
        nm = name_of(key)
        is_lvl = key in LEVEL_KEYS
        ma50, ma200 = s.rolling(50).mean(), s.rolling(200).mean()
        last = s.iloc[-1]

        # 1. 200d regime change: price crossed the 200d within last 5 sessions and held
        above = (s > ma200)
        recent = above.iloc[-6:]
        prior_run = 0
        if recent.iloc[-1] != recent.iloc[0] and len(above) > 6:
            prior_run = int((above.iloc[:-6][::-1] != recent.iloc[0]).argmax())
        if recent.iloc[-1] != recent.iloc[0] and recent.iloc[-3:].nunique() == 1 and prior_run >= 20:
            direction = "above" if recent.iloc[-1] else "below"
            dist = (last / ma200.iloc[-1] - 1) if not is_lvl else (last - ma200.iloc[-1])
            out.append(Signal(
                id=f"trend:{key}:200d", family="trend", horizon="medium",
                severity=_clip(4 + min(prior_run, 200) / 50),
                title=f"{nm} crossed {direction} its 200-day average",
                detail=f"Last {_fmt_lvl(key, last)}; {direction} 200d by {_fmt_chg(key, dist)}; "
                       f"prior regime lasted ~{prior_run} sessions.",
                asset=key))

        # 2. 50/200 cross in last 10 sessions
        cross = (ma50 > ma200).astype(int).diff().iloc[-10:]
        if cross.abs().sum() > 0:
            golden = cross[cross != 0].iloc[-1] > 0
            out.append(Signal(
                id=f"trend:{key}:50x200", family="trend", horizon="long",
                severity=5.0,
                title=f"{nm}: {'golden' if golden else 'death'} cross (50d vs 200d)",
                detail=f"50d {_fmt_lvl(key, ma50.iloc[-1])} vs 200d {_fmt_lvl(key, ma200.iloc[-1])}.",
                asset=key))

        # 3. Fresh 52w extreme (first in >= 60 sessions) within last 3 sessions
        hi52, lo52 = s.rolling(TD_Y).max(), s.rolling(TD_Y).min()
        for kind, ext, cmp in (("high", hi52, np.greater_equal), ("low", lo52, np.less_equal)):
            hits = cmp(s.values, ext.values)
            hits_s = pd.Series(hits, index=s.index)
            if hits_s.iloc[-3:].any():
                prev_hits = hits_s.iloc[:-3]
                gap = int(len(prev_hits) - 1 - np.flatnonzero(prev_hits.values)[-1]) if prev_hits.any() else 999
                # Only a breakout after >= 60 sessions without one is news; persistent
                # trends are visible in the snapshot (12m change, 5y percentile).
                if gap >= 60:
                    out.append(Signal(
                        id=f"trend:{key}:52w{kind}", family="trend", horizon="long",
                        severity=_clip(6.5 + (1 if key in LEVEL_KEYS else 0)),
                        title=f"{nm} at fresh 52-week {kind}",
                        detail=f"Last {_fmt_lvl(key, last)}; "
                               + (f"first 52w {kind} in {gap} sessions." if gap < 999 else "first in the sample."),
                        asset=key))

        # 4. Trend reversal: 12m trend strong, 3m move strongly against it
        r = np.log(s).diff() if not is_lvl else s.diff()
        sd = r.iloc[-TD_Y:].std()
        if sd and not np.isnan(sd):
            y = (s.iloc[-1] - s.iloc[-TD_Y]) if is_lvl else math.log(s.iloc[-1] / s.iloc[-TD_Y])
            q = (s.iloc[-1] - s.iloc[-TD_Q]) if is_lvl else math.log(s.iloc[-1] / s.iloc[-TD_Q])
            zy, zq = y / (sd * math.sqrt(TD_Y)), q / (sd * math.sqrt(TD_Q))
            if abs(zy) >= 1.0 and abs(zq) >= 1.25 and np.sign(zy) != np.sign(zq):
                out.append(Signal(
                    id=f"trend:{key}:reversal", family="trend", horizon="medium",
                    severity=_clip(3 + 1.5 * abs(zq)),
                    title=f"{nm}: 3-month move cutting against 12-month trend",
                    detail=f"12m {_fmt_chg(key, y if is_lvl else math.exp(y) - 1)} (z {zy:+.1f}) vs "
                           f"3m {_fmt_chg(key, q if is_lvl else math.exp(q) - 1)} (z {zq:+.1f}).",
                    asset=key))

            # 5. Vol-adjusted weekly move
            sd63 = r.iloc[-TD_Q:].std()
            w = (s.iloc[-1] - s.iloc[-1 - TD_W]) if is_lvl else math.log(s.iloc[-1] / s.iloc[-1 - TD_W])
            zw = w / (sd63 * math.sqrt(TD_W)) if sd63 else 0
            if abs(zw) >= config.VOL_MOVE_Z:
                out.append(Signal(
                    id=f"move:{key}:1w", family="move", horizon="short",
                    severity=_clip(1.5 * abs(zw)),
                    title=f"{nm}: {abs(zw):.1f}σ weekly move",
                    detail=f"1w {_fmt_chg(key, w if is_lvl else math.exp(w) - 1)} vs 3m daily vol.",
                    asset=key))
    return out


# ─── Correction / rally behaviour ────────────────────────────────────────────
def _episodes(s: pd.Series, trig_k: float = 1.5, horizon: int = 40) -> list[dict]:
    """Pullback episodes: drawdown from 20d high exceeding trig_k * 10d vol.

    Each episode records depth (in vol units) and whether the prior peak was
    regained within `horizon` sessions.
    """
    s = s.dropna()
    r = np.log(s).diff()
    vol = r.rolling(TD_Q).std()
    peak20 = s.rolling(20).max()
    vals, peaks, vols = s.values, peak20.values, vol.values
    eps, i, n = [], TD_Q, len(s)
    while i < n:
        if np.isnan(vols[i]) or np.isnan(peaks[i]):
            i += 1
            continue
        trig = math.exp(-trig_k * vols[i] * math.sqrt(10))
        if vals[i] <= peaks[i] * trig:
            peak, v0, start = peaks[i], vols[i], i
            low, j, recovered = vals[i], i, False
            while j < n and j - start <= horizon:
                low = min(low, vals[j])
                if vals[j] >= peak:
                    recovered = True
                    break
                j += 1
            depth = -math.log(low / peak) / (v0 * math.sqrt(10))
            done = recovered or (j - start > horizon)
            eps.append({"start": s.index[start], "depth": depth, "recovered": recovered,
                        "days": j - start, "complete": done})
            i = j + 1
        else:
            i += 1
    return eps


def behaviour_signals(p: pd.DataFrame) -> tuple[list[Signal], list[dict]]:
    out, table = [], []
    for key in BEHAVIOUR_KEYS:
        if key not in p or p[key].dropna().shape[0] < TD_Y * 3:
            continue
        s = p[key].dropna()
        cutoff = s.index[-189]
        nm = name_of(key)
        for direction, label in ((1, "pullbacks"), (-1, "rallies")):
            series = s if direction == 1 else 1 / s
            eps = _episodes(series)
            done = [e for e in eps if e["complete"]]
            recent = [e for e in eps if e["start"] >= cutoff]
            base = [e for e in done if e["start"] < cutoff]
            if len(base) < 6:
                continue
            base_rate = np.mean([e["recovered"] for e in base])
            base_depth = np.mean([e["depth"] for e in base])
            rec_done = [e for e in recent if e["complete"]]
            open_ep = [e for e in recent if not e["complete"]]
            row = {"key": key, "name": nm, "type": label, "base_n": len(base),
                   "base_recovery": round(float(base_rate) * 100),
                   "base_depth": round(float(base_depth), 2),
                   "recent_n": len(recent), "recent_done": len(rec_done)}
            if rec_done:
                rr = float(np.mean([e["recovered"] for e in rec_done]))
                rd = float(np.mean([e["depth"] for e in recent]))
                row.update(recent_recovery=round(rr * 100), recent_depth=round(rd, 2))
                if len(rec_done) >= 2 and abs(rr - base_rate) >= 0.30:
                    stronger = rr > base_rate
                    if direction == 1:
                        title = (f"{nm}: dips being bought far faster than history" if stronger
                                 else f"{nm}: pullbacks no longer recovering")
                        meaning = "persistent underlying bid" if stronger else "dip-buyers exhausted / trend ageing"
                    else:
                        title = (f"{nm}: counter-trend rallies being sold hard" if stronger
                                 else f"{nm}: selloffs no longer extending - rallies sticking")
                        meaning = "persistent supply" if stronger else "sellers exhausted"
                    out.append(Signal(
                        id=f"behaviour:{key}:{label}", family="behaviour", horizon="medium",
                        severity=_clip((3 if len(rec_done) == 2 else 4) + 6 * abs(rr - base_rate)),
                        title=title,
                        detail=(f"Last 9m: {sum(e['recovered'] for e in rec_done)}/{len(rec_done)} {label} fully "
                                f"retraced within 40d ({rr:.0%}) vs 5y base rate {base_rate:.0%} (n={len(base)}); "
                                f"avg depth {rd:.1f}σ vs {base_depth:.1f}σ → {meaning}."),
                        asset=key))
            if open_ep:
                e = open_ep[-1]
                row["open_days"] = int(e["days"])
                row["open_depth"] = round(float(e["depth"]), 2)
                # Open pullback already deeper than 90% of historic episodes
                depths = np.array([x["depth"] for x in base])
                if e["depth"] > np.quantile(depths, 0.9):
                    out.append(Signal(
                        id=f"behaviour:{key}:{label}:deep", family="behaviour", horizon="short",
                        severity=_clip(5 + e["depth"] - np.quantile(depths, 0.9)),
                        title=f"{nm}: current {label[:-1].replace('ie', 'y')} deeper than 90% of past episodes",
                        detail=f"Open {e['days']}d, depth {e['depth']:.1f}σ vs 90th pct "
                               f"{np.quantile(depths, 0.9):.1f}σ - correction is behaving differently.",
                        asset=key))
            table.append(row)
    return out, table


# ─── Relationship monitor ────────────────────────────────────────────────────
def _weekly(p: pd.DataFrame, key: str, kind: str) -> pd.Series | None:
    if key not in p:
        return None
    w = p[key].dropna().resample("W-FRI").last().dropna()
    if kind == "ret":
        w = w[w > 0]
        return np.log(w).diff()
    return w.diff()


def relationship_signals(p: pd.DataFrame) -> tuple[list[Signal], list[dict]]:
    out, table = [], []
    win = config.CORR_WINDOW_WEEKS
    for rk, a, b, ka, kb, nm, hint in config.RELATIONSHIPS:
        ra, rb = _weekly(p, a, ka), _weekly(p, b, kb)
        if ra is None or rb is None:
            continue
        df = pd.concat([ra, rb], axis=1, keys=["a", "b"]).dropna()
        if len(df) < win + 52 * 2:
            continue
        rc = df["a"].rolling(win).corr(df["b"]).dropna()
        rc13 = df["a"].rolling(13).corr(df["b"]).dropna()
        base = rc.iloc[-52 * config.CORR_BASELINE_YEARS:-13]
        cur, med = float(rc.iloc[-1]), float(base.median())
        sd = float(base.std()) or 0.1
        z = (cur - float(base.mean())) / sd
        flip = (np.sign(cur) != np.sign(med) and abs(cur) >= config.CORR_SIGN_FLIP_MIN
                and abs(med) >= config.CORR_SIGN_FLIP_MIN)
        pct = float((base <= cur).mean() * 100)
        state = "sign flip" if flip else ("broken" if abs(z) >= config.CORR_BREAK_Z else
                                          ("weakening" if abs(z) >= 1.0 else "intact"))
        hist = rc.iloc[-156:]
        table.append({
            "key": rk, "name": nm, "corr_26w": round(cur, 2), "corr_13w": round(float(rc13.iloc[-1]), 2),
            "corr_26w_3m_ago": round(float(rc.iloc[-14]), 2) if len(rc) > 14 else None,
            "baseline_median": round(med, 2), "z": round(z, 2), "pct": round(pct), "state": state,
            "hint": hint,
            "history": [[str(d.date()), round(float(v), 3)] for d, v in hist.items()],
        })
        if state in ("sign flip", "broken"):
            out.append(Signal(
                id=f"relationship:{rk}", family="relationship",
                horizon="long" if abs(float(rc.iloc[-14]) - med) > sd else "medium",
                severity=_clip(2 * abs(z) + (3 if flip else 0)),
                title=f"{nm}: historical relationship {'has flipped sign' if flip else 'has broken down'}",
                detail=(f"26w corr {cur:+.2f} (13w {float(rc13.iloc[-1]):+.2f}) vs 5y median {med:+.2f}; "
                        f"z {z:+.1f}, 5y pctile {pct:.0f}. Context: {hint}"),
                asset=a))
    return out, table


# ─── Regime composites ───────────────────────────────────────────────────────
RISK_COMPONENTS = [  # (key, sign)
    ("HYOAS", -1), ("VIX", -1), ("MOVE", -1), ("VIXTS", +1), ("AUDJPY", +1),
    ("BREADTH", +1), ("CYC_DEF", +1), ("HY_UST", +1), ("EM_US", +1), ("COPPER_GOLD", +1),
]


def _z_rolling(s: pd.Series, n: int = TD_Y) -> pd.Series:
    m, sd = s.rolling(n, min_periods=n // 2).mean(), s.rolling(n, min_periods=n // 2).std()
    return (s - m) / sd


def regime_signals(p: pd.DataFrame) -> tuple[list[Signal], dict]:
    out: list[Signal] = []
    reg: dict = {}
    q = p.copy()
    if {"VIX", "VIX3M"} <= set(q.columns):
        q["VIXTS"] = q["VIX3M"] / q["VIX"]

    # Risk appetite composite (0-100)
    zs = [sign * _z_rolling(q[k]) for k, sign in RISK_COMPONENTS if k in q and q[k].notna().sum() > TD_Y]
    if len(zs) >= 5:
        comp = pd.concat(zs, axis=1).mean(axis=1, skipna=True).dropna()
        idx = comp.apply(lambda z: 100 * _ncdf(float(z) * 1.5))
        now, m1 = float(idx.iloc[-1]), float(idx.iloc[-1 - TD_M])
        d20 = idx.diff(TD_M).dropna()
        zchg = (now - m1) / (float(d20.std()) or 1)
        reg["risk_appetite"] = {
            "value": round(now), "m1_ago": round(m1), "w1_ago": round(float(idx.iloc[-1 - TD_W])),
            "components": len(zs),
            "history": [[str(d.date()), round(float(v), 1)] for d, v in idx.iloc[-TD_Y * 2::2].items()],
        }
        if abs(zchg) >= 1.5 or (m1 - 50) * (now - 50) < 0 and abs(now - m1) > 15:
            out.append(Signal(
                id="regime:risk_appetite", family="regime", horizon="medium",
                severity=_clip(3 + 1.5 * abs(zchg)),
                title=f"Cross-asset risk appetite {'surging' if now > m1 else 'deteriorating'}",
                detail=f"Composite {now:.0f}/100 vs {m1:.0f} a month ago ({zchg:+.1f}σ 1m change); "
                       f"{len(zs)} components (credit, vol, carry, breadth, cyclicals, EM, copper/gold).",
            ))

    # Growth / inflation quadrant from 3m impulses
    def _imp(key, n=TD_Q):
        if key not in q:
            return None
        s = q[key].dropna()
        ch = s.diff(n) if key in LEVEL_KEYS else np.log(s).diff(n)
        return _z_rolling(ch, TD_Y * 3)
    g_parts = [x for x in (_imp("COPPER_GOLD"), _imp("CYC_DEF"), _imp("UST10Y"), _imp("SMALL_LARGE")) if x is not None]
    i_parts = [x for x in (_imp("BE10Y"), _imp("WTI"), _imp("FWD5Y5Y")) if x is not None]
    if g_parts and i_parts:
        g = pd.concat(g_parts, axis=1).mean(axis=1).dropna()
        inf = pd.concat(i_parts, axis=1).mean(axis=1).dropna()
        both = pd.concat([g, inf], axis=1, keys=["g", "i"]).dropna()

        def quad(gv, iv):
            return ("Reflation" if iv > 0 else "Goldilocks") if gv > 0 else ("Stagflation" if iv > 0 else "Disinflationary slowdown")
        gn, inn = both["g"].iloc[-1], both["i"].iloc[-1]
        gp, ip = both["g"].iloc[-1 - TD_M], both["i"].iloc[-1 - TD_M]
        reg["quadrant"] = {
            "now": quad(gn, inn), "m1_ago": quad(gp, ip),
            "growth_z": round(float(gn), 2), "inflation_z": round(float(inn), 2),
            "path": [[str(d.date()), round(float(r.g), 2), round(float(r.i), 2)]
                     for d, r in both.iloc[-TD_Q * 2::5].iterrows()],
        }
        if quad(gn, inn) != quad(gp, ip) and (abs(gn) > 0.3 or abs(inn) > 0.3):
            out.append(Signal(
                id="regime:quadrant", family="regime", horizon="medium", severity=6.5,
                title=f"Macro quadrant shift: {quad(gp, ip)} → {quad(gn, inn)}",
                detail=f"Market-implied growth impulse z {gn:+.2f} (was {gp:+.2f}), "
                       f"inflation impulse z {inn:+.2f} (was {ip:+.2f}).",
            ))

    # Yield-curve shape regime (20d)
    if {"UST2Y", "UST10Y"} <= set(q.columns):
        y2, y10 = q["UST2Y"].dropna(), q["UST10Y"].dropna()
        d2, d10 = float(y2.iloc[-1] - y2.iloc[-1 - TD_M]), float(y10.iloc[-1] - y10.iloc[-1 - TD_M])
        steep = d10 - d2
        if steep > 0:
            shape = "bull steepener" if d2 < 0 and abs(d2) >= abs(d10) else "bear steepener"
        else:
            shape = "bull flattener" if d10 < 0 else "bear flattener"
        s210 = q["S2S10"].dropna()
        zc = steep / (float(s210.diff(TD_M).dropna().iloc[-TD_Y * 3:].std()) or 1)
        reg["curve"] = {"shape_1m": shape, "d2y_bp": round(d2 * 100), "d10y_bp": round(d10 * 100),
                        "s2s10_bp": round(float(s210.iloc[-1]) * 100),
                        "s3m10_bp": round(float(q["S3M10"].dropna().iloc[-1]) * 100) if "S3M10" in q else None}
        if abs(zc) >= 1.25:
            out.append(Signal(
                id=f"regime:curve:{shape.replace(' ', '_')}", family="regime", horizon="medium",
                severity=_clip(3 + 1.5 * abs(zc)),
                title=f"UST curve: decisive {shape}",
                detail=f"1m: 2Y {d2 * 100:+.0f}bp, 10Y {d10 * 100:+.0f}bp; 2s10s now {s210.iloc[-1] * 100:.0f}bp "
                       f"({zc:+.1f}σ 1m change).",
            ))
        # Un-inversion / re-inversion events in last 10 sessions
        for ck in ("S2S10", "S3M10"):
            if ck in q:
                sg = np.sign(q[ck].dropna().iloc[-11:])
                if sg.nunique() > 1 and sg.iloc[-3:].nunique() == 1:
                    out.append(Signal(
                        id=f"regime:curve:{ck}:sign", family="regime", horizon="long", severity=7.0,
                        title=f"{name_of(ck)} {'dis-inverted' if sg.iloc[-1] > 0 else 're-inverted'}",
                        detail=f"Now {q[ck].dropna().iloc[-1] * 100:+.0f}bp.",
                    ))

    # VIX term structure
    if "VIXTS" in q and q["VIXTS"].notna().any():
        ts = q["VIXTS"].dropna()
        reg["vix_term"] = round(float(ts.iloc[-1]), 3)
        if ts.iloc[-1] < 1.0:
            days = int((ts.iloc[::-1] < 1.0).cumprod().sum())
            out.append(Signal(
                id="regime:vix_backwardation", family="regime", horizon="short",
                severity=_clip(6 + 20 * (1 - ts.iloc[-1])),
                title="VIX curve inverted (spot above 3M) - acute stress pricing",
                detail=f"VIX3M/VIX {ts.iloc[-1]:.2f}; inverted {days} session(s).",
            ))

    # Credit
    if "HYOAS" in q:
        hy = q["HYOAS"].dropna()
        ch = hy.diff(TD_M)
        z = float((ch.iloc[-1]) / (ch.iloc[-TD_Y * 3:].std() or 1))
        pct = float((hy.iloc[-TD_Y * 5:] <= hy.iloc[-1]).mean() * 100)
        reg["credit"] = {"hy_oas_bp": round(float(hy.iloc[-1]) * 100), "hy_1m_bp": round(float(ch.iloc[-1]) * 100),
                         "hy_pct5y": round(pct)}
        if abs(z) >= 1.5:
            out.append(Signal(
                id="regime:credit", family="regime", horizon="medium", severity=_clip(3 + 1.6 * abs(z)),
                title=f"HY spreads {'widening' if z > 0 else 'compressing'} at {abs(z):.1f}σ pace",
                detail=f"HY OAS {hy.iloc[-1] * 100:.0f}bp, 1m {ch.iloc[-1] * 100:+.0f}bp; 5y pctile {pct:.0f}.",
            ))
        elif pct <= 5:
            out.append(Signal(
                id="regime:credit:tight", family="regime", horizon="long", severity=4.5,
                title="HY spreads near 5-year tights - credit priced for perfection",
                detail=f"HY OAS {hy.iloc[-1] * 100:.0f}bp (5y pctile {pct:.0f}).",
            ))

    # Fed net liquidity
    if "NETLIQ" in q and q["NETLIQ"].notna().sum() > TD_Y:
        nl = q["NETLIQ"].dropna()
        c4, c13 = float(nl.iloc[-1] - nl.iloc[-1 - 20]), float(nl.iloc[-1] - nl.iloc[-1 - TD_Q])
        sd = float(nl.diff(20).iloc[-TD_Y * 3:].std() or 1)
        reg["liquidity"] = {"netliq_bn": round(float(nl.iloc[-1])), "chg_4w_bn": round(c4), "chg_13w_bn": round(c13),
                            "history": [[str(d.date()), round(float(v))] for d, v in nl.iloc[-TD_Y * 2::5].items()]}
        if abs(c4 / sd) >= 1.5:
            out.append(Signal(
                id="regime:liquidity", family="regime", horizon="medium", severity=_clip(3 + 1.5 * abs(c4 / sd)),
                title=f"Fed net liquidity {'injection' if c4 > 0 else 'drain'} ({abs(c4 / sd):.1f}σ)",
                detail=f"Net liquidity ${nl.iloc[-1]:,.0f}bn; 4w {c4:+,.0f}bn, 13w {c13:+,.0f}bn (WALCL - TGA - RRP).",
            ))

    # Real yields / breakevens decomposition of 10Y move (3m)
    if {"REAL10Y", "BE10Y", "UST10Y"} <= set(q.columns):
        r3 = float(q["REAL10Y"].dropna().diff(TD_Q).iloc[-1])
        b3 = float(q["BE10Y"].dropna().diff(TD_Q).iloc[-1])
        reg["rates_decomp_3m"] = {"real_bp": round(r3 * 100), "breakeven_bp": round(b3 * 100),
                                  "driver": "real yields" if abs(r3) > abs(b3) else "inflation expectations"}
    if "TP10Y" in q and q["TP10Y"].notna().sum() > TD_Y:
        tp = q["TP10Y"].dropna()
        pct = float((tp.iloc[-TD_Y * 5:] <= tp.iloc[-1]).mean() * 100)
        reg["term_premium"] = {"value_bp": round(float(tp.iloc[-1]) * 100), "pct5y": round(pct),
                               "chg_3m_bp": round(float(tp.diff(TD_Q).iloc[-1]) * 100)}
        if pct >= 95 and float(tp.diff(TD_Q).iloc[-1]) > 0:
            out.append(Signal(
                id="regime:term_premium", family="regime", horizon="long", severity=6.0,
                title="UST term premium at 5-year high and rising",
                detail=f"Kim-Wright 10Y TP {tp.iloc[-1] * 100:.0f}bp, 3m {tp.diff(TD_Q).iloc[-1] * 100:+.0f}bp - "
                       f"fiscal/supply risk being priced.",
            ))
    return out, reg


# ─── Positioning (CFTC) ──────────────────────────────────────────────────────
def positioning_signals(cot: dict[str, pd.DataFrame]) -> tuple[list[Signal], list[dict]]:
    out, table = [], []
    for key, df in cot.items():
        if len(df) < 52:
            continue
        nm = config.COT_MARKETS[key][2]
        who = "managed money" if config.COT_MARKETS[key][0] == config.COT_DISAGG else "leveraged funds"
        h = df["net_oi"].iloc[-config.COT_LOOKBACK_WEEKS:]
        pct = float((h <= h.iloc[-1]).mean() * 100)
        pct4 = float((h <= h.iloc[-5]).mean() * 100) if len(h) > 5 else pct
        row = {"key": key, "name": nm, "who": who, "asof": str(df.index[-1].date()),
               "net": int(df["net"].iloc[-1]), "net_oi_pct": round(float(df["net_oi"].iloc[-1]) * 100, 1),
               "pct3y": round(pct), "pct3y_4w_ago": round(pct4),
               "history": [[str(d.date()), round(float(v) * 100, 1)] for d, v in h.iloc[-104:].items()]}
        table.append(row)
        if pct >= config.COT_EXTREME_PCT or pct <= 100 - config.COT_EXTREME_PCT:
            side = "long" if pct >= 50 else "short"
            out.append(Signal(
                id=f"positioning:{key}:extreme_{side}", family="positioning", horizon="medium",
                severity=_clip(3 + abs(pct - 50) / 12.5),
                title=f"{nm}: {who} positioning crowded {side}",
                detail=f"Net {int(df['net'].iloc[-1]):+,} contracts ({df['net_oi'].iloc[-1] * 100:+.1f}% of OI), "
                       f"3y pctile {pct:.0f} (4w ago {pct4:.0f}). Report {row['asof']}.",
                asset=key))
        elif abs(pct - pct4) >= 40:
            out.append(Signal(
                id=f"positioning:{key}:swing", family="positioning", horizon="short",
                severity=_clip(3 + abs(pct - pct4) / 15),
                title=f"{nm}: sharp {who} {'build' if pct > pct4 else 'liquidation'}",
                detail=f"3y percentile {pct4:.0f} → {pct:.0f} in 4 weeks. Report {row['asof']}.",
                asset=key))
    return out, table


# ─── India / Gulf lens ───────────────────────────────────────────────────────
def india_signals(p: pd.DataFrame) -> tuple[list[Signal], dict]:
    out, d = [], {}
    if {"USDINR", "DXY"} <= set(p.columns):
        ri, rd = np.log(p["USDINR"].dropna()).diff(), np.log(p["DXY"].dropna()).diff()
        vi, vd = ri.rolling(TD_M).std() * math.sqrt(TD_Y), rd.rolling(TD_M).std() * math.sqrt(TD_Y)
        ratio = (vi / vd).dropna()
        if len(ratio) > TD_Y * 2:
            pct = float((ratio.iloc[-TD_Y * 3:] <= ratio.iloc[-1]).mean() * 100)
            d["inr_vol_ratio"] = {"value": round(float(ratio.iloc[-1]), 2), "pct3y": round(pct),
                                  "inr_vol_1m": round(float(vi.dropna().iloc[-1]) * 100, 1),
                                  "dxy_vol_1m": round(float(vd.dropna().iloc[-1]) * 100, 1)}
            if pct <= 10 or pct >= 90:
                out.append(Signal(
                    id=f"india:inr_vol:{'low' if pct <= 10 else 'high'}", family="india", horizon="medium",
                    severity=5.5 if pct >= 90 else 4.5,
                    title=("USD/INR volatility suppressed vs DXY - heavy RBI smoothing" if pct <= 10
                           else "USD/INR volatility unusually high vs DXY - RBI stepping back"),
                    detail=f"1m realised vol INR {vi.dropna().iloc[-1] * 100:.1f}% vs DXY {vd.dropna().iloc[-1] * 100:.1f}% "
                           f"(ratio {ratio.iloc[-1]:.2f}, 3y pctile {pct:.0f}).",
                    asset="USDINR"))
    if {"NIFTY", "USDINR", "EEM"} <= set(p.columns):
        nusd = (p["NIFTY"] / p["USDINR"]).dropna()
        rel = (nusd / p["EEM"].reindex(nusd.index)).dropna()
        if len(rel) > TD_Y:
            r3 = float(rel.iloc[-1] / rel.iloc[-1 - TD_Q] - 1)
            r12 = float(rel.iloc[-1] / rel.iloc[-1 - TD_Y] - 1)
            d["nifty_usd_vs_em"] = {"rel_3m": round(r3 * 100, 1), "rel_12m": round(r12 * 100, 1),
                                    "history": [[str(dt.date()), round(float(v / rel.iloc[-TD_Y * 2]) * 100, 1)]
                                                for dt, v in rel.iloc[-TD_Y * 2::5].items()]}
            sd = float(np.log(rel).diff(TD_Q).iloc[-TD_Y * 3:].std() or 1)
            if abs(math.log(1 + r3)) / sd >= 1.5:
                out.append(Signal(
                    id="india:em_rotation", family="india", horizon="medium",
                    severity=_clip(3 + 1.5 * abs(math.log(1 + r3)) / sd),
                    title=f"India {'out' if r3 > 0 else 'under'}performing EM in USD terms - FPI rotation signal",
                    detail=f"Nifty (USD) vs EEM: 3m {r3 * 100:+.1f}%, 12m {r12 * 100:+.1f}%.",
                    asset="NIFTY"))
    return out, d


# ─── Orchestration ───────────────────────────────────────────────────────────
def run_all(panel: pd.DataFrame, cot: dict, previous: list[dict] | None = None) -> dict:
    p = enrich(panel)
    sigs: list[Signal] = []
    sigs += trend_signals(p)
    b_sigs, b_table = behaviour_signals(p)
    r_sigs, r_table = relationship_signals(p)
    g_sigs, regime = regime_signals(p)
    c_sigs, c_table = positioning_signals(cot)
    i_sigs, india = india_signals(p)
    sigs += b_sigs + r_sigs + g_sigs + c_sigs + i_sigs

    # De-duplicate: keep strongest per id
    best: dict[str, Signal] = {}
    for s in sigs:
        if s.id not in best or s.severity > best[s.id].severity:
            best[s.id] = s
    sigs = list(best.values())

    # Novelty vs previous run: ongoing signals are down-weighted so the brief favours change
    prev = {d["id"]: d for d in (previous or [])}
    for s in sigs:
        if s.id in prev:
            s.status = "ongoing"
            s.days_active = int(prev[s.id].get("days_active", 1)) + 1
    sigs.sort(key=lambda s: (s.severity * (1.0 if s.status == "new" else 0.75)), reverse=True)

    return {
        "asof": str(p.index.max().date()),
        "snapshot": snapshot(p),
        "signals": [s.to_dict() for s in sigs],
        "regime": regime,
        "relationships": r_table,
        "behaviour": b_table,
        "positioning": c_table,
        "india": india,
    }
