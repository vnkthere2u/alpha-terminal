# AlphaTerminal — macro regime monitor

A scheduled engine that tracks global macro markets, finds trend and regime changes
with fixed quantitative rules, and has Google Gemini (free tier) turn them into a short brief. The brief
goes to **Telegram at about 07:00 Dubai time** and to a **static dashboard on GitHub Pages**.
There is no server, so nothing can go to sleep.

- **Mon–Fri:** daily brief (what changed in the last session).
- **Sunday:** weekly review (medium and long-term trends, positioning, relationships that broke or recovered).
- **No trade ideas.** The brief is for knowledge and context only.

## How it works

```
GitHub Actions (cron 02:41 UTC)
 ├─ engine/data.py      Yahoo Finance (~40 assets, 6y history) · FRED (rates, credit, liquidity) · CFTC COT
 ├─ engine/signals.py   Fixed rules, no AI:
 │     trend         200d crosses after long regimes, 50/200 crosses, fresh 52-week extremes, 3m-vs-12m reversals
 │     move          weekly moves of 2σ or more, scaled to each asset's own volatility
 │     behaviour     how pullbacks/rallies resolve over the last 9 months vs the 5-year base rate
 │                   ("gold dips are bought within days, history says 48%")
 │     relationship  26-week rolling correlations vs 5-year norm for 11 links that usually hold
 │                   (gold vs real yields, oil vs breakevens, stocks vs bonds, USD/INR vs DXY …)
 │     regime        risk-appetite composite · growth/inflation quadrant · curve shape · VIX term
 │                   structure · HY spreads · Fed net liquidity · term premium
 │     positioning   CFTC managed-money / leveraged-fund 3-year percentiles and 4-week swings
 │     india         USD/INR volatility vs DXY (RBI smoothing) · Nifty-in-USD vs EM (FPI rotation)
 │     New signals rank above ongoing ones, so the brief favours what changed
 ├─ engine/news.py      Free headlines: Google News RSS topic searches + Fed/ECB/RBI/BoJ press feeds
 ├─ engine/analyst.py   Gemini (free tier), 2-3 calls per run: (1) news scan that also looks for the
 │                      causes of flagged moves, using Google Search grounding when the free tier
 │                      allows it, otherwise the headlines only; (2) synthesis into JSON with a
 │                      fixed schema. The first available model is picked from a preference list,
 │                      skipping models that are retired or out of daily quota. Every figure in the
 │                      output is checked against the inputs; untraceable figures are listed as caveats
 ├─ engine/telegram.py  HTML brief, split safely at Telegram's 4,096-character limit
 └─ docs/               Static dashboard reading docs/data/*.json; each edition is archived
```

If Gemini or the news scan fails, the brief still goes out with quant signals only and
labels itself as such. If the whole run fails, Telegram gets a failure alert.

## One-time setup

1. **Telegram bot**
   - In Telegram, message **@BotFather** → `/newbot` → copy the token.
   - Send any message to your new bot.
   - Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
2. **Repository secrets** (Settings → Secrets and variables → Actions → *New repository secret*):

   | Secret | Value |
   |---|---|
   | `FRED_API_KEY` | your FRED key |
   | `GEMINI_API_KEY` | free key from aistudio.google.com → *Get API key* (no billing needed) |
   | `TELEGRAM_BOT_TOKEN` | from BotFather |
   | `TELEGRAM_CHAT_ID` | from getUpdates |

   Optional repository **variable**: `GEMINI_MODEL`, to force a specific model. By default the
   engine tries `gemini-flash-latest`, then `gemini-2.5-flash`, then the Flash-Lite versions.
3. **Pages:** Settings → Pages → Source: **GitHub Actions**.
4. **Merge to the default branch.** GitHub only runs scheduled workflows from the default branch.
5. **First run:** Actions → *Macro brief* → *Run workflow*. Turn *Run Gemini analysis* off
   for a free first test of the data feeds.

The dashboard will be at `https://<owner>.github.io/alpha-terminal/`. The repo is public,
so the dashboard is public too.

## Running locally

```bash
pip install -r requirements.txt pytest
python -m pytest -q tests                                  # offline unit tests
python -m engine.run --synthetic --no-llm --no-telegram --no-save --print
FRED_API_KEY=... GEMINI_API_KEY=... python -m engine.run --no-telegram --print
```

## Cost

**$0.** GitHub Actions and Pages are free for a public repo, the news feeds are free RSS, and the
engine needs 2–3 Gemini calls a day, well inside free-tier quotas.

Free-tier trade-offs:
- **Your prompts may be used by Google.** Google's terms allow free-tier inputs to be used to
  improve its products. The inputs here are public market data and headlines.
- **Search may not be available.** Google Search grounding is free only on some models
  (reportedly the 2.5 Flash family). On models where it isn't free, the news scan works from
  RSS headlines only, which carry fewer exact figures. The dashboard footer shows which mode
  each edition used.
- **Quotas are small and change.** Some newer Flash models reportedly allow only about 20
  requests a day on the free tier. That is enough for this job, and the engine falls back to
  Flash-Lite if a quota runs out.

## Limits

- **Prices are free Yahoo data.** Yahoo sometimes throttles GitHub runners. Missing series are
  listed under *Missing data*, never filled in.
- **This is a morning batch job.** It doesn't watch markets during the day.
- **No proprietary flow data.** It has no dealer gamma, prime-broker flows or options flow.
  Positioning comes only from the weekly CFTC report.
- **India FPI flow figures come from the news scan**, not a direct feed, because NSE/NSDL block
  automated requests.
- **No economic calendar.** There is no reliable free source of consensus forecasts.
