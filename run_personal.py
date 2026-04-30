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
from telegram_bot import send_message, format_personal_summary


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


if __name__ == "__main__":
    main()
