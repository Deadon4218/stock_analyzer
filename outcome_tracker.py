"""
Daily outcome tracker — checks if past analyses hit TP or SL.
For each analysis with outcome=null:
  - Fetch yfinance daily candles since the day AFTER the analysis
  - If high >= TP at any point → outcome = 'tp_hit'
  - If low <= SL at any point → outcome = 'sl_hit'
  - If both happened on the same day → assume SL hit first (conservative)
  - If 30+ days old and no hit → outcome = 'expired'

Optimization: groups records by ticker so we make one yfinance call per
unique ticker (not per record).
"""
import os
from collections import defaultdict
from datetime import datetime, timezone
import yfinance as yf

from analyses_log import read_all, write_all

EXPIRY_DAYS = int(os.environ.get("OUTCOME_EXPIRY_DAYS", 30))


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fetch_history(ticker: str, earliest_start):
    """Fetch daily history once per ticker since earliest_start (a date)."""
    try:
        return yf.Ticker(ticker).history(start=earliest_start, period=None, interval="1d")
    except Exception as e:
        print(f"   ⚠️  yfinance failed for {ticker}: {e}")
        return None


def _check_outcome(record: dict, hist) -> tuple[str, str | None, float | None]:
    """
    Returns (outcome, hit_date_iso, return_pct).
    Looks at price action AFTER the analysis day (not including it).
    """
    tp = record.get("take_profit")
    sl = record.get("stop_loss")
    ts = record["ts"]
    start = _parse_ts(ts)
    age_days = (datetime.now(timezone.utc) - start).days

    if tp is None or sl is None:
        return ("no_data", None, None)

    if hist is None or hist.empty:
        return ("expired", None, None) if age_days >= EXPIRY_DAYS else ("still_open", None, None)

    analysis_date = start.date()

    for date, row in hist.iterrows():
        # Skip data on or before the analysis day
        if date.date() <= analysis_date:
            continue

        high, low = float(row["High"]), float(row["Low"])
        # Conservative: if both possible same day, assume SL hit first (prevents fake wins)
        if low <= sl:
            return ("sl_hit", date.strftime("%Y-%m-%d"),
                    round((sl - row["Open"]) / row["Open"] * 100, 2))
        if high >= tp:
            return ("tp_hit", date.strftime("%Y-%m-%d"),
                    round((tp - row["Open"]) / row["Open"] * 100, 2))

    return ("expired", None, None) if age_days >= EXPIRY_DAYS else ("still_open", None, None)


def main():
    records = read_all()
    if not records:
        print("No analyses logged yet")
        return

    # Group OPEN records by ticker so we fetch each ticker's history once.
    open_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("outcome") in (None, "still_open"):
            open_by_ticker[r["ticker"]].append(r)

    if not open_by_ticker:
        print("No open analyses to check")
        return

    print(f"🔍 Checking {sum(len(v) for v in open_by_ticker.values())} open analyses across {len(open_by_ticker)} ticker(s)")

    updated = 0
    for ticker, ticker_records in open_by_ticker.items():
        # Earliest analysis for this ticker — fetch from there
        earliest_start = min(_parse_ts(r["ts"]).date() for r in ticker_records)
        hist = _fetch_history(ticker, earliest_start)

        for r in ticker_records:
            outcome, hit_date, ret_pct = _check_outcome(r, hist)
            if outcome == "still_open":
                continue
            r["outcome"] = outcome
            if hit_date:
                r["outcome_date"] = hit_date
            if ret_pct is not None:
                r["return_pct"] = ret_pct
            updated += 1
            print(f"   {ticker} ({r['ts'][:10]}) → {outcome}")

    if updated:
        write_all(records)
        print(f"\n✅ Updated {updated} analysis outcome(s)")
    else:
        print("No outcomes to update")


if __name__ == "__main__":
    main()
