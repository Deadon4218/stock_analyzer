"""
Single-cycle screener.

Triggered by GitHub Actions twice daily (pre-open + post-close).
1. Scans S&P 500 + NASDAQ-100 for MA150 band matches.
2. Saves the result to Redis (consumed by /screen command on Vercel).
3. Broadcasts a summary to all registered Telegram users.
"""
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

import screener
import redis_store as r
from user_store import get_all_users
from telegram_bot import broadcast


def main():
    label = os.environ.get("SCREENER_RUN_LABEL", "manual")
    print(f"🔎 MA150 screener — label={label}")

    tickers = screener.load_universe()
    print(f"   Universe: {len(tickers)} tickers")

    def progress(done, total):
        pct = done / total * 100
        print(f"   ... {done}/{total} ({pct:.0f}%)")

    matches = screener.scan(tickers, progress_callback=progress)

    rising, weak = screener.categorize(matches)
    print(f"\n📈 {len(rising)} rising-MA matches, 📊 {len(weak)} weak crosses")

    scan_ts = screener.utc_now_iso()
    payload = {
        "ts": scan_ts,
        "label": label,
        "count": len(matches),
        "rising_count": len(rising),
        "weak_count": len(weak),
        "matches": matches,
    }

    # Persist for /screen command
    r.set_json("screener:latest", payload)
    print("💾 Saved snapshot to Redis (screener:latest)")

    if not matches:
        print("⚠️  No matches — skipping broadcast")
        return

    # Broadcast to all registered users
    report = screener.format_report(matches, scan_ts)
    users = get_all_users()
    chat_ids = [u["chat_id"] for u in users if u.get("chat_id")]

    if not chat_ids:
        print("⚠️  No users registered — skipping broadcast")
        return

    print(f"📤 Broadcasting to {len(chat_ids)} user(s)")
    broadcast(report, chat_ids)
    print("✅ Done")


if __name__ == "__main__":
    main()
