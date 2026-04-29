"""
Telegram bot command poller.
Run by GitHub Actions every 5 min — checks for new commands and processes them.
"""
import os
import requests
from dotenv import load_dotenv

from user_store import add_ticker, remove_ticker, get_watchlist
from state_store import get_last_update_id, set_last_update_id
from telegram_bot import send_message, format_personal_report

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


WELCOME_TEXT = (
    "👋 <b>Stock Signal Analyzer Bot</b>\n\n"
    "<b>Commands:</b>\n"
    "/add AAPL — Add stock to your watchlist\n"
    "/remove AAPL — Remove stock\n"
    "/list — Show your watchlist\n"
    "/analyze AAPL — Instant analysis (~30s)\n\n"
    "You'll automatically receive:\n"
    "• Discord signals as they come in (shared)\n"
    "• Personal watchlist report 2x/day"
)


def get_updates(offset: int) -> list[dict]:
    resp = requests.get(
        f"{API_URL}/getUpdates",
        params={"offset": offset, "timeout": 0, "limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def handle_command(chat_id: int, text: str):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd == "/start" or cmd == "/help":
        send_message(chat_id, WELCOME_TEXT)

    elif cmd == "/add":
        if not args:
            send_message(chat_id, "Usage: <code>/add AAPL</code>")
            return
        ticker = args[0].upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            send_message(chat_id, f"⚠️ Invalid ticker: {ticker}")
            return
        if add_ticker(chat_id, ticker):
            send_message(chat_id, f"✅ <b>{ticker}</b> added to your watchlist")
        else:
            send_message(chat_id, f"⚠️ <b>{ticker}</b> already in your watchlist")

    elif cmd == "/remove":
        if not args:
            send_message(chat_id, "Usage: <code>/remove AAPL</code>")
            return
        ticker = args[0].upper().strip()
        if remove_ticker(chat_id, ticker):
            send_message(chat_id, f"🗑 <b>{ticker}</b> removed")
        else:
            send_message(chat_id, f"⚠️ <b>{ticker}</b> not in your watchlist")

    elif cmd == "/list":
        watchlist = get_watchlist(chat_id)
        if watchlist:
            tickers_str = "\n".join(f"  • <b>{t}</b>" for t in watchlist)
            send_message(chat_id, f"📋 <b>Your watchlist ({len(watchlist)}):</b>\n{tickers_str}")
        else:
            send_message(chat_id, "📋 Your watchlist is empty.\nUse <code>/add AAPL</code>")

    elif cmd == "/analyze":
        if not args:
            send_message(chat_id, "Usage: <code>/analyze AAPL</code>")
            return
        ticker = args[0].upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            send_message(chat_id, f"⚠️ Invalid ticker: {ticker}")
            return
        send_message(chat_id, f"🔄 Analyzing <b>{ticker}</b>... (~30s)")
        from personal_analysis import analyze_ticker
        result = analyze_ticker(ticker)
        if result:
            send_message(chat_id, format_personal_report(ticker, result))
        else:
            send_message(chat_id, f"❌ Could not analyze {ticker} (no market data)")

    else:
        send_message(chat_id, "Unknown command. Send /help for command list.")


def main():
    last_id = get_last_update_id()
    offset = last_id + 1 if last_id else 0
    updates = get_updates(offset)

    if not updates:
        print("No new updates")
        return

    print(f"Processing {len(updates)} update(s)")

    for update in updates:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")

        if chat_id and text.startswith("/"):
            try:
                handle_command(chat_id, text)
            except Exception as e:
                print(f"⚠️  Error handling command '{text}': {e}")
                send_message(chat_id, "❌ Internal error processing command")

    set_last_update_id(updates[-1]["update_id"])
    print(f"Updated last_update_id to {updates[-1]['update_id']}")


if __name__ == "__main__":
    main()
