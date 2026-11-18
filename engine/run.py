"""Entry point.

    python -m engine.run                 # auto mode (Sunday Dubai = weekly, else daily)
    python -m engine.run --mode weekly
    python -m engine.run --no-llm        # quant-only (no Anthropic cost)
    python -m engine.run --no-telegram --print
    python -m engine.run --synthetic     # offline smoke test with synthetic data

Environment: FRED_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
optional CLAUDE_MODEL, CLAUDE_EFFORT, DASHBOARD_URL.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import timedelta

from . import signals, store, telegram
from .analyst import Analyst, audit_numbers, build_digest, dubai_now, prior_summary

log = logging.getLogger("alpha")


def dashboard_url() -> str:
    if os.getenv("DASHBOARD_URL"):
        return os.environ["DASHBOARD_URL"]
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return ""


def news_since(mode: str, now) -> str:
    if mode == "weekly":
        start = now - timedelta(days=7)
    elif now.weekday() == 0:  # Monday: cover Friday session + weekend
        start = now - timedelta(days=3)
    else:
        start = now - timedelta(days=1)
    return start.strftime("%a %d %b %Y %H:%M") + " Dubai time"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "daily", "weekly"], default="auto")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--print", action="store_true", help="print the Telegram text to stdout")
    ap.add_argument("--synthetic", action="store_true", help="offline synthetic data (testing)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    now = dubai_now()
    mode = args.mode if args.mode != "auto" else ("weekly" if now.weekday() == 6 else "daily")
    tg_token, tg_chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    send_tg = not args.no_telegram and tg_token and tg_chat

    try:
        prev = store.load_previous()
        if args.synthetic:
            sys.path.insert(0, str(store.ROOT))
            from tests.synthetic import make
            panel, cot, _ = make()
            gaps: list[str] = ["synthetic-data"]
        else:
            from .data import load_all
            md = load_all(os.getenv("FRED_API_KEY", ""))
            panel, cot, gaps = md.prices, md.cot, md.gaps
        log.info("panel %s, asof %s, gaps %d", panel.shape, panel.index.max().date(), len(gaps))

        prev_signals = prev["quant"]["signals"] if prev and prev.get("quant") else None
        # Only treat a signal as "ongoing" if the previous run used an earlier data date
        if prev and prev.get("quant", {}).get("asof") == str(panel.index.max().date()):
            prev_signals = prev.get("prev_signals_ref") or prev_signals
        q = signals.run_all(panel, cot, previous=prev_signals)
        log.info("%d signals", len(q["signals"]))

        note = {
            "mode": mode,
            "note_date": now.strftime("%Y-%m-%d"),
            "note_label": now.strftime("%a %d %b %Y, %H:%M GST"),
            "generated_at": now.isoformat(),
            "dashboard_url": dashboard_url(),
            "gaps": gaps,
            "quant": q,
            "analysis": None,
            "news": "",
            "audit": [],
            "llm": None,
            "prev_signals_ref": prev_signals,
        }

        if not args.no_llm and os.getenv("ANTHROPIC_API_KEY"):
            digest = build_digest(q, gaps)
            an = Analyst()
            try:
                titles = [s["title"] for s in q["signals"][:8]]
                news = an.news(now.strftime("%a %d %b %Y"), news_since(mode, now), titles)
                note["news"] = news
            except Exception as e:
                log.error("news scan failed: %s", e)
                news = "News scan unavailable today."
                note["gaps"].append("news-scan")
            prior, prior_date = prior_summary(prev)
            try:
                analysis = an.synthesise(mode, now.strftime("%a %d %b %Y"), q["asof"], digest, news,
                                         prior, prior_date)
                note["analysis"] = analysis
                unverified = audit_numbers(analysis, digest + "\n" + news + "\n" + prior)
                if unverified:
                    note["audit"] = [f"{len(unverified)} figure(s) not traceable to source data: "
                                     + ", ".join(unverified[:10])]
            except Exception as e:
                log.error("synthesis failed: %s", e)
                note["gaps"].append("ai-synthesis")
            note["llm"] = {"model": an.model, **an.usage, "est_cost_usd": an.cost_usd()}
            log.info("LLM usage %s", note["llm"])
        elif not args.no_llm:
            note["gaps"].append("no-anthropic-key")

        text = telegram.format_brief(note)
        if args.print:
            print(text)
        if not args.no_save:
            store.save(note)
        if send_tg:
            telegram.send(tg_token, tg_chat, text)
            log.info("Telegram sent")
        return 0
    except Exception as e:
        log.error("run failed: %s\n%s", e, traceback.format_exc())
        if send_tg:
            try:
                telegram.send(tg_token, tg_chat, f"⚠ AlphaTerminal run failed: {telegram.esc(e)}")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
