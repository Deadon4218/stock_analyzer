"""
Personal watchlist analysis for all users.
Triggered by GitHub Actions 2x daily (market open + close).
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

    for user in users:
        chat_id = user.get("chat_id")
        watchlist = user.get("watchlist", [])
        if not chat_id or not watchlist:
            continue

        print(f"\n👤 User {chat_id} — {len(watchlist)} ticker(s)")
        send_message(chat_id, f"⏳ Running <b>{label}</b> analysis on your watchlist...")

        per_ticker = []
        for i, ticker in enumerate(watchlist):
            print(f"  [{i+1}/{len(watchlist)}] {ticker}")
            try:
                result = analyze_ticker(ticker)
            except Exception as e:
                print(f"    ⚠️  {ticker} failed: {e}")
                result = None
            per_ticker.append((ticker, result))

            if i < len(watchlist) - 1:
                time.sleep(delay)

        report = format_personal_summary(per_ticker)
        send_message(chat_id, report)
        print(f"  ✅ Sent to {chat_id}")


if __name__ == "__main__":
    main()
