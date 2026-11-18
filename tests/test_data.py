"""Parsing tests for the network fetchers, using recorded-shape fake responses."""
import pandas as pd

from engine import config, data


class FakeResp:
    def __init__(self, payload, ok=True):
        self._p, self.ok, self.status_code = payload, ok, 200

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


def test_fred_parsing_and_missing_values(monkeypatch):
    def fake_get(url, params=None, **kw):
        if url.endswith("/observations"):
            return FakeResp({"observations": [{"date": "2026-11-13", "value": "4.10"},
                                              {"date": "2026-11-14", "value": "."},
                                              {"date": "2026-11-17", "value": "4.15"}]})
        return FakeResp({"seriess": [{"units": "Percent"}]})
    monkeypatch.setattr(data.requests, "get", fake_get)
    df, units, gaps = data.fetch_fred("k")
    assert not gaps and set(df.columns) == set(config.FRED)
    assert df["UST10Y"].dropna().tolist() == [4.10, 4.15]
    assert units["UST10Y"] == "Percent"


def test_fred_without_key():
    df, _, gaps = data.fetch_fred("")
    assert df.empty and gaps == ["fred:no_api_key"]


def test_cot_parsing(monkeypatch):
    def fake_get(url, params=None, **kw):
        disagg = config.COT_DISAGG in url
        rows = []
        for i, d in enumerate(pd.date_range("2025-01-07", periods=60, freq="W-TUE")):
            r = {"report_date_as_yyyy_mm_dd": d.strftime("%Y-%m-%dT00:00:00.000"), "open_interest_all": "500000"}
            if disagg:
                r.update(m_money_positions_long_all=str(100000 + i * 100), m_money_positions_short_all="50000")
            else:
                r.update(lev_money_positions_long="80000", lev_money_positions_short=str(90000 + i))
            rows.append(r)
        return FakeResp(rows[::-1])
    monkeypatch.setattr(data.requests, "get", fake_get)
    cot, gaps = data.fetch_cot()
    assert not gaps
    assert cot["GOLD"]["net"].iloc[-1] == 100000 + 59 * 100 - 50000
    assert cot["GOLD"].index.is_monotonic_increasing
    assert cot["EUR"]["net"].iloc[-1] < 0


def test_net_liquidity_unit_normalisation():
    idx = pd.bdate_range("2026-01-01", periods=5)
    p = pd.DataFrame({"WALCL": 6_700_000.0, "TGA": 800.0, "RRP": 200.0}, index=idx)
    out = data.add_net_liquidity(p, {"WALCL": "Millions of U.S. Dollars", "TGA": "Billions of U.S. Dollars",
                                     "RRP": "Billions of U.S. Dollars"})
    assert out["NETLIQ"].iloc[-1] == 6_700 - 800 - 200


def test_rss_parsing_and_dedupe(monkeypatch):
    from datetime import datetime, timezone
    from email.utils import format_datetime
    from engine import news
    now = format_datetime(datetime.now(timezone.utc))
    rss = f"""<rss><channel>
      <item><title>Fed holds rates steady - Reuters</title><pubDate>{now}</pubDate><source>Reuters</source></item>
      <item><title>Fed holds rates steady - Bloomberg</title><pubDate>{now}</pubDate></item>
      <item><title>Old story</title><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
    </channel></rss>"""
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: FakeResp(None) if False else type(
        "R", (), {"ok": True, "text": rss})())
    items = news.headlines(days=2)
    titles = [i["title"] for i in items]
    assert titles.count("Fed holds rates steady - Reuters") == 1
    assert not any("Bloomberg" in t for t in titles)  # same story deduped
    assert "Old story" not in titles
    assert "Reuters" in news.as_text(items)
