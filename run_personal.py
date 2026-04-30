"""
Personal watchlist analysis for all users.
Triggered by GitHub Actions 2x daily (market open + close).

Optimization: shared tickers across users are analyzed only once per run.
"""
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

from user_store import get_all_users
from personal_analysis import analyze_ticker
from state_store import get_last_run_ts, set_last_run_ts
from telegram_bot import send_message, format_personal_summary

# Skip the run if a same-labeled run completed within this many seconds.
# Lets us schedule a backup cron without double-sending reports.
DEDUP_WINDOW_SEC = int(os.environ.get("PERSONAL_DEDUP_WINDOW_SEC", 1800))


def validate_env():
    required = ["GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"❌ Missing env vars: {', '.join(missing)}")


def main():
    load_dotenv()
    validate_env()

    label = os.environ.get("PERSONAL_RUN_LABEL", "Daily")
    delay = float(os.environ.get("PERSONAL_DELAY_SECONDS", 8))

    print(f"📊 Personal cycle ({label}) — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Dedup: if a primary run already completed recently, skip the backup.
    now = time.time()
    last = get_last_run_ts(label)
    if last and (now - last) < DEDUP_WINDOW_SEC:
        elapsed = int(now - last)
        print(f"⏭  Skipping — a '{label}' run completed {elapsed}s ago ({DEDUP_WINDOW_SEC}s window)")
        return

    users = get_all_users()
    if not users:
        print("No users registered")
        return

    # Collect all unique tickers across users so we analyze each only once.
    all_tickers = sorted({
        t.upper().strip()
        for u in users
        for t in u.get("watchlist", [])
        if t and t.strip()
    })

    if not all_tickers:
        print("No tickers in any watchlist")
        return

    print(f"🧮 Analyzing {len(all_tickers)} unique ticker(s) shared across {len(users)} user(s)")

    cache: dict[str, object] = {}
    for i, ticker in enumerate(all_tickers):
        print(f"  [{i+1}/{len(all_tickers)}] {ticker}")
        try:
            cache[ticker] = analyze_ticker(ticker)
        except Exception as e:
            print(f"    ⚠️  {ticker} failed: {e}")
            cache[ticker] = None

        if i < len(all_tickers) - 1:
            time.sleep(delay)

    # Send each user a summary built from the cache.
    for user in users:
        chat_id = user.get("chat_id")
        watchlist = user.get("watchlist", [])
        if not chat_id or not watchlist:
            continue

        per_ticker = [(t.upper().strip(), cache.get(t.upper().strip())) for t in watchlist]
        report = format_personal_summary(per_ticker)
        try:
            send_message(chat_id, f"📈 <b>{label} watchlist analysis</b>")
            send_message(chat_id, report)
            print(f"  ✅ Sent to {chat_id}")
        except Exception as e:
            print(f"  ⚠️  Failed to send to {chat_id}: {e}")

    set_last_run_ts(label, now)
    print(f"💾 Marked '{label}' run complete at {datetime.utcfromtimestamp(now).strftime('%H:%M UTC')}")


if __name__ == "__main__":
    main()
