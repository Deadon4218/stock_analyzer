"""
MA150 screener.

Scans S&P 500 + NASDAQ-100 (~516 tickers) for stocks sitting in the
0-3% band ABOVE their 150-day moving average. Categorizes each match
as a "bullish reversal" (MA150 currently rising) or a "weak cross"
(MA150 flat/falling). Sorts by category, then by volume DESC.

Pure logic — no I/O side effects. Caller is responsible for persisting
the result (run_screener.py writes to Redis + broadcasts).
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

# Filter parameters (read once; tune via env vars if you want them dynamic)
MA_PERIOD = 150
MA_SLOPE_LOOKBACK = 20   # MA150 today vs MA150 N days ago → trend
MAX_PCT_ABOVE_MA = 0.03  # 3% — top of the band
CHUNK_SIZE = 50          # yfinance bulk download in groups of N tickers
HISTORY_PERIOD = "1y"    # ~250 trading days, enough for MA150 + 20-day lookback


def load_universe() -> list[str]:
    """Load combined S&P 500 + NASDAQ-100 list from tickers.json."""
    path = Path(__file__).parent / "tickers.json"
    with path.open() as f:
        data = json.load(f)
    return data["combined"]


def _analyze_one(ticker: str, hist: pd.DataFrame) -> Optional[dict]:
    """Return a match record if the ticker passes filters, else None."""
    if hist is None or hist.empty:
        return None

    closes = hist["Close"].dropna()
    if len(closes) < MA_PERIOD + MA_SLOPE_LOOKBACK:
        return None

    current = float(closes.iloc[-1])
    ma150_today = float(closes.iloc[-MA_PERIOD:].mean())
    # MA150 as it was N days ago: same window length, shifted back N
    ma150_then = float(closes.iloc[-(MA_PERIOD + MA_SLOPE_LOOKBACK):-MA_SLOPE_LOOKBACK].mean())

    if ma150_today == 0:
        return None

    pct_above = (current - ma150_today) / ma150_today
    if not (0 <= pct_above <= MAX_PCT_ABOVE_MA):
        return None

    volumes = hist["Volume"].dropna()
    volume_today = int(volumes.iloc[-1]) if len(volumes) else 0

    return {
        "ticker": ticker,
        "price": round(current, 2),
        "ma150": round(ma150_today, 2),
        "pct_above_ma": round(pct_above * 100, 2),
        "volume": volume_today,
        "rising_ma": ma150_today > ma150_then,
        "ma150_slope_pct": round((ma150_today - ma150_then) / ma150_then * 100, 2)
                            if ma150_then else 0,
    }


def scan(tickers: list[str], progress_callback=None) -> list[dict]:
    """
    Scan a list of tickers; return matches sorted by (rising_ma DESC, volume DESC).
    Uses yfinance bulk download in chunks for speed.
    progress_callback(done, total) is called after each chunk if provided.
    """
    matches: list[dict] = []
    total = len(tickers)

    for i in range(0, total, CHUNK_SIZE):
        chunk = tickers[i : i + CHUNK_SIZE]
        try:
            data = yf.download(
                tickers=chunk,
                period=HISTORY_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            print(f"  ⚠️  Chunk {i//CHUNK_SIZE + 1} failed: {e}")
            if progress_callback:
                progress_callback(min(i + CHUNK_SIZE, total), total)
            continue

        for ticker in chunk:
            try:
                # When chunk has 1 ticker yfinance returns a flat frame;
                # multi-ticker returns column-grouped
                if len(chunk) == 1:
                    hist = data
                else:
                    if ticker not in data.columns.get_level_values(0):
                        continue
                    hist = data[ticker]
                result = _analyze_one(ticker, hist)
                if result:
                    matches.append(result)
            except Exception as e:
                print(f"  ⚠️  {ticker}: {e}")
                continue

        if progress_callback:
            progress_callback(min(i + CHUNK_SIZE, total), total)
        # Be polite — Yahoo doesn't like rapid-fire bulk requests
        time.sleep(0.5)

    # Sort: rising MA first, then volume DESC
    matches.sort(key=lambda m: (not m["rising_ma"], -m["volume"]))
    return matches


def categorize(matches: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split matches into (rising_ma, flat_or_falling_ma)."""
    rising = [m for m in matches if m["rising_ma"]]
    weak = [m for m in matches if not m["rising_ma"]]
    return rising, weak


def format_report(matches: list[dict], scan_ts: str, cap_per_section: int = 30) -> str:
    """Format the screener result for Telegram (HTML parse_mode)."""
    rising, weak = categorize(matches)

    lines = [
        "📊 <b>MA150 Screener</b>",
        f"<i>{scan_ts}</i>",
        f"Universe: S&amp;P 500 + NASDAQ-100 ({len(matches)} matches in 0-3% band above MA150)",
        "",
    ]

    if not matches:
        lines.append("No stocks currently in the band. Check again later.")
        return "\n".join(lines)

    if rising:
        shown = rising[:cap_per_section]
        lines.append(f"📈 <b>Bullish reversal — MA150 rising ({len(rising)})</b>")
        for m in shown:
            vol_m = m["volume"] / 1_000_000
            lines.append(
                f"  <b>{m['ticker']}</b> ${m['price']:.2f}  "
                f"MA: ${m['ma150']:.2f} (+{m['pct_above_ma']:.1f}%)  "
                f"Vol: {vol_m:.1f}M"
            )
        if len(rising) > cap_per_section:
            lines.append(f"  …and {len(rising) - cap_per_section} more")
        lines.append("")

    if weak:
        shown = weak[:cap_per_section]
        lines.append(f"📊 <b>Just above MA150 — MA flat/falling ({len(weak)})</b>")
        for m in shown:
            vol_m = m["volume"] / 1_000_000
            lines.append(
                f"  <b>{m['ticker']}</b> ${m['price']:.2f}  "
                f"MA: ${m['ma150']:.2f} (+{m['pct_above_ma']:.1f}%)  "
                f"Vol: {vol_m:.1f}M"
            )
        if len(weak) > cap_per_section:
            lines.append(f"  …and {len(weak) - cap_per_section} more")

    return "\n".join(lines)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
