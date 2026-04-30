"""
Daily outcome tracker — checks if past analyses hit TP or SL.
For each analysis with outcome=null:
  - Fetch yfinance daily candles since the analysis timestamp
  - If high >= TP at any point → outcome = 'tp_hit' (win for ENTRY)
  - If low <= SL at any point → outcome = 'sl_hit' (loss)
  - If both happened on the same day → assume SL hit first (conservative)
  - If 30+ days old and no hit → outcome = 'expired'
"""
import os
from datetime import datetime, timezone, timedelta
import yfinance as yf

from analyses_log import read_all, write_all

EXPIRY_DAYS = int(os.environ.get("OUTCOME_EXPIRY_DAYS", 30))


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _check_outcome(ticker: str, ts: str, tp: float, sl: float) -> tuple[str, str | None, float | None]:
    """
    Returns (outcome, hit_date_iso, return_pct).
    outcome: 'tp_hit' | 'sl_hit' | 'still_open' | 'expired' | 'no_data'
    """
    if tp is None or sl is None:
        return ("no_data", None, None)

    start = _parse_ts(ts)
    age_days = (datetime.now(timezone.utc) - start).days

    try:
        hist = yf.Ticker(ticker).history(start=start.date(), period=None, interval="1d")
    except Exception as e:
        print(f"   ⚠️  yfinance failed for {ticker}: {e}")
        return ("no_data", None, None)

    if hist.empty:
        if age_days >= EXPIRY_DAYS:
            return ("expired", None, None)
        return ("still_open", None, None)

    for date, row in hist.iterrows():
        high, low = float(row["High"]), float(row["Low"])
        sl_hit = low <= sl
        tp_hit = high >= tp
        # Conservative: if both possible same day, assume SL hit first
        if sl_hit:
            return ("sl_hit", date.strftime("%Y-%m-%d"), round((sl - row["Open"]) / row["Open"] * 100, 2))
        if tp_hit:
            return ("tp_hit", date.strftime("%Y-%m-%d"), round((tp - row["Open"]) / row["Open"] * 100, 2))

    if age_days >= EXPIRY_DAYS:
        return ("expired", None, None)
    return ("still_open", None, None)


def main():
    records = read_all()
    if not records:
        print("No analyses logged yet")
        return

    updated = 0
    for r in records:
        if r.get("outcome") is not None:
            continue

        outcome, hit_date, ret_pct = _check_outcome(
            r["ticker"], r["ts"],
            r.get("take_profit"), r.get("stop_loss"),
        )

        if outcome == "still_open":
            continue  # leave as null

        r["outcome"] = outcome
        if hit_date:
            r["outcome_date"] = hit_date
        if ret_pct is not None:
            r["return_pct"] = ret_pct
        updated += 1
        print(f"   {r['ticker']} ({r['ts'][:10]}) → {outcome}")

    if updated:
        write_all(records)
        print(f"\n✅ Updated {updated} analysis outcome(s)")
    else:
        print("No outcomes to update")


if __name__ == "__main__":
    main()
