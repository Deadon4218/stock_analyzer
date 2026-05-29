"""
Portfolio Manager & Manual Stop-Loss Tracker.
Manages user portfolios in Upstash Redis and tracks stop-losses.
"""
from datetime import datetime, timezone
import yfinance as yf
import redis_store as r
from telegram_bot import send_message
from user_store import get_all_users

_K_PORTFOLIO = "portfolio:{chat_id}"


def get_portfolio(chat_id: int) -> dict:
    """Load portfolio data from Redis."""
    data = r.get_json(_K_PORTFOLIO.format(chat_id=chat_id))
    if data is None:
        return {"open": [], "closed": []}
    if "open" not in data:
        data["open"] = []
    if "closed" not in data:
        data["closed"] = []
    return data


def save_portfolio(chat_id: int, portfolio: dict):
    """Save portfolio data to Redis."""
    r.set_json(_K_PORTFOLIO.format(chat_id=chat_id), portfolio)


def fetch_current_price(ticker: str) -> float | None:
    """Fetch the latest close price for a ticker using yfinance."""
    try:
        yf_ticker = yf.Ticker(ticker)
        # Fetch 5 days history to make sure we get a valid candle even on weekends/holidays
        hist = yf_ticker.history(period="5d")
        if not hist.empty:
            closes = hist["Close"].dropna()
            if not closes.empty:
                return float(closes.iloc[-1])
    except Exception as e:
        print(f"⚠️ Error fetching price for {ticker}: {e}")
    return None


def buy_stock(
    chat_id: int,
    ticker: str,
    entry_price: float,
    quantity: float,
    stop_loss: float,
) -> dict:
    """Add a new position to the open portfolio with a manual stop-loss."""
    portfolio = get_portfolio(chat_id)
    
    # Generate a unique sequential ID
    all_positions = portfolio["open"] + portfolio["closed"]
    next_id = max([p["id"] for p in all_positions]) + 1 if all_positions else 1
    
    new_pos = {
        "id": next_id,
        "ticker": ticker.upper().strip(),
        "entry_price": round(entry_price, 2),
        "quantity": quantity,
        "entry_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_stop_loss": round(stop_loss, 2),
        "status": "open",
    }
    
    portfolio["open"].append(new_pos)
    save_portfolio(chat_id, portfolio)
    return new_pos


def update_stop_loss(chat_id: int, position_id: int, new_sl: float) -> dict | None:
    """Manually update the stop-loss (exit price) of an open position."""
    portfolio = get_portfolio(chat_id)
    
    for pos in portfolio["open"]:
        if pos["id"] == position_id:
            pos["current_stop_loss"] = round(new_sl, 2)
            save_portfolio(chat_id, portfolio)
            return pos
            
    return None


def sell_stock(chat_id: int, position_id: int, exit_price: float = None, reason: str = "manual") -> dict | None:
    """Close an open position and move it to closed history."""
    portfolio = get_portfolio(chat_id)
    
    found_idx = -1
    for idx, pos in enumerate(portfolio["open"]):
        if pos["id"] == position_id:
            found_idx = idx
            break
            
    if found_idx == -1:
        return None
        
    pos = portfolio["open"].pop(found_idx)
    
    if exit_price is None:
        mkt_price = fetch_current_price(pos["ticker"])
        exit_price = mkt_price if mkt_price is not None else pos["entry_price"]
        
    pos["status"] = "closed"
    pos["exit_price"] = round(exit_price, 2)
    pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pos["exit_reason"] = reason
    
    portfolio["closed"].append(pos)
    save_portfolio(chat_id, portfolio)
    return pos


def clear_portfolio(chat_id: int):
    """Wipe out the user portfolio."""
    r.delete(_K_PORTFOLIO.format(chat_id=chat_id))


def update_portfolio_prices(chat_id: int) -> list[str]:
    """
    Update prices for open positions, check if stop-loss is hit.
    Returns list of alert messages to send to the user.
    """
    portfolio = get_portfolio(chat_id)
    if not portfolio["open"]:
        return []
        
    alerts = []
    updated_any = False
    
    # We iterate over a copy of open positions since we might remove items if SL hits
    for pos in list(portfolio["open"]):
        ticker = pos["ticker"]
        current_price = fetch_current_price(ticker)
        if current_price is None:
            continue
            
        current_price = round(current_price, 2)
        
        # Check if current price hit stop-loss
        if current_price <= pos["current_stop_loss"]:
            portfolio["open"].remove(pos)
            
            pos["status"] = "closed"
            # Assume SL exit price for conservative tracking
            pos["exit_price"] = pos["current_stop_loss"]
            pos["exit_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            pos["exit_reason"] = "sl_hit"
            
            portfolio["closed"].append(pos)
            updated_any = True
            
            # P/L calculations
            cost = pos["entry_price"] * pos["quantity"]
            revenue = pos["exit_price"] * pos["quantity"]
            pl_usd = revenue - cost
            pl_pct = (pos["exit_price"] - pos["entry_price"]) / pos["entry_price"] * 100
            
            pl_color = "🟢" if pl_usd >= 0 else "🔴"
            pl_sign = "+" if pl_usd >= 0 else ""
            
            alerts.append(
                f"🚨 <b>Stop-Loss Hit for {ticker}!</b> (ID: {pos['id']})\n"
                f"Closed at: ${pos['exit_price']:.2f}\n"
                f"Purchase: {pos['quantity']} @ ${pos['entry_price']:.2f}\n"
                f"{pl_color} P/L: {pl_sign}${pl_usd:.2f} ({pl_sign}{pl_pct:.2f}%)"
            )
            
    if updated_any:
        save_portfolio(chat_id, portfolio)
        
    return alerts


def update_all_portfolios():
    """Run price updates for all registered user portfolios and notify users of hits."""
    users = get_all_users()
    for user in users:
        chat_id = user.get("chat_id")
        if not chat_id:
            continue
        try:
            alerts = update_portfolio_prices(chat_id)
            for alert in alerts:
                send_message(chat_id, alert)
        except Exception as e:
            print(f"⚠️ Error updating portfolio for chat_id {chat_id}: {e}")
