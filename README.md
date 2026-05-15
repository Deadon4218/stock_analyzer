# Stock Signal Analyzer

A Hebrew-Discord-channel-aware stock signal analysis bot. Reads free-form
Hebrew messages and chart screenshots from a Discord channel, extracts
trade signals, runs multi-agent analysis with Groq Llama, and pushes
verdicts to Telegram subscribers.

The bot also responds to inbound Telegram commands instantly via a Vercel
serverless webhook (`/help`, `/add`, `/list`, `/analyze`, `/stats`, etc).

---

## 1. Architecture at a glance

```
                        ┌──────────────────────────────────────────┐
                        │             Upstash Redis                │
                        │  (users, watchlists, analyses, state)    │
                        └────────────▲─────────────────────▲───────┘
                                     │                     │
                                     │ read/write          │ read/write
                                     │                     │
   ┌─────────────┐  webhook    ┌─────┴────────────┐   ┌────┴─────────────┐
   │  Telegram   │ ─────────►  │   Vercel         │   │ GitHub Actions   │
   │   (users)   │ ◄─────────  │  api/telegram.py │   │  (3 workflows)   │
   └──────┬──────┘  reply      └──────────────────┘   └─────────┬────────┘
          │                                                     │
          │ broadcast (sendMessage)                              │ broadcasts
          │  ◄──────────────────────────────────────────────────┘
          │                                                     │
          │                                            ┌────────▼─────────┐
          │                                            │  Discord channel │
          │                                            │  (signal source) │
          └────────────────────────────────────────────┴──────────────────┘
                                                          │
                                                          │ extract signals
                                                          ▼
                                                  ┌───────────────┐
                                                  │   Groq Llama  │
                                                  │  text + vision│
                                                  └───────┬───────┘
                                                          │
                                                          ▼
                                                  ┌───────────────┐
                                                  │   yfinance    │
                                                  │ (market data) │
                                                  └───────────────┘
```

### Two ingress paths

| Path | Trigger | Latency | Use |
|------|---------|---------|-----|
| **Inbound Telegram** (Vercel webhook) | Every user message | ~1 s | `/help`, `/add`, `/list`, `/analyze`, `/stats`, etc |
| **Outbound Discord scan** (GitHub Actions cron) | Every 15 min | ~30-90 s per cycle | Watches Discord, broadcasts new signals to all subscribers |

---

## 2. Where everything is deployed

| Component | Hosted on | Tier | What it does |
|-----------|-----------|------|--------------|
| `api/telegram.py` | **Vercel Serverless Functions** | Hobby (free) | Receives Telegram webhook POSTs, dispatches commands |
| `state_store`, `user_store`, `analyses_log` | **Upstash Redis** | Free | Single source of truth for all state |
| `broadcast.yml` workflow (`run_broadcast.py`) | **GitHub Actions** | Free | Discord scan + agent analysis + Telegram broadcast, every 15 min |
| `personal.yml` workflow (`run_personal.py`) | GitHub Actions | Free | Per-user watchlist analysis, AM + PM on weekdays |
| `outcome_tracker.yml` workflow | GitHub Actions | Free | Once daily, marks past ENTRY calls as win/loss based on price action |
| `screener.yml` workflow (`run_screener.py`) | GitHub Actions | Free | MA150 screener over S&P 500 + NASDAQ-100, twice daily (~1 hr before open, ~15 min after close). Zero LLM tokens — pure price math. |
| LLM inference | **Groq Cloud** | Free | Llama 3.1 8b (text), Llama 4 Scout 17b (vision) |
| Market data | **yfinance** (Yahoo) | Free | Price history, RSI, ATR, MAs |

**Repo:** `github.com/Deadon4218/stock_analyzer` (main branch is production)
**Vercel project:** `stock-analyzer-gfw4.vercel.app`

Total cost: **$0/month** for typical low-volume bot usage.

---

## 3. Setup — from zero to running

Assumes a fresh clone of the repo. Total time: ~20 minutes.

### 3.1 Local environment

```bash
git clone https://github.com/Deadon4218/stock_analyzer.git
cd stock_analyzer
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3.2 Provision external services

| # | Service | What to do |
|---|---------|------------|
| 1 | **Telegram BotFather** | `/newbot` → save the bot token |
| 2 | **Groq Cloud** | console.groq.com → API Keys → create key |
| 3 | **Upstash Redis** | console.upstash.com → Create Database → Redis → Regional, free tier. Copy `REST URL` and `REST TOKEN` |
| 4 | **Vercel** | vercel.com/new → Import the GitHub repo. **Framework Preset = "Other"** (not "Python") |
| 5 | **Discord account** | Get your channel ID (right-click channel → Copy ID with developer mode on) |

### 3.3 Configure secrets

Three places need the same secrets:

**Local `.env`** (for testing / migrations):
```
TELEGRAM_BOT_TOKEN=...
GROQ_API_KEY=...
DISCORD_USER_TOKEN=...
DISCORD_CHANNEL_ID=...
UPSTASH_REDIS_REST_URL=https://your-db.upstash.io
UPSTASH_REDIS_REST_TOKEN=...
```

**GitHub repo → Settings → Secrets → Actions:** same 6 secrets as above.

**Vercel project → Settings → Environment Variables:**
- `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`
- (Vercel doesn't need Discord vars — Discord scanning lives in GitHub Actions)

### 3.4 Initial data migration (one-time, only when migrating from file-based state)

```bash
venv/bin/python _migrate_to_redis.py
```
Copies users, analyzed signal keys, and the analyses log from `data/*.json` into Upstash.

### 3.5 Register Telegram webhook

```bash
venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv()
import os, requests
token = os.environ['TELEGRAM_BOT_TOKEN']
requests.post(
    f'https://api.telegram.org/bot{token}/setWebhook',
    json={'url': 'https://stock-analyzer-gfw4.vercel.app/api/telegram',
          'allowed_updates': ['message', 'edited_message'],
          'drop_pending_updates': True}
)
"
```

### 3.6 Verify

```bash
# Local smoke test (skips Discord, hits Groq + yfinance for one ticker)
venv/bin/python _e2e_test.py

# Or: in Telegram, message the bot /help — should reply in ~1 second
```

---

## 4. Telegram commands

| Command | Action | Speed |
|---------|--------|-------|
| `/start`, `/help` | Show help + register | instant |
| `/add AAPL` | Add ticker to your watchlist | instant |
| `/remove AAPL` | Remove ticker | instant |
| `/list` | Show your watchlist | instant |
| `/analyze AAPL` | One-off full analysis | ~30 s |
| `/stats` | Overall win rate of past calls | instant |
| `/agent_stats` | Per-agent accuracy | instant |
| `/strategy_stats` | Accuracy by strategy type | instant |
| `/history AAPL` | Past calls on a ticker | instant |
| `/screen` | Latest MA150 screener results (cached) | instant |

Each user also automatically receives:
- Discord-detected signals when they hit the 67% threshold (every 15 min cycle)
- A morning + evening watchlist report on weekdays

---

## 5. Project structure

```
stock_analyzer/
├── api/
│   └── telegram.py            # Vercel serverless function entrypoint
│
├── .github/workflows/
│   ├── broadcast.yml          # Discord scan + broadcast (every 15 min)
│   ├── personal.yml           # Per-user watchlist analysis (2× daily)
│   └── outcome_tracker.yml    # Marks past calls win/loss (daily)
│
├── data/                      # Legacy snapshots — no longer the source of truth
│
├── main.py                    # Local CLI loop (dev only — not on Vercel)
├── run_broadcast.py           # One-shot entrypoint for broadcast.yml
├── run_personal.py            # One-shot entrypoint for personal.yml
├── outcome_tracker.py         # One-shot entrypoint for outcome_tracker.yml
│
├── webhook_handler.py         # handle_command() — shared by Vercel + workflows
├── telegram_bot.py            # send_message + report formatting
│
├── discord_reader.py          # Discord API client (user token)
├── signal_parser.py           # LLM extraction of ticker/entry/SL/TP from Hebrew text
├── image_analyzer.py          # Vision (Llama 4 Scout) on TradingView screenshots
│
├── agents.py                  # 6 specialist agents (3 bull-leaning, 3 bear-leaning), all return p_up
├── aggregator.py              # Confidence-weighted average → bull_probability + entry decision
├── stock_data.py              # yfinance wrapper (RSI, ATR, MAs, 52w high/low)
├── personal_analysis.py       # Single-ticker analysis path (used by /analyze and personal.yml)
│
├── redis_store.py             # Upstash REST API wrapper (no driver, just `requests`)
├── state_store.py             # analyzed_keys, cooldowns, last_runs (Redis-backed)
├── user_store.py              # users + watchlists (Redis-backed)
├── analyses_log.py            # append-only history of every analysis (Redis-backed)
├── stats.py                   # /stats, /agent_stats — accuracy aggregation over analyses_log
│
├── vercel.json                # maxDuration=60 for the analyze command
├── .vercelignore              # exclude CLI entrypoints + venv + data/ from Vercel bundle
├── requirements.txt
└── README.md
```

---

## 6. Design decisions and why

### 6.1 Why Vercel webhook instead of GitHub Actions polling

The bot used to poll Telegram via a workflow running `cron: "* * * * *"`. In
practice GitHub free tier delays scheduled workflows by 1-3 hours. A `/help`
typed by a user took ~2 hours to reply, which felt broken.

Vercel webhooks receive the message instantly. Vercel Hobby tier is free and
has no cold-start issues for low-volume bots.

### 6.2 Why Upstash Redis instead of file-based state

The pre-migration architecture stored state in `data/*.json` and auto-committed
back to the repo after each workflow run. Two problems:

1. **Vercel is stateless** — files written during a function invocation are discarded.
2. **Two writers, no locking** — both `broadcast.yml` and `personal.yml` could race
   on the same `data/state.json`, occasionally producing merge conflicts on the
   bot's auto-commits.

Upstash gives both Vercel and GitHub Actions a shared, atomic, HTTP-accessible
store. Free tier (10k commands/day, 256 MB) is well over our load.

### 6.3 Why a directional `p_up` contract for agents

Earlier agent contract had each agent return a `score` for "the strength of my
assigned side" (bull or bear). Bull agents argued bull, bear agents argued bear.
With Llama 8b's tendency to hedge around 0.5, both sides averaged ~0.55 and the
bull/bear normalization collapsed every verdict to ~50/50, regardless of how
clearly bullish or bearish the actual setup was.

The new contract: every agent returns a unified `p_up` (probability the trade
succeeds). The specialty is just a lens — a "bearish reversal" agent looking at
a clearly bullish chart now honestly reports `p_up = 0.8`. The aggregator takes
a confidence-weighted average across all 6 agents and there's no normalization
step that can collapse the answer.

Few-shot examples in the system prompt explicitly model both directions to
prevent 8b from anchoring on its role label.

### 6.4 Why Groq Llama 8b instead of 70b

70b produces more decisive verdicts (0.8 vs 0.65), but its 100k tokens/day
free-tier cap burns out in ~1-2 hours of running a 15-minute cron plus
per-cycle signal extraction. 8b has a much larger daily cap (~500k+ TPD),
enough to sustain 24/7 operation. The architectural fix (directional `p_up`
contract + anti-bias few-shot prompt) closed most of the quality gap.

### 6.5 Why two-track ingress (webhook + cron)

| Track | Why not the other |
|-------|-------------------|
| Webhook for inbound | Cron is too slow (hours of lag) |
| Cron for outbound | Webhook would need to be triggered by something — there's no "new Discord message" event for our pattern. Discord doesn't push to webhooks from user-token-readable channels in this setup |

---

## 7. Operational notes

### How to redeploy

- **Vercel**: any `git push` to `main` triggers a redeploy automatically
- **GitHub Actions**: workflows pick up the latest `main` on their next scheduled run, or manually trigger via the Actions tab → workflow → "Run workflow"

### How to roll back

`git revert <bad-commit-sha>` → push. Vercel + GitHub Actions both follow.

### Logs

| What | Where |
|------|-------|
| Webhook function logs | Vercel dashboard → project → Logs |
| Broadcast / personal / outcome logs | GitHub Actions → run → job logs |
| Telegram delivery errors | `getWebhookInfo` API call shows the last error |

### Daily token budget watch

If you see lots of `429` errors with `TPD: Limit ... Used ...` in logs:
- 8b on Groq free has ~500k TPD. If you exceed it, agents fall back to neutral verdicts
- Solutions: reduce cron frequency, reduce agent count, or upgrade to Groq Dev tier

---

## 8. Known limitations and improvement points

### Architectural

1. **Discord user-token usage violates Discord ToS**. The `DISCORD_USER_TOKEN`
   header without the `Bot` prefix is "self-bot" behavior. Risk: account ban.
   *Fix*: register a proper Discord bot, invite it to the channel, switch the
   client to use `Bot {token}` auth.

2. **GitHub Actions cron is unreliable on free tier**. The 15-minute
   `broadcast.yml` schedule can drift by an hour during GitHub peak load.
   *Fix options*: (a) accept it, (b) pay for GitHub Pro runners, (c) move
   `broadcast.yml` to a free always-on host (Oracle Cloud Free Tier ARM VM).

3. **No webhook secret token yet** — anyone who learns the Vercel URL could
   POST fake Telegram updates. Mitigated by the fact that `handle_command`
   only acts on `chat_id`s for registered users, but still worth fixing.
   *Fix*: set `TELEGRAM_WEBHOOK_SECRET` env in Vercel, re-register the webhook
   with `secret_token`, and verify the `X-Telegram-Bot-Api-Secret-Token`
   header in `api/telegram.py` (scaffolding is already in place).

### Quality

4. **8b still hedges on borderline setups**. The `p_up` contract closed most of
   the gap with 70b, but on truly ambiguous stocks all 6 agents may cluster
   around 0.5-0.6. A "judge" pattern (5 small agents + 1 big agent that
   synthesizes) would help.

5. **Per-agent accuracy is not yet rolled into agent weighting**. The `stats.py`
   module computes per-agent win/loss rates, but the aggregator weights agents
   only by their self-reported confidence. Weighting by *historical accuracy*
   would let consistently-right agents dominate.

6. **No backtesting harness**. `analyses.jsonl` (now `analyses` list in Redis)
   has the data but there's no tool to replay "what if I had used threshold
   0.65 instead of 0.67" over a date range.

### Operational

7. **`/analyze` runs up to 60s on Vercel** — close to the Hobby tier limit. If a
   single ticker's analysis ever slows down (e.g. Groq queue depth spikes),
   `/analyze` will time out and the user gets no reply. The user sees
   "Analyzing..." but never a result.
   *Fix*: replace the inline call with a `workflow_dispatch` to a small
   `analyze_single.yml` workflow, which can run for minutes without bound.

8. **Analyses log grows unbounded in Redis** (capped to 10k records via LTRIM).
   Once you cross 10k, oldest entries silently drop. Move to a cold-storage
   tier (S3, Cloudflare R2, both free for small data) if you want full history
   forever.

9. **Hebrew signal parser is a single LLM call per cycle**. If Groq is down or
   rate-limited, the whole cycle dies. *Fix*: keep a thin regex fallback to
   catch the obvious `$TSLA buy at 250 sl 245` cases.

### Code hygiene

10. **`webhook_handler.py:main()` is dead code** — only the `handle_command`
    function is called now. Could be deleted, but it's a useful manual
    debug entrypoint, so it stays.

11. **`data/*.json` files still in repo** — historical snapshots, no longer
    written to. Safe to delete in a future cleanup commit.

12. **No automated tests**. There's `_e2e_test.py` as a manual smoke test, but
    nothing automated. Pytest plus the existing fixtures inside `_e2e_test.py`
    would be ~2 hours of work and would catch the kind of regression that
    broke the 50/50 verdict for weeks.

---

## 9. Migration history

| Date | Change |
|------|--------|
| 2026-04 | Initial monolith — `main.py` polling loop, regex signal parser |
| 2026-04 | Replaced regex parser with LLM-based Hebrew extraction (Groq) |
| 2026-04 | Added vision analysis (Llama 4 Scout) for TradingView screenshots |
| 2026-04 | Deployed to GitHub Actions cron (broadcast every 15 min) |
| 2026-04 | Added per-user watchlists + Telegram inbound polling workflow |
| 2026-05 | Discovered 50/50 verdict collapse → architectural fix: `score` → `p_up` contract |
| 2026-05 | Switched all LLM calls 70b → 8b to fit daily TPD budget |
| 2026-05 | Migrated state from `data/*.json` → Upstash Redis |
| 2026-05 | Replaced inbound polling workflow with Vercel serverless webhook |
