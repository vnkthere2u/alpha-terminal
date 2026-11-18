"""Persistence: the dashboard reads these JSON files; the next run reads the previous one."""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
HIST = DATA / "history"


def _clean(o):
    """Replace NaN/inf with None so the output is strict JSON for browsers."""
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def load_previous() -> dict | None:
    f = DATA / "latest.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def save(note: dict) -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    note = _clean(note)
    (DATA / "latest.json").write_text(json.dumps(note, separators=(",", ":"), ensure_ascii=False))
    # Slim archive copy: drop chart histories, keep the analysis and signal tape
    q = note["quant"]
    slim = {**{k: v for k, v in note.items() if k != "prev_signals_ref"}, "quant": {"asof": q["asof"], "snapshot": q["snapshot"], "signals": q["signals"],
                              "regime": {k: {kk: vv for kk, vv in v.items() if kk not in ("history", "path")}
                                         if isinstance(v, dict) else v for k, v in q["regime"].items()},
                              "relationships": [{k: v for k, v in r.items() if k != "history"}
                                                for r in q["relationships"]],
                              "positioning": [{k: v for k, v in r.items() if k != "history"}
                                              for r in q["positioning"]]}}
    (HIST / f"{note['note_date']}.json").write_text(json.dumps(slim, separators=(",", ":"), ensure_ascii=False))
    dates = sorted((p.stem for p in HIST.glob("*.json")), reverse=True)
    (DATA / "index.json").write_text(json.dumps({"dates": dates}))
