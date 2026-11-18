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


class _FakeAPIError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _fake_analyst(handler):
    """Analyst wired to a fake Gemini client; handler(model, contents, cfg) -> text or raises."""
    from types import SimpleNamespace as NS

    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append((model, contents, config))
            text = handler(model, contents, config)
            gm = NS(web_search_queries=["q1", "q2"]) if config.get("tools") else None
            return NS(text=text, candidates=[NS(grounding_metadata=gm, finish_reason="STOP")],
                      usage_metadata=NS(prompt_token_count=1000, candidates_token_count=200,
                                        thoughts_token_count=50, tool_use_prompt_token_count=0))

    an = analyst.Analyst.__new__(analyst.Analyst)
    an.client = NS(models=Models())
    an.types = NS(GenerateContentConfig=lambda **kw: kw, Tool=lambda **kw: {"tool": kw},
                  GoogleSearch=lambda: "google_search")
    an.errors = NS(APIError=_FakeAPIError)
    an.usage = {"input_tokens": 0, "output_tokens": 0, "searches": 0, "calls": 0}
    an.grounded = False
    an.models = list(analyst.MODEL_PREFS)
    an.model = an.models[0]
    return an, calls


def test_gemini_flow_grounded():
    def handler(model, contents, cfg):
        if cfg.get("tools"):
            return "[17 Nov] [US] CPI 3.1% vs 3.0% (BLS)"
        return json.dumps(SAMPLE)
    an, calls = _fake_analyst(handler)
    news = an.news("Wed", "Tue", ["Gold regime break"], "[17 Nov] headline (Reuters)")
    assert news.startswith("[17 Nov]") and an.grounded and an.usage["searches"] == 2
    out = an.synthesise("daily", "Wed", "2026-11-17", "digest", news, "none", "n/a")
    assert out["regime"]["label"] == "Late-cycle stress"
    assert calls[-1][2]["response_json_schema"] == analyst.SCHEMA
    assert "headline (Reuters)" in calls[0][1]


def test_gemini_falls_back_to_headlines_and_next_model():
    def handler(model, contents, cfg):
        if cfg.get("tools"):
            raise _FakeAPIError(429, "Quota exceeded for grounding: limit 0 per day")
        if model == analyst.MODEL_PREFS[0]:
            raise _FakeAPIError(404, "model not found")
        if "response_json_schema" in cfg:
            return "```json\n" + json.dumps({"regime": {"label": "X"}}) + "\n```"
        return "[17 Nov] headline-only fact"
    an, calls = _fake_analyst(handler)
    news = an.news("Wed", "Tue", [], "[17 Nov] headline (FT)")
    assert news == "[17 Nov] headline-only fact" and not an.grounded
    assert "no search tool" in calls[-1][1]
    out = an.synthesise("daily", "Wed", "2026-11-17", "d", news, "none", "n/a")
    assert out["regime"]["label"] == "X" and out["trends"] == [] and out["hidden_signal"]["title"] == ""
    assert an.model == analyst.MODEL_PREFS[1]


def test_schema_dialect_fallback_strips_additional_properties():
    s = analyst._schema_without_additional(analyst.SCHEMA)
    assert "additionalProperties" not in json.dumps(s)
    assert "additionalProperties" in json.dumps(analyst.SCHEMA)
