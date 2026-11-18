"""Telegram delivery (Bot API, HTML parse mode)."""

from __future__ import annotations

import html
import logging
import re
import time

import requests

log = logging.getLogger(__name__)
LIMIT = 4000  # Telegram hard limit is 4096 chars per message

ARROW = {"emerging": "◔", "strengthening": "▲", "fading": "▽", "broken": "✕", "ongoing": "●"}
TAPE = ["SPX", "NIFTY", "UST10Y", "S2S10", "DXY", "USDINR", "GOLD", "BRENT", "HYOAS", "VIX", "BTC"]


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _tape(q: dict) -> str:
    snap = {r["key"]: r for r in q.get("snapshot", [])}
    parts = []
    for k in TAPE:
        r = snap.get(k)
        if not r:
            continue
        w = r.get("w1")
        if w is None:
            chg = ""
        elif r["is_rate"]:
            chg = f" {w * 100:+.0f}bp"
        else:
            chg = f" {w * 100:+.1f}%"
        parts.append(f"{esc(r['name'])} {esc(r['last_fmt'])}{chg}")
    ra = q.get("regime", {}).get("risk_appetite")
    if ra:
        parts.append(f"Risk appetite {ra['value']}/100")
    return " · ".join(parts)


def format_brief(note: dict) -> str:
    a, q = note.get("analysis"), note["quant"]
    title = "Weekly Macro Review" if note["mode"] == "weekly" else "Morning Macro Brief"
    out = [f"<b>📊 AlphaTerminal · {title}</b>\n<i>{esc(note['note_label'])} · data to {esc(q['asof'])} US close</i>"]
    if a:
        rg = a["regime"]
        out.append(f"<b>Regime: {esc(rg['label'])}</b>\n{esc(rg['one_liner'])}"
                   + (f"\n<i>vs prior:</i> {esc(rg['shift_vs_prior'])}" if rg.get("shift_vs_prior") else ""))
        out.append("<b>Bottom line</b>\n" + "\n".join(f"• {esc(b)}" for b in a["bottom_line"]))
        if a["developments"]:
            items = []
            for i, d in enumerate(a["developments"], 1):
                items.append(f"{i}. <b>{esc(d['headline'])}</b> — {esc(d['what_happened'])}\n"
                             f"   <i>So what:</i> {esc(d['so_what'])}\n"
                             f"   <i>2nd order:</i> {esc(d['second_order'])}")
            out.append("<b>What matters</b>\n" + "\n".join(items))
        if a["trends"]:
            order = {"short": 0, "medium": 1, "long": 2}
            items = [f"{ARROW.get(t['status'], '•')} <b>[{t['horizon'].title()} · {esc(t['status'])}] {esc(t['title'])}</b>\n"
                     f"   {esc(t['evidence'])}\n   → {esc(t['read'])}"
                     for t in sorted(a["trends"], key=lambda t: order.get(t["horizon"], 9))]
            out.append("<b>Trends &amp; regime shifts</b>\n" + "\n".join(items))
        hs = a["hidden_signal"]
        out.append(f"<b>🔍 Under the radar: {esc(hs['title'])}</b>\n{esc(hs['evidence'])}\n"
                   f"<i>Why it's missed:</i> {esc(hs['why_consensus_misses'])}")
        out.append(f"<b>🇮🇳🇦🇪 India / Gulf lens</b>\n{esc(a['india_gulf'])}")
        if a["watch"]:
            out.append("<b>Watch</b>\n" + "\n".join(f"• {esc(w['trigger'])} → {esc(w['implication'])}"
                                                   for w in a["watch"]))
    else:
        out.append("<b>⚠ AI synthesis unavailable today — quant signals only.</b>")
        sigs = note["quant"]["signals"][:10]
        out.append("<b>Top signals</b>\n" + "\n".join(
            f"• [{s['horizon']}] <b>{esc(s['title'])}</b> — {esc(s['detail'])}" for s in sigs))
    out.append(f"<b>Tape (1w)</b>\n<code>{_tape(q)}</code>")
    caveats = list((a or {}).get("data_caveats", [])) + note.get("audit", [])
    if note.get("gaps"):
        caveats.append(f"Missing data: {', '.join(note['gaps'][:8])}" + (" …" if len(note['gaps']) > 8 else ""))
    if caveats:
        out.append("<i>Caveats: " + esc(" · ".join(caveats)) + "</i>")
    if note.get("dashboard_url"):
        out.append(f'<a href="{esc(note["dashboard_url"])}">Open dashboard →</a>')
    return "\n\n".join(out)


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Split on section boundaries, then lines, so HTML tags are never cut."""
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        piece = block if not cur else "\n\n" + block
        if len(cur) + len(piece) <= limit:
            cur += piece
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if len(block) <= limit:
            cur = block
        else:
            for line in block.split("\n"):
                add = line if not cur else "\n" + line
                if len(cur) + len(add) > limit:
                    chunks.append(cur)
                    cur = line[:limit]
                else:
                    cur += add
    if cur:
        chunks.append(cur)
    return chunks


def send(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split(text):
        for attempt in range(3):
            r = requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                         "disable_web_page_preview": True}, timeout=30)
            if r.ok:
                break
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)))
                continue
            if r.status_code == 400 and "parse" in r.text.lower():
                # Malformed HTML should never block delivery: resend as plain text
                plain = html.unescape(re.sub(r"<[^>]+>", "", chunk))
                r = requests.post(url, json={"chat_id": chat_id, "text": plain}, timeout=30)
                if r.ok:
                    break
            log.error("Telegram send failed (%s): %s", r.status_code, r.text[:300])
            if attempt == 2:
                r.raise_for_status()
            time.sleep(2 ** attempt)
        time.sleep(1)
