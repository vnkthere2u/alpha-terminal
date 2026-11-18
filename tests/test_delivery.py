import json

from engine import analyst, telegram
from engine.signals import run_all
from tests.synthetic import make

SAMPLE = {
    "regime": {"label": "Late-cycle stress", "one_liner": "Credit & vol leading equities lower.",
               "shift_vs_prior": "Risk appetite fell to 13 from 66 a month ago."},
    "bottom_line": ["Gold has decoupled from real yields <b>& rates</b>.", "VIX curve inverted."],
    "developments": [{"headline": "Fed holds", "what_happened": "Held at 4.30%.", "so_what": "Curve steepens.",
                      "second_order": "USD/INR pressure.", "assets": "UST, DXY, INR"}],
    "trends": [{"horizon": "long", "status": "emerging", "title": "Gold regime break",
                "evidence": "26w corr +0.87 vs -0.84", "read": "Non-rate buyer."},
               {"horizon": "short", "status": "strengthening", "title": "Vol stress",
                "evidence": "VIX3M/VIX 0.89", "read": "Hedging demand."}],
    "hidden_signal": {"title": "x", "evidence": "y", "why_consensus_misses": "z"},
    "india_gulf": "INR smoothing.", "watch": [{"trigger": "HY OAS > 450bp", "implication": "Credit event risk."}],
    "data_caveats": [],
}


def test_schema_accepts_sample():
    for k in analyst.SCHEMA["required"]:
        assert k in SAMPLE


def test_digest_compact():
    panel, cot, _ = make()
    q = run_all(panel, cot)
    d = analyst.build_digest(q, ["price:MOVE"])
    assert "SNAPSHOT" in d and "SIGNALS" in d and "DATA GAPS: price:MOVE" in d
    assert len(d) < 16000  # ~4k tokens


def test_audit_flags_invented_numbers():
    corpus = "Gold 4,012.30 +1.2% ; HY OAS 324bp ; corr +0.87"
    a = {"x": "Gold at 4012.3 and HY 324bp, corr 0.87, but SPX 6,950 is invented; 3 months; 2026"}
    bad = analyst.audit_numbers(a, corpus)
    assert bad == ["6950"]


def test_telegram_format_escapes_and_splits():
    panel, cot, _ = make()
    q = run_all(panel, cot)
    note = {"mode": "daily", "note_label": "Wed 18 Nov 2026", "quant": q, "analysis": SAMPLE,
            "gaps": [], "audit": [], "dashboard_url": "https://x.github.io/y/"}
    text = telegram.format_brief(note)
    assert "&lt;b&gt;" in text  # model text escaped
    chunks = telegram.split(text * 3)
    assert all(len(c) <= telegram.LIMIT for c in chunks)
    note["analysis"] = None
    assert "quant signals only" in telegram.format_brief(note)


def test_prior_summary():
    s, d = analyst.prior_summary({"analysis": SAMPLE, "note_date": "2026-11-17"})
    assert "Late-cycle stress" in s and d == "2026-11-17"
    assert analyst.prior_summary(None) == ("none", "n/a")


class _Blk:
    def __init__(self, type, text=""):
        self.type, self.text = type, text


class _Usage:
    input_tokens, output_tokens = 1000, 200
    cache_read_input_tokens = cache_creation_input_tokens = 0

    class server_tool_use:
        web_search_requests = 2


class _Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, _Usage()


def test_analyst_flow_with_fake_client():
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            if "tools" in kw:
                if len(calls) == 1:
                    return _Resp([_Blk("text", "Searching…"), _Blk("server_tool_use")], stop="pause_turn")
                return _Resp([_Blk("web_search_tool_result"), _Blk("text", "[17 Nov] [US] CPI 3.1% vs 3.0% (BLS)")])
            return _Resp([_Blk("text", json.dumps(SAMPLE))])

    an = analyst.Analyst.__new__(analyst.Analyst)
    an.client = type("C", (), {"beta": type("B", (), {"messages": FakeMessages()})()})()
    an.model, an.usage = "claude-opus-5-5", {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    news = an.news("Wed 18 Nov 2026", "Tue 17 Nov", ["Gold regime break"])
    assert news == "[17 Nov] [US] CPI 3.1% vs 3.0% (BLS)"
    out = an.synthesise("daily", "Wed", "2026-11-17", "digest", news, "none", "n/a")
    assert out["regime"]["label"] == "Late-cycle stress"
    assert calls[0]["fallbacks"] == "default" and calls[0]["tools"][0]["max_uses"] == 6
    assert calls[-1]["output_config"]["format"]["type"] == "json_schema"
    assert an.usage["searches"] == 6 and an.cost_usd() > 0
