"""
Single-cycle Discord scan + Telegram broadcast.
Triggered by GitHub Actions every 15 min.
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from discord_reader import fetch_messages, search_messages_for_ticker
from signal_parser import extract_signals_from_messages
from stock_data import fetch_stock_data
from image_analyzer import analyze_all_images, format_chart_analysis
from agents import run_all_agents
from aggregator import aggregate, calculate_price_levels
from state_store import get_analyzed_keys, add_analyzed_keys
from user_store import get_all_users
from telegram_bot import broadcast, format_broadcast_report


def validate_env():
    required = ["DISCORD_USER_TOKEN", "DISCORD_CHANNEL_ID", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"❌ Missing env vars: {', '.join(missing)}")


def main():
    load_dotenv()
    validate_env()

    print(f"🔄 Broadcast cycle — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    channel_id = os.environ["DISCORD_CHANNEL_ID"]
    msg_limit = int(os.environ.get("MESSAGES_LIMIT", 50))

    # 1. Fetch Discord
    try:
        messages = fetch_messages(channel_id, limit=msg_limit)
        print(f"📡 {len(messages)} messages fetched")
    except Exception as e:
        print(f"❌ Discord fetch failed: {e}")
        return

    if not messages:
        print("No messages")
        return

    # 2. Extract signals
    signals = extract_signals_from_messages(messages)
    if not signals:
        print("No signals extracted")
        return

    # 3. Dedupe
    analyzed_keys = get_analyzed_keys()
    new_signals = []
    new_keys = set()
    for s in signals:
        key = f"{s.ticker}:{s.timestamp}"
        if key not in analyzed_keys:
            new_signals.append(s)
            new_keys.add(key)

    if not new_signals:
        print("All signals already analyzed in prior runs")
        return

    print(f"📋 {len(new_signals)} new signal(s)")

    # 4. Analyze each
    results = []
    for signal in new_signals:
        print(f"\n🔍 {signal.ticker}")
        chart_context = "No chart images available."
        chart_analyses = []
        if signal.image_urls:
            chart_analyses = analyze_all_images(signal.image_urls)
            chart_context = format_chart_analysis(chart_analyses)

        data = fetch_stock_data(signal.ticker)
        if data.error:
            print(f"   ⚠️  {data.error}")

        price_levels = calculate_price_levels(signal, data, chart_analyses)
        related = search_messages_for_ticker(messages, signal.ticker)
        msg_ctx = "\n".join(f"[{m['author']}]: {m['content']}" for m in related[-15:])

        bull, bear = run_all_agents(signal, data, msg_ctx, chart_context)
        result = aggregate(signal.ticker, bull, bear, price_levels)
        results.append(result)

    # 5. Broadcast to all users
    users = get_all_users()
    chat_ids = [u["chat_id"] for u in users if u.get("chat_id")]

    if not chat_ids:
        print("⚠️  No users registered — skipping broadcast")
    else:
        report = format_broadcast_report(results)
        print(f"\n📤 Broadcasting to {len(chat_ids)} user(s)")
        broadcast(report, chat_ids)

    # 6. Persist analyzed keys
    add_analyzed_keys(new_keys)
    print(f"💾 State saved ({len(new_keys)} new keys)")


if __name__ == "__main__":
    main()
