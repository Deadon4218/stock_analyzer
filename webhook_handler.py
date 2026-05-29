"""
Telegram bot command poller.
Run by GitHub Actions every 5 min — checks for new commands and processes them.
"""
import os
import requests
from dotenv import load_dotenv

# Load environment variables first so they are available during module imports
load_dotenv()

import time

from user_store import add_ticker, remove_ticker, get_watchlist
from state_store import get_last_update_id, set_last_update_id, get_cooldown_ts, set_cooldown_ts
from telegram_bot import send_message, format_personal_report

ANALYZE_COOLDOWN_SEC = 60

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


WELCOME_TEXT = (
    "👋 <b>Stock Signal Analyzer Bot</b>\n\n"
    "<b>Watchlist:</b>\n"
    "/add AAPL — Add stock to your watchlist\n"
    "/remove AAPL — Remove stock\n"
    "/list — Show your watchlist\n"
    "/analyze AAPL — Instant analysis (~30s)\n\n"
    "<b>Trading Notebook (Portfolio):</b>\n"
    "/buy AAPL 150 10 145 — Buy 10 AAPL @ $150, stop-loss at $145\n"
    "/update ID NEW_SL — Manually update stop-loss price of an open position\n"
    "/sell ID [PRICE] — Sell position by its ID (fetches market price if blank)\n"
    "/portfolio (or /port) — Show your open positions & P/L\n"
    "/port_history — Show your closed trades history\n"
    "/clear_portfolio — Clear all your portfolio data\n\n"
    "<b>Stats &amp; History:</b>\n"
    "/stats — Overall win rate of past calls\n"
    "/agent_stats — Per-agent accuracy\n"
    "/strategy_stats — Accuracy by strategy type\n"
    "/history AAPL — Past calls on a ticker\n\n"
    "<b>Screener:</b>\n"
    "/screen — Stocks in 0-3% band above MA150 (S&amp;P 500 + NASDAQ-100, twice daily)\n\n"
    "You'll automatically receive:\n"
    "• Discord signals as they come in (shared, every 15 min)\n"
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

        now = time.time()
        last = get_cooldown_ts(chat_id, "analyze")
        remaining = ANALYZE_COOLDOWN_SEC - (now - last)
        if remaining > 0:
            send_message(chat_id, f"⏳ Please wait {int(remaining)}s before another /analyze")
            return
        set_cooldown_ts(chat_id, "analyze", now)

        send_message(chat_id, f"🔄 Analyzing <b>{ticker}</b>... (~30s)")
        from personal_analysis import analyze_ticker
        result = analyze_ticker(ticker, source="manual")
        if result:
            send_message(chat_id, format_personal_report(ticker, result))
        else:
            send_message(chat_id, f"❌ Could not analyze {ticker} (no market data)")

    elif cmd == "/stats":
        from stats import overall_stats, format_overall
        send_message(chat_id, format_overall(overall_stats()))

    elif cmd == "/agent_stats":
        from stats import agent_accuracy, format_agent_table
        send_message(chat_id, format_agent_table(agent_accuracy()))

    elif cmd == "/strategy_stats":
        from stats import strategy_accuracy, format_strategy_table
        send_message(chat_id, format_strategy_table(strategy_accuracy()))

    elif cmd == "/history":
        if not args:
            send_message(chat_id, "Usage: <code>/history AAPL</code>")
            return
        ticker = args[0].upper().strip()
        from stats import ticker_history, format_ticker_history
        send_message(chat_id, format_ticker_history(ticker, ticker_history(ticker)))

    elif cmd == "/screen":
        import redis_store as r
        from screener import format_report
        snapshot = r.get_json("screener:latest")
        if not snapshot:
            send_message(
                chat_id,
                "📊 No screener results yet. The first scheduled scan runs at "
                "12:30 UTC weekdays. Check back after that.",
            )
            return
        report = format_report(snapshot["matches"], snapshot["ts"])
        send_message(chat_id, report)

    elif cmd == "/buy":
        if len(args) < 3:
            send_message(
                chat_id,
                "Usage: <code>/buy TICKER PRICE QUANTITY [STOP_LOSS]</code>\n"
                "Example: <code>/buy AAPL 150 10 145</code>"
            )
            return
            
        ticker = args[0].upper().strip()
        if not ticker.isalpha() or len(ticker) > 5:
            send_message(chat_id, f"⚠️ Invalid ticker: {ticker}")
            return
            
        try:
            price = float(args[1])
            qty = float(args[2])
            
            if price <= 0 or qty <= 0:
                send_message(chat_id, "⚠️ Price and quantity must be positive numbers.")
                return
                
            if len(args) >= 4:
                # Stop-loss is specified
                sl = float(args[3])
                if sl <= 0:
                    send_message(chat_id, "⚠️ Stop-loss must be a positive number.")
                    return
                if sl >= price:
                    send_message(chat_id, f"⚠️ Stop-loss (${sl:.2f}) must be lower than entry price (${price:.2f}).")
                    return
                    
                from portfolio import buy_stock
                pos = buy_stock(chat_id, ticker, price, qty, sl)
                send_message(
                    chat_id,
                    f"✅ <b>Position Entered!</b>\n"
                    f"📈 <b>{pos['ticker']}</b> (ID: {pos['id']})\n"
                    f"  • Shares: {pos['quantity']} @ ${pos['entry_price']:.2f} (Cost: ${pos['entry_price']*pos['quantity']:.2f})\n"
                    f"  • Stop-Loss: ${pos['current_stop_loss']:.2f}"
                )
            else:
                # Stop-loss is NOT specified - calculate recommendations!
                send_message(chat_id, f"🔍 Calculating stop-loss recommendations for <b>{ticker}</b>...")
                from portfolio import recommend_stop_loss
                recs = recommend_stop_loss(ticker, price)
                
                if not recs:
                    send_message(
                        chat_id,
                        f"⚠️ Stop-loss is required to enter a position.\n"
                        f"Could not calculate automated recommendations for {ticker}. Please specify it manually:\n"
                        f"<code>/buy {ticker} {price} {qty} STOP_LOSS</code>"
                    )
                    return
                    
                atr_val = f"${recs['atr_14']:.2f}" if recs.get("atr_14") else "N/A"
                
                lines = [
                    f"⚠️ <b>Stop-Loss (Exit Price) is required!</b>\n",
                    f"Based on market data for <b>{ticker}</b>, here are recommended levels:",
                    ""
                ]
                
                if recs.get("sl_atr_1_5x"):
                    lines.append(f"• <b>Option A (1.5x ATR):</b> ${recs['sl_atr_1_5x']:.2f} (14d ATR is {atr_val})")
                    lines.append(f"  To use: <code>/buy {ticker} {price} {qty} {recs['sl_atr_1_5x']:.2f}</code>\n")
                    
                if recs.get("prev_day_low"):
                    lines.append(f"• <b>Option B (Yesterday's Low):</b> ${recs['prev_day_low']:.2f}")
                    lines.append(f"  To use: <code>/buy {ticker} {price} {qty} {recs['prev_day_low']:.2f}</code>")
                    
                if not recs.get("sl_atr_1_5x") and not recs.get("prev_day_low"):
                    lines.append(f"Please specify a stop-loss price manually:")
                    lines.append(f"<code>/buy {ticker} {price} {qty} STOP_LOSS</code>")
                    
                send_message(chat_id, "\n".join(lines))
                
        except ValueError:
            send_message(chat_id, "⚠️ Invalid numbers. Usage: <code>/buy TICKER PRICE QUANTITY [STOP_LOSS]</code>")

    elif cmd == "/update":
        if len(args) < 2:
            send_message(
                chat_id,
                "Usage: <code>/update ID NEW_SL</code>\n"
                "Example: <code>/update 1 148</code>"
            )
            return
        try:
            pos_id = int(args[0])
            new_sl = float(args[1])
            
            if new_sl <= 0:
                send_message(chat_id, "⚠️ Stop-loss must be positive.")
                return
                
            from portfolio import update_stop_loss
            pos = update_stop_loss(chat_id, pos_id, new_sl)
            if not pos:
                send_message(chat_id, f"⚠️ Open position with ID {pos_id} not found.")
                return
                
            send_message(
                chat_id,
                f"✅ <b>Stop-Loss Updated!</b>\n"
                f"📈 <b>{pos['ticker']}</b> (ID: {pos['id']})\n"
                f"  • Entry Price: ${pos['entry_price']:.2f}\n"
                f"  • New Stop-Loss: ${pos['current_stop_loss']:.2f}"
            )
        except ValueError:
            send_message(chat_id, "⚠️ Invalid arguments. Usage: <code>/update ID NEW_SL</code>")

    elif cmd == "/sell":
        if not args:
            send_message(chat_id, "Usage: <code>/sell ID [EXIT_PRICE]</code>\nExample: <code>/sell 1</code>")
            return
        try:
            pos_id = int(args[0])
            exit_price = float(args[1]) if len(args) > 1 else None
            
            if exit_price is not None and exit_price <= 0:
                send_message(chat_id, "⚠️ Exit price must be positive.")
                return
                
            from portfolio import sell_stock
            pos = sell_stock(chat_id, pos_id, exit_price)
            if not pos:
                send_message(chat_id, f"⚠️ Open position with ID {pos_id} not found.")
                return
                
            cost = pos["entry_price"] * pos["quantity"]
            revenue = pos["exit_price"] * pos["quantity"]
            pl_usd = revenue - cost
            pl_pct = (pos["exit_price"] - pos["entry_price"]) / pos["entry_price"] * 100
            
            pl_color = "🟢" if pl_usd >= 0 else "🔴"
            pl_sign = "+" if pl_usd >= 0 else ""
            
            send_message(
                chat_id,
                f"🗑 <b>Position Closed!</b>\n"
                f"📈 <b>{pos['ticker']}</b> (ID: {pos['id']})\n"
                f"  • Shares: {pos['quantity']} @ ${pos['entry_price']:.2f}\n"
                f"  • Closed at: ${pos['exit_price']:.2f}\n"
                f"  • {pl_color} P/L: {pl_sign}${pl_usd:.2f} ({pl_sign}{pl_pct:.2f}%)"
            )
        except ValueError:
            send_message(chat_id, "⚠️ Invalid arguments. Usage: <code>/sell ID [EXIT_PRICE]</code>")

    elif cmd in ("/portfolio", "/port"):
        from portfolio import get_portfolio, fetch_current_price
        port = get_portfolio(chat_id)
        if not port["open"]:
            send_message(
                chat_id,
                "💼 Your portfolio has no open positions.\n"
                "Use <code>/buy AAPL 150 10 145</code> to add a position."
            )
            return
            
        send_message(chat_id, "🔄 Fetching latest portfolio prices... (this may take a few seconds)")
        
        lines = ["📊 <b>Your Trading Portfolio</b>", ""]
        total_cost = 0.0
        total_value = 0.0
        
        for pos in port["open"]:
            ticker = pos["ticker"]
            mkt_price = fetch_current_price(ticker)
            current_price = mkt_price if mkt_price is not None else pos["entry_price"]
            
            cost = pos["entry_price"] * pos["quantity"]
            value = current_price * pos["quantity"]
            
            total_cost += cost
            total_value += value
            
            pl_usd = value - cost
            pl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            
            pl_color = "🟢" if pl_usd >= 0 else "🔴"
            pl_sign = "+" if pl_usd >= 0 else ""
            
            lines.append(
                f"🟢 <b>{ticker}</b> (ID: {pos['id']})\n"
                f"  • Shares: {pos['quantity']} @ ${pos['entry_price']:.2f} (Cost: ${cost:.2f})\n"
                f"  • Current: ${current_price:.2f} (Value: ${value:.2f})\n"
                f"  • Stop-Loss: ${pos['current_stop_loss']:.2f}\n"
                f"  • P/L: {pl_color} <b>{pl_sign}${pl_usd:.2f} ({pl_sign}{pl_pct:.2f}%)</b>\n"
            )
            
        total_pl_usd = total_value - total_cost
        total_pl_pct = (total_value - total_cost) / total_cost * 100 if total_cost > 0 else 0
        total_pl_color = "🟢" if total_pl_usd >= 0 else "🔴"
        total_pl_sign = "+" if total_pl_usd >= 0 else ""
        
        lines.append("💰 <b>Summary:</b>")
        lines.append(f"  • Total Cost: ${total_cost:.2f}")
        lines.append(f"  • Current Value: ${total_value:.2f}")
        lines.append(f"  • Total Open P/L: {total_pl_color} <b>{total_pl_sign}${total_pl_usd:.2f} ({total_pl_sign}{total_pl_pct:.2f}%)</b>")
        
        send_message(chat_id, "\n".join(lines))

    elif cmd == "/port_history":
        from portfolio import get_portfolio
        port = get_portfolio(chat_id)
        if not port["closed"]:
            send_message(chat_id, "📜 No closed trades in your history yet.")
            return
            
        lines = ["📜 <b>Portfolio History (Closed Trades)</b>", ""]
        # Show last 15 closed trades (newest first)
        for pos in reversed(port["closed"][-15:]):
            cost = pos["entry_price"] * pos["quantity"]
            revenue = pos["exit_price"] * pos["quantity"]
            pl_usd = revenue - cost
            pl_pct = (pos["exit_price"] - pos["entry_price"]) / pos["entry_price"] * 100
            
            pl_color = "🟢" if pl_usd >= 0 else "🔴"
            pl_sign = "+" if pl_usd >= 0 else ""
            reason = "SL HIT" if pos.get("exit_reason") == "sl_hit" else "MANUAL"
            
            lines.append(
                f"{pl_color} <b>{pos['ticker']}</b> (ID: {pos['id']}) - <b>{reason}</b>\n"
                f"  • Entered: {pos['entry_date'][:10]} @ ${pos['entry_price']:.2f}\n"
                f"  • Exited: {pos.get('exit_date', 'N/A')[:10]} @ ${pos['exit_price']:.2f}\n"
                f"  • Shares: {pos['quantity']}\n"
                f"  • P/L: {pl_color} <b>{pl_sign}${pl_usd:.2f} ({pl_sign}{pl_pct:.2f}%)</b>\n"
            )
            
        send_message(chat_id, "\n".join(lines))

    elif cmd == "/clear_portfolio":
        from portfolio import clear_portfolio
        clear_portfolio(chat_id)
        send_message(chat_id, "🗑 <b>Portfolio Wiped!</b> All open and closed history has been deleted.")

    else:
        send_message(chat_id, "Unknown command. Send /help for command list.")


def main():
    # Update active portfolios (trails SL & alerts users of SL hits)
    try:
        from portfolio import update_all_portfolios
        update_all_portfolios()
    except Exception as e:
        print(f"⚠️ Error updating portfolios: {e}")

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
