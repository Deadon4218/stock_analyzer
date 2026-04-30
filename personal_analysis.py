"""
Personal watchlist analysis — runs 2x daily for each user.
No Discord dependency, just yfinance + agents.
"""

import time
from signal_parser import StockSignal
from stock_data import fetch_stock_data
from agents import run_all_agents
from aggregator import aggregate, calculate_price_levels, AnalysisResult
from analyses_log import log_analysis


def analyze_ticker(ticker: str, source: str = "personal") -> AnalysisResult | None:
    signal = StockSignal(ticker=ticker, direction="long")

    data = fetch_stock_data(ticker)
    if data.error:
        print(f"   ⚠️  {ticker}: {data.error}")
        return None

    price_levels = calculate_price_levels(signal, data, [])

    bull_verdicts, bear_verdicts = run_all_agents(
        signal, data, "", "No chart images available."
    )

    result = aggregate(ticker, bull_verdicts, bear_verdicts, price_levels)
    log_analysis(result, source=source)
    return result


def analyze_watchlist(tickers: list[str], delay_between: float = 5.0) -> list[AnalysisResult]:
    results = []
    for i, ticker in enumerate(tickers):
        print(f"\n📊 [{i+1}/{len(tickers)}] Analyzing {ticker}...")
        result = analyze_ticker(ticker)
        if result:
            results.append(result)

        if i < len(tickers) - 1:
            time.sleep(delay_between)

    return results
