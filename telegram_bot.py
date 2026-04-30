import os
import html
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _check_token():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")


def _esc(text: str) -> str:
    """Escape user-supplied text for Telegram HTML parse mode."""
    return html.escape(str(text), quote=False)


def send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    _check_token()
    for chunk in _split_message(text):
        try:
            resp = requests.post(
                f"{API_URL}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode, "disable_web_page_preview": True},
                timeout=10,
            )
            if not resp.ok:
                # Fallback: retry without parse mode (in case of HTML errors)
                requests.post(
                    f"{API_URL}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                    timeout=10,
                )
                print(f"⚠️  Telegram HTML failed, sent as plain: {resp.text[:200]}")
        except Exception as e:
            print(f"⚠️  Telegram send error: {e}")


def broadcast(text: str, chat_ids: list[int]):
    for chat_id in chat_ids:
        send_message(chat_id, text)


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def format_broadcast_report(results) -> str:
    """Render one Telegram message summarizing all signals from a Discord cycle."""
    if not results:
        return ""

    lines = ["<b>📡 Discord Signal Analysis</b>\n"]
    for r in results:
        emoji = "✅" if r.should_enter else "❌"
        line = (
            f"{emoji} <b>{_esc(r.ticker)}</b> | "
            f"🟢 {r.bull_probability:.1%} | "
            f"🔴 {r.bear_probability:.1%} | "
            f"Ratio: {r.probability_ratio():.2f}x"
        )
        if r.price_levels and r.price_levels.entry:
            pl = r.price_levels
            line += f"\n   Entry: ${pl.entry:.2f}"
            if pl.take_profit:
                line += f" → TP: ${pl.take_profit:.2f}"
            if pl.stop_loss:
                line += f" / SL: ${pl.stop_loss:.2f}"
            if pl.rr_ratio:
                line += f" (R:R {pl.rr_ratio:.1f})"

        if r.should_enter:
            line += f"\n   📌 <i>{_esc(r.top_bull_reason[:200])}</i>"

        lines.append(line)

    return "\n\n".join(lines)


def format_personal_report(ticker: str, result) -> str:
    emoji = "✅" if result.should_enter else "❌"
    lines = [
        f"{emoji} <b>{_esc(ticker)}</b>",
        f"🟢 Bull: {result.bull_probability:.1%} | 🔴 Bear: {result.bear_probability:.1%}",
        f"Ratio: {result.probability_ratio():.2f}x",
    ]
    if result.price_levels and result.price_levels.entry:
        pl = result.price_levels
        lines.append(f"Entry: ${pl.entry:.2f} → TP: ${pl.take_profit:.2f} / SL: ${pl.stop_loss:.2f}")
        if pl.rr_ratio:
            lines.append(f"R:R: {pl.rr_ratio:.1f}")

    if result.should_enter:
        lines.append(f"\n💡 <i>{_esc(result.top_bull_reason[:250])}</i>")
    else:
        lines.append(f"\n⚠️ <i>{_esc(result.top_bear_reason[:250])}</i>")

    return "\n".join(lines)


def format_personal_summary(user_results: list[tuple[str, object]]) -> str:
    """Render a single message with per-ticker summary for one user."""
    lines = ["<b>📊 Your Watchlist Analysis</b>\n"]
    for ticker, result in user_results:
        if result is None:
            lines.append(f"❌ <b>{_esc(ticker)}</b> — analysis failed")
            continue
        emoji = "✅" if result.should_enter else "⚠️"
        line = (
            f"{emoji} <b>{_esc(ticker)}</b> | "
            f"🟢 {result.bull_probability:.1%} | 🔴 {result.bear_probability:.1%}"
        )
        if result.price_levels and result.price_levels.entry:
            pl = result.price_levels
            line += f"\n   Entry: ${pl.entry:.2f}"
            if pl.take_profit:
                line += f" → TP: ${pl.take_profit:.2f}"
            if pl.stop_loss:
                line += f" / SL: ${pl.stop_loss:.2f}"

        reason = result.top_bull_reason if result.should_enter else result.top_bear_reason
        line += f"\n   <i>{_esc(reason[:180])}</i>"
        lines.append(line)

    return "\n\n".join(lines)
