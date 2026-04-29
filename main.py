"""
Stock Signal Analyzer v2
========================
Reads signals from Discord (Hebrew text + TradingView charts),
analyzes with 10 AI agents (5 bull, 5 bear),
and gives buy/skip decision based on 67% threshold.

Setup:
  1. Copy .env.example → .env and fill in your credentials
  2. pip install -r requirements.txt
  3. python main.py
"""

import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

from discord_reader import fetch_messages, search_messages_for_ticker, random_sleep
from signal_parser import extract_signals_from_messages
from stock_data import fetch_stock_data
from image_analyzer import analyze_all_images, format_chart_analysis
from agents import run_all_agents
from aggregator import aggregate, calculate_price_levels

# Deduplication — tracks analyzed signal keys (ticker:timestamp)
_analyzed_keys: set[str] = set()


def validate_env():
    required = ["DISCORD_USER_TOKEN", "DISCORD_CHANNEL_ID", "GROQ_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("   Copy .env.example → .env and fill in the values")
        sys.exit(1)


def run_cycle():
    """
    Single analysis cycle:
    1. Fetch messages from Discord
    2. Extract signals with LLM (handles Hebrew)
    3. For each signal: analyze charts + fetch market data + run 10 agents
    4. Print verdict
    """
    channel_id = os.environ["DISCORD_CHANNEL_ID"]
    msg_limit = int(os.environ.get("MESSAGES_LIMIT", 75))

    print(f"\n{'=' * 60}")
    print(f"🔄 New cycle — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 60}")

    # Step 1: Fetch from Discord
    print(f"\n📡 Fetching {msg_limit} latest messages...")
    try:
        messages = fetch_messages(channel_id, limit=msg_limit)
        print(f"   ✅ {len(messages)} messages fetched")
    except ValueError as e:
        print(f"   {e}")
        return
    except Exception as e:
        print(f"   ❌ Fetch error: {e}")
        return

    if not messages:
        print("   ⚠️  No messages found")
        return

    # Count images
    total_images = sum(len(m.get("image_urls", [])) for m in messages)
    print(f"   📸 {total_images} chart images detected")

    # Step 2: Extract signals with LLM
    print(f"\n🧠 Extracting signals with LLM...")
    signals = extract_signals_from_messages(messages)
    if not signals:
        print("   ⚠️  No signals found in recent messages")
        return

    # Deduplication
    new_signals = []
    for s in signals:
        sig_key = f"{s.ticker}:{s.timestamp}"
        if sig_key not in _analyzed_keys:
            new_signals.append(s)
            _analyzed_keys.add(sig_key)
        else:
            print(f"   ⏭  {s.ticker} already analyzed — skipping")

    if not new_signals:
        print("   ⚠️  No new signals since last cycle")
        return

    signals = new_signals
    print(f"\n📋 Found {len(signals)} new signal(s):")
    for s in signals:
        print(f"   {s}")

    # Step 3: Analyze each signal
    results = []
    for signal in signals:
        print(f"\n{'─' * 60}")
        print(f"🔍 Analyzing {signal.ticker}...")

        # 3a: Chart image analysis (if available)
        chart_context = "No chart images available."
        chart_analyses = []
        if signal.image_urls:
            print(f"   📸 Analyzing {len(signal.image_urls)} chart image(s)...")
            chart_analyses = analyze_all_images(signal.image_urls)
            chart_context = format_chart_analysis(chart_analyses)
            if chart_analyses:
                print(f"   ✅ Chart analysis complete")

        # 3b: Market data from yfinance
        print(f"   📈 Fetching market data...")
        data = fetch_stock_data(signal.ticker)
        if data.error:
            print(f"   ⚠️  Market data error: {data.error}")
        else:
            print(f"   {data.summary()}")

        # 3c: Calculate price levels
        price_levels = calculate_price_levels(signal, data, chart_analyses)
        print(f"   💰 {price_levels}")

        # 3d: Related Discord messages
        related_messages = search_messages_for_ticker(messages, signal.ticker)
        messages_context = "\n".join([
            f"[{m['author']}]: {m['content']}"
            for m in related_messages[-20:]
        ])

        # 3e: Run 10 agents in parallel
        bull_verdicts, bear_verdicts = run_all_agents(
            signal, data, messages_context, chart_context
        )

        # 3f: Aggregate
        result = aggregate(signal.ticker, bull_verdicts, bear_verdicts, price_levels)
        results.append(result)

        # Print report
        print(result.formatted_report())

    # Summary
    print(f"\n{'=' * 60}")
    print("📌 Summary:")
    for r in results:
        emoji = "✅" if r.should_enter else "❌"
        line = (
            f"  {emoji} {r.ticker} | "
            f"🟢 {r.bull_probability:.1%} | "
            f"🔴 {r.bear_probability:.1%} | "
            f"Ratio: {r.probability_ratio():.2f}x"
        )
        if r.price_levels and r.price_levels.entry:
            pl = r.price_levels
            line += f" | Entry: ${pl.entry:.2f}"
            if pl.take_profit:
                line += f" → TP: ${pl.take_profit:.2f}"
            if pl.stop_loss:
                line += f" / SL: ${pl.stop_loss:.2f}"
            if pl.rr_ratio:
                line += f" (R:R {pl.rr_ratio:.1f})"
        print(line)
    print(f"{'=' * 60}")

    return results


def main():
    load_dotenv()
    validate_env()

    print("🚀 Stock Signal Analyzer v2 started")
    print(f"   Polling: every {os.environ.get('POLL_MIN_MINUTES', 5)}–{os.environ.get('POLL_MAX_MINUTES', 15)} min (random)")
    print(f"   Features: Hebrew text parsing, TradingView chart analysis, 10 AI agents")
    print("   Press Ctrl+C to stop\n")

    while True:
        try:
            run_cycle()
            random_sleep()
        except KeyboardInterrupt:
            print("\n\n👋 System stopped")
            break
        except Exception as e:
            print(f"\n⚠️  Unexpected error: {e}")
            print("   Retrying in 5 minutes...")
            import time
            time.sleep(300)


if __name__ == "__main__":
    main()
