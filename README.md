# Stock Signal Analyzer

Automated Discord signal analyzer + personal watchlist tracker. 10 AI agents (5 bull, 5 bear) decide whether each signal passes a 67% confidence threshold. Sends results via Telegram.

## How it works

Three GitHub Actions cron jobs:
- **Broadcast** (every 15 min): scans Discord channel, analyzes new signals, sends to all registered Telegram users
- **Personal** (2x daily, market open + close): analyzes each user's private watchlist
- **Telegram poll** (every 5 min): processes bot commands (`/add`, `/remove`, `/list`, `/analyze`)

State persisted to `data/users.json` and `data/state.json`, committed back to repo automatically.

## Cost

$0/month — uses GitHub Actions free tier, Groq free tier, Telegram Bot API, yfinance.

## Deployment

### 1. Create the GitHub repo

```bash
gh repo create stock_analyzer --public --source=. --push
# or via web UI, then: git remote add origin … && git push -u origin main
```

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow prompts
3. Copy the bot token

### 3. Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Source |
|--------|--------|
| `DISCORD_USER_TOKEN` | Discord browser → F12 → Network → any request → Authorization header |
| `DISCORD_CHANNEL_ID` | Right-click channel → Copy Channel ID (Developer Mode on) |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |

### 4. Enable workflows

Go to the **Actions** tab, enable workflows. They start running on the cron schedule.

### 5. Register users

Each friend opens Telegram → searches your bot → sends `/start`. Then `/add AAPL` etc.

## Local development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in tokens
python run_broadcast.py     # one cycle of Discord broadcast
python run_personal.py      # one cycle of personal analysis
python webhook_handler.py   # process pending Telegram commands
```

## Telegram commands

| Command | Action |
|---------|--------|
| `/start` | Show help + register |
| `/add AAPL` | Add ticker to your watchlist |
| `/remove AAPL` | Remove ticker |
| `/list` | Show your watchlist |
| `/analyze AAPL` | One-off analysis (~30s) |

## Files

| File | Purpose |
|------|---------|
| `run_broadcast.py` | Single Discord cycle → broadcast |
| `run_personal.py` | Per-user watchlist analysis |
| `webhook_handler.py` | Telegram command poller |
| `agents.py` | 10 bull/bear agents |
| `aggregator.py` | Combines verdicts → entry decision |
| `signal_parser.py` | LLM signal extraction (Hebrew → JSON) |
| `discord_reader.py` | Discord API client |
| `image_analyzer.py` | TradingView chart vision analysis |
| `stock_data.py` | yfinance market data |
| `personal_analysis.py` | Watchlist analysis (no Discord) |
| `telegram_bot.py` | Telegram message formatting + send |
| `user_store.py` | Per-user watchlist storage |
| `state_store.py` | Cross-run state (analyzed_keys, last_update_id) |
| `groq_client.py` | Groq API singleton |
| `data/users.json` | Per-user watchlists |
| `data/state.json` | Persistent state |

## Notes

- The 10 agents are shared across all users — when you change agent prompts in `agents.py`, all users get the new version
- Each user has their own private watchlist
- Groq free tier is rate-limited (~12k TPM) — `PERSONAL_DELAY_SECONDS` spaces out calls
