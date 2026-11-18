import json

import numpy as np
import pandas as pd

from engine import signals
from engine.signals import _episodes, run_all
from tests.synthetic import make


def test_run_all_shapes():
    panel, cot, _ = make()
    res = run_all(panel, cot)
    assert res["asof"] == "2026-11-17"
    assert res["signals"], "expected some signals"
    json.dumps(res)  # serialisable
    keys = {r["key"] for r in res["snapshot"]}
    assert {"GOLD", "UST10Y", "S2S10", "NETLIQ", "GOLD_AED_G"} <= keys
    for s in res["signals"]:
        assert 0 <= s["severity"] <= 10
        assert s["horizon"] in {"short", "medium", "long"}


def test_gold_real_yield_break_detected():
    panel, cot, _ = make(gold_regime_break=True)
    res = run_all(panel, cot)
    rel = {r["key"]: r for r in res["relationships"]}["gold_real"]
    assert rel["baseline_median"] < -0.3
    assert rel["corr_26w"] > 0.2
    assert rel["state"] == "sign flip"
    assert any(s["id"] == "relationship:gold_real" for s in res["signals"])


def test_no_break_when_relationship_intact():
    panel, cot, _ = make(gold_regime_break=False)
    res = run_all(panel, cot)
    rel = {r["key"]: r for r in res["relationships"]}["gold_real"]
    assert rel["state"] in {"intact", "weakening"}


def test_vix_inversion_and_cot_extreme():
    panel, cot, _ = make()
    ids = {s["id"] for s in run_all(panel, cot)["signals"]}
    assert "regime:vix_backwardation" in ids
    assert "positioning:GOLD:extreme_long" in ids


def test_novelty_tracking():
    panel, cot, _ = make()
    first = run_all(panel, cot)["signals"]
    second = run_all(panel, cot, previous=first)["signals"]
    assert all(s["status"] == "ongoing" and s["days_active"] == 2 for s in second)


def test_net_liquidity_units():
    panel, _, _ = make()
    # WALCL 6.7tn (millions) - TGA 750bn - RRP 300bn ~ 5.6tn, in billions
    assert 2_000 < panel["NETLIQ"].iloc[0] < 9_000


def test_episode_detection_recovered_vs_failed():
    idx = pd.bdate_range("2020-01-01", periods=400)
    rng = np.random.default_rng(1)
    base = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.005, 400)))
    s = pd.Series(base, index=idx)
    s.iloc[200:210] *= 0.9           # sharp dip...
    eps = _episodes(s)
    assert any(e["start"] >= idx[200] for e in eps)


def test_fmt_helpers():
    assert signals._fmt_chg("UST10Y", 0.25) == "+25bp"
    assert signals._fmt_chg("GOLD", 0.0123) == "+1.2%"
